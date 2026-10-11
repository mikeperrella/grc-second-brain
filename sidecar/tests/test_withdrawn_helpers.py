"""Withdrawn-section detection, stub IDs and identifier ordering (ADR-010, M4f). Pure functions."""

import pytest

from grc_sidecar.retrieval.ordering import heading_names_identifier
from grc_sidecar.retrieval.withdrawn import is_withdrawn_section, names_a_stub, stub_ids

SECTION = (
    "## Withdrawn controls\n\n"
    "These controls are withdrawn. They are listed so old references still resolve.\n\n"
    "- **03.01.13**: Withdrawn. Addressed by: 03.13.08.\n"
    "- **03.01.14**: Withdrawn. Incorporated into: 03.01.12.\n"
)


@pytest.mark.parametrize(
    ("heading", "expected"),
    [
        ("Access Control (03.01) > Withdrawn controls", True),
        ("Withdrawn controls", True),
        ("Access Control (03.01) > 03.01.01 Account Management", False),
        ("Access Control (03.01) > Withdrawn controls revisited", False),
        ("Policy > Withdrawn controls > Details", False),  # only the LAST segment counts
        ("", False),
    ],
)
def test_the_withdrawn_section_is_recognised_by_its_last_heading(
    heading: str, expected: bool
) -> None:
    assert is_withdrawn_section(heading) is expected


def test_stub_ids_are_read_from_the_stub_lines_only() -> None:
    assert stub_ids(SECTION) == ("03.01.13", "03.01.14")
    assert stub_ids("- **03.01.01** Account Management is required.") == ()
    assert stub_ids("A stub looks like - **03.01.13**: Withdrawn. in prose") == ()


@pytest.mark.parametrize(
    ("forms", "expected"),
    [
        ((("3.1.13", "03.01.13"),), True),
        ((("03.01.14",),), True),
        ((("03.13.08",),), False),  # named as the target of a stub, not a stub itself
        ((("03.01.99",),), False),
        ((("ac-2",),), False),
        ((), False),
    ],
)
def test_a_stub_is_named_only_by_its_own_id(
    forms: tuple[tuple[str, ...], ...], expected: bool
) -> None:
    assert names_a_stub(SECTION, forms) is expected


def test_stub_matching_ignores_case() -> None:
    text = "- **AC-2(1)**: Withdrawn. Incorporated into: AC-2.\n"
    assert names_a_stub(text, (("ac-2(1)",),))


@pytest.mark.parametrize(
    ("heading", "forms", "expected"),
    [
        ("Access Control (AC) > AC-2 Account Management", {"ac-2", "ac-02"}, True),
        ("Access Control (AC) > AC-2(4) Automated Audit Actions", {"ac-2", "ac-02"}, False),
        ("Access Control (AC) > AC-20 Use of External Systems", {"ac-2", "ac-02"}, False),
        ("Access Control (AC) > AC-2", {"ac-2"}, True),
        ("Access Control (03.01) > 03.01.01 Account Management", {"3.1.1", "03.01.01"}, True),
        ("Access Control (03.01) > 03.01.10 Device Lock", {"3.1.1", "03.01.01"}, False),
        ("Protect (PR) > PR.AA-01", {"pr.aa-01", "pr.aa-1"}, True),
        ("Protect (PR) > PR.AA-010", {"pr.aa-01"}, False),
        ("", {"ac-2"}, False),
    ],
)
def test_a_heading_names_an_identifier_only_as_a_whole_word(
    heading: str, forms: set[str], expected: bool
) -> None:
    assert heading_names_identifier(heading, forms) is expected
