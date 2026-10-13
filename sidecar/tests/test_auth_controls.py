"""The authentication-controls list (ADR-016 decision 13): its content, its presence in the packs,
and its refusal of a malformed file. Offline; the packs are only read."""

import json
import re
from pathlib import Path

import pytest

from grc_sidecar.errors import AuthControlsError
from grc_sidecar.reality.auth_controls import (
    MAX_BYTES,
    AuthControl,
    auth_control_ids,
    load_auth_controls,
    parse_auth_controls,
)

PACKS = Path(__file__).resolve().parents[2] / "packs"

EXPECTED_IDS = ("IA-2", "IA-2(1)", "IA-2(2)", "03.05.03", "PR.AA-03", "CC6.1", "A.8.5")


def present_as_heading_or_bold_item(text: str, control_id: str) -> bool:
    """True if a line is a Markdown heading that starts with the ID, or a `- **ID**` list item.

    The ID must be followed by a space, the closing `**` or the end of the line, so `IA-2` is not
    found in the heading of `IA-2(1)` and a mention in prose does not count.
    """
    escaped = re.escape(control_id)
    heading = re.compile(rf"^#{{1,6}}\s+{escaped}(?:\s|$)", re.MULTILINE)
    bold_item = re.compile(rf"^- \*\*{escaped}\*\*", re.MULTILINE)
    return heading.search(text) is not None or bold_item.search(text) is not None


def anywhere_in_packs(control_id: str) -> bool:
    return any(
        present_as_heading_or_bold_item(path.read_text(encoding="utf-8"), control_id)
        for path in sorted(PACKS.rglob("*.md"))
    )


def test_the_file_loads_and_holds_exactly_the_seven_ids() -> None:
    assert auth_control_ids() == EXPECTED_IDS
    assert len(load_auth_controls()) == 7


@pytest.mark.parametrize("control", load_auth_controls(), ids=lambda control: control.id)
def test_every_id_is_a_heading_or_bold_item_in_its_cited_pack_file(control: AuthControl) -> None:
    path = PACKS / control.pack / control.file
    assert path.is_file()
    assert present_as_heading_or_bold_item(path.read_text(encoding="utf-8"), control.id)


@pytest.mark.parametrize(
    "control_id", ["IA-99", "IA-2(99)", "XX-1", "03.05.99", "PR.ZZ-01", "CC99.9"]
)
def test_the_presence_check_fails_for_an_id_in_no_pack(control_id: str) -> None:
    assert not anywhere_in_packs(control_id)


@pytest.mark.parametrize(
    ("text", "control_id"),
    [
        ("### IA-2(1) Multi-factor Authentication\n", "IA-2"),  # a child is not its parent
        ("See IA-2 for details.\n", "IA-2"),  # prose is not a heading
        ("- **CC6.10** Something\n", "CC6.1"),  # a longer ID is not this one
        ("-   **CC6.1** spaced oddly\n", "CC6.1"),  # not the pack's list-item form
    ],
)
def test_the_presence_check_is_not_loose(text: str, control_id: str) -> None:
    assert not present_as_heading_or_bold_item(text, control_id)


@pytest.mark.parametrize(
    ("text", "control_id"),
    [
        ("## IA-2 Identification\n", "IA-2"),
        ("### IA-2(1) Multi-factor\n", "IA-2(1)"),
        ("- **A.8.5** Sign-in controls.\n", "A.8.5"),
    ],
)
def test_the_presence_check_accepts_the_two_forms(text: str, control_id: str) -> None:
    assert present_as_heading_or_bold_item(text, control_id)


def _entry(control_id: str = "IA-2") -> dict[str, str]:
    return {"id": control_id, "framework": "Synthetic", "pack": "p", "file": "f.md"}


def _file(**changes: object) -> bytes:
    data: dict[str, object] = {"schema_version": 1, "controls": [_entry()]}
    data.update(changes)
    return json.dumps(data).encode()


def test_a_valid_file_parses() -> None:
    assert [control.id for control in parse_auth_controls(_file())] == ["IA-2"]


MALFORMED: dict[str, bytes] = {
    "empty": b"",
    "not-json": b"not json",
    "not-utf8": b"\xff\xfe\x00",
    "list-not-object": b"[]",
    "empty-object": b"{}",
    "wrong-version": _file(schema_version=2),
    "no-controls": _file(controls=[]),
    "duplicate-id": _file(controls=[_entry(), _entry()]),
    "not-an-identifier": _file(controls=[_entry("not an identifier")]),
    "word-not-identifier": _file(controls=[_entry("TODO")]),
    "extra-key-in-entry": _file(controls=[{**_entry(), "extra": "x"}]),
    "missing-key": _file(controls=[{"id": "IA-2", "framework": "x", "pack": "p"}]),
    "id-wrong-type": _file(controls=[{**_entry(), "id": 7}]),
    "extra-key-at-top": _file(extra=1),
    "too-big": b" " * (MAX_BYTES + 1),
}


@pytest.mark.parametrize("raw", MALFORMED.values(), ids=MALFORMED.keys())
def test_a_malformed_file_is_a_typed_refusal(raw: bytes) -> None:
    with pytest.raises(AuthControlsError) as raised:
        parse_auth_controls(raw)
    assert str(raised.value) == "Authentication controls list is invalid"
    assert raised.value.__cause__ is None
