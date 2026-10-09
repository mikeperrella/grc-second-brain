"""The authored SOC 2 and ISO 27001 ID packs (M3e, ADR-008).

These hold control IDs only, plus our own short summaries for exactly the controls the sample vault
maps to. The list of expected summaries is read from the sample vault, not typed here.
"""

import re
from pathlib import Path

import pytest

from grc_sidecar.packs.loader import verify_pack

ROOT = Path(__file__).resolve().parents[2]
PACKS = ROOT / "packs"
VAULT = ROOT / "vault-sample" / "vault"
ENTRY = re.compile(r"^- \*\*(?P<id>[A-Z0-9.]+)\*\*(?P<rest>.*)$", re.MULTILINE)
SOC2_ID = re.compile(r"^(CC\d\.\d|A1\.\d|C1\.\d|PI1\.\d|P\d\.\d)$")
ISO_ID = re.compile(r"^A\.[5-8]\.\d{1,2}$")

SUMMARY_MAX_CHARS = 300
SUMMARY_MAX_SENTENCES = 2
SPECS = {
    "soc2-tsc-ids": ("SOC2", SOC2_ID, 61),
    "iso27001-annex-a-ids": ("ISO27001", ISO_ID, 93),
}


def entries(pack_id: str) -> dict[str, str]:
    """control ID -> the text after the ID on its line (empty for an ID-only entry)."""
    found: dict[str, str] = {}
    for path in sorted((PACKS / pack_id).glob("*.md")):
        for match in ENTRY.finditer(path.read_text(encoding="utf-8")):
            assert match["id"] not in found  # unique IDs
            found[match["id"]] = match["rest"].strip()
    return found


def vault_mapped(prefix: str) -> set[str]:
    mapped: set[str] = set()
    for path in VAULT.glob("*.md"):
        mapped |= set(re.findall(rf'"{prefix}:([^"]+)"', path.read_text(encoding="utf-8")))
    return mapped


@pytest.mark.parametrize("pack_id", sorted(SPECS))
def test_pack_verifies_and_is_marked_authored_and_unverified(pack_id: str) -> None:
    metadata = verify_pack(PACKS / pack_id)
    assert metadata.origin == "original_authored"
    assert metadata.content_kind == "ids_and_original_summaries"
    assert metadata.id_list_verified_against == "unverified"
    assert "not bundled" in metadata.upstream_license


@pytest.mark.parametrize("pack_id", sorted(SPECS))
def test_every_control_id_is_listed_once_with_a_valid_shape(pack_id: str) -> None:
    _, shape, expected_count = SPECS[pack_id]
    found = entries(pack_id)
    assert len(found) == expected_count
    assert all(shape.fullmatch(identifier) for identifier in found)


@pytest.mark.parametrize("pack_id", sorted(SPECS))
def test_id_only_entries_carry_no_summary(pack_id: str) -> None:
    prefix, _, _ = SPECS[pack_id]
    with_text = {identifier for identifier, rest in entries(pack_id).items() if rest}
    # Text is allowed on exactly the controls the sample vault maps to, and nowhere else.
    assert with_text == vault_mapped(prefix)


@pytest.mark.parametrize("pack_id", sorted(SPECS))
def test_summaries_are_short(pack_id: str) -> None:
    for identifier, rest in entries(pack_id).items():
        if not rest:
            continue
        assert len(rest) <= SUMMARY_MAX_CHARS, identifier
        # One sentence for our title, then at most SUMMARY_MAX_SENTENCES for the summary.
        assert len(re.findall(r"[.!?](?:\s|$)", rest)) <= SUMMARY_MAX_SENTENCES + 1, identifier


@pytest.mark.parametrize("pack_id", sorted(SPECS))
def test_every_vault_mapping_exists_in_the_id_list(pack_id: str) -> None:
    prefix, _, _ = SPECS[pack_id]
    assert vault_mapped(prefix) <= set(entries(pack_id))
