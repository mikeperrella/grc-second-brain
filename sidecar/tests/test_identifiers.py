"""The identifier table (ADR-010, M4d): one test row per rule, format normalization only."""

import pytest

from grc_sidecar.retrieval.identifiers import IDENTIFIER_RULES, identify

# rule name -> (token, expected forms) rows that must match; NEGATIVE: tokens that must NOT match.
POSITIVE: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    "nist-800-171-r3": [
        ("3.1.1", ("3.1.1", "03.01.01")),
        ("03.01.01", ("3.1.1", "03.01.01")),
        ("3.1.12", ("3.1.12", "03.01.12")),
        ("03.13.08", ("3.13.8", "03.13.08")),
    ],
    "iso-27001-annex-a": [
        ("5.15", ("5.15", "A.5.15")),
        ("A.5.15", ("5.15", "A.5.15")),
        ("a.5.15", ("5.15", "A.5.15")),
        ("8.10", ("8.10", "A.8.10")),
    ],
    "nist-800-53": [
        ("AC-2", ("AC-2", "AC-02")),
        ("ac-2", ("AC-2", "AC-02")),
        ("AC-02", ("AC-2", "AC-02")),
        ("AC-2(1)", ("AC-2(1)", "AC-02(1)")),
        ("AC-02(01)", ("AC-2(1)", "AC-02(1)")),
        ("SC-13", ("SC-13",)),  # two digits already: padded and plain are the same text
        ("IA-5(1)", ("IA-5(1)", "IA-05(1)")),
    ],
    "nist-csf-2.0": [
        ("PR.AA-01", ("PR.AA-01", "PR.AA-1")),
        ("PR.AA-1", ("PR.AA-01", "PR.AA-1")),
        ("pr.aa-01", ("PR.AA-01", "PR.AA-1")),
        ("PR.AA", ("PR.AA",)),
        ("GV.OC-05", ("GV.OC-05", "GV.OC-5")),
    ],
    "soc2-tsc": [
        ("CC6.1", ("CC6.1",)),
        ("cc6.1", ("CC6.1",)),
        ("A1.2", ("A1.2",)),
        ("PI1.3", ("PI1.3",)),
        ("P1.1", ("P1.1",)),
        ("C1.1", ("C1.1",)),
        ("SOC2:CC6.1", ("CC6.1", "SOC2:CC6.1")),
    ],
    "generic-hyphenated": [
        ("HR-POL-005", ("HR-POL-005",)),
        ("TLS-1", ("TLS-1",)),
    ],
}

# Tokens that look close to a rule but are not identifiers of THAT rule (they may be another's).
NEGATIVE: dict[str, list[str]] = {
    "nist-800-171-r3": ["3.1", "3.1.1.1", "123.1.1", "a.b.c", "3..1"],
    "iso-27001-annex-a": ["5", "5.", "1.2.3", "A.5", "A5.15"],
    "nist-800-53": ["XX-2", "AC-", "AC-2()", "AC-123", "AC2"],
    "nist-csf-2.0": ["PR", "ID", "PR.A", "XX.AA-01", "PR.AAA"],
    "soc2-tsc": ["CC6", "CC.6.1", "XX6.1", "CC6.1.2"],
    "generic-hyphenated": ["multi-factor", "well-known", "-", "a--b", "HR_POL_005"],
}


def test_every_rule_has_positive_and_negative_rows() -> None:
    names = {rule.name for rule in IDENTIFIER_RULES}
    assert set(POSITIVE) == names
    assert set(NEGATIVE) == names
    assert all(POSITIVE[name] and NEGATIVE[name] for name in names)


@pytest.mark.parametrize(
    ("rule", "token", "forms"),
    [(rule, token, forms) for rule, rows in POSITIVE.items() for token, forms in rows],
)
def test_a_token_gets_every_written_form(rule: str, token: str, forms: tuple[str, ...]) -> None:
    found = identify(token)
    assert found is not None
    assert found.rule == rule
    assert found.forms == forms


@pytest.mark.parametrize(
    ("rule", "token"),
    [(rule, token) for rule, tokens in NEGATIVE.items() for token in tokens],
)
def test_a_near_miss_is_not_that_rules_identifier(rule: str, token: str) -> None:  # [NEG]
    found = identify(token)
    assert found is None or found.rule != rule


def test_a_plain_word_is_not_an_identifier() -> None:  # [NEG]
    for word in ["authentication", "multi-factor", "policy", "of", "", "2026"]:
        assert identify(word) is None, word


def test_the_forms_never_claim_that_two_revisions_mean_the_same_thing() -> None:
    # Format only: 3.1.1 and 03.01.01 are two ways of writing one ID. Nothing here knows about
    # Rev 2 or Rev 3, so no form can carry a revision.
    forms = {form for rows in POSITIVE.values() for _token, fs in rows for form in fs}
    assert not any("rev" in form.lower() for form in forms)


def test_forms_are_deterministic_and_distinct() -> None:
    for rows in POSITIVE.values():
        for token, _expected in rows:
            first, second = identify(token), identify(token)
            assert first == second
            assert first is not None
            assert len(set(first.forms)) == len(first.forms)


def test_case_does_not_change_the_forms() -> None:
    assert identify("AC-2(1)") == identify("ac-2(1)")
    assert identify("CC6.1") == identify("cc6.1")
    assert identify("pr.aa-01") == identify("PR.AA-01")
