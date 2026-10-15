"""Claim extraction (ADR-019 decision 4): table rows, sentences, lines and subjects. No model."""

from fractions import Fraction

from grc_sidecar.contradictions.claims import content_words, extract_claims, stem, subject_of

from .contradictionkit import claims_of, text_chunk
from .vaultkit import DEMO_VAULT


def test_a_table_row_is_a_claim_with_its_first_cell_as_the_subject() -> None:  # hard test
    table = "\n".join(
        [
            "| Data | Retention |",
            "|---|---|",
            "| Application logs | 14 days |",
            "| Security logs | 90 days |",
        ]
    )
    claims = claims_of(table, start_line=20)
    assert [(c.start_line, c.quote, c.subject) for c in claims] == [
        (22, "| Application logs | 14 days |", "Application logs"),
        (23, "| Security logs | 90 days |", "Security logs"),
    ]
    last = claims[-1]
    assert (last.number.kind, last.number.value) == ("days", Fraction(90))
    assert last.subject_words == frozenset({"security", "log"})
    assert all(c.start_line == c.end_line for c in claims)


def test_the_demo_vaults_security_log_row_is_read_at_its_line() -> None:
    path = DEMO_VAULT / "data-retention-policy.md"
    lines = path.read_text(encoding="utf-8").split("\n")
    claims = extract_claims(text_chunk("\n".join(lines), path="data-retention-policy.md"))
    row = [c for c in claims if c.quote == "| Security logs | 90 days |"]
    assert [c.start_line for c in row] == [23]


def test_a_table_header_and_separator_row_make_no_claim() -> None:
    assert claims_of("| Log type | Retention |\n|:---|---:|\n") == []


def test_every_value_cell_of_a_row_is_read_with_the_same_subject() -> None:
    claims = claims_of("| Tickets | 24 months | 3 years |")
    assert [c.number.text for c in claims] == ["24 months", "3 years"]
    assert {c.subject for c in claims} == {"Tickets"}


def test_a_sentence_spanning_two_lines_cites_both_lines() -> None:
    text = "Intro line.\n\nSecurity logs are kept\nfor 365 days after the event.\n"
    (claim,) = claims_of(text, start_line=10)
    assert (claim.start_line, claim.end_line) == (12, 13)
    assert claim.quote == "Security logs are kept for 365 days after the event."


def test_two_sentences_on_one_line_are_read_separately() -> None:
    claims = claims_of("Backups are kept for 35 days. Logs are kept for 90 days.")
    assert [(c.quote, c.number.text) for c in claims] == [
        ("Backups are kept for 35 days.", "35 days"),
        ("Logs are kept for 90 days.", "90 days"),
    ]


def test_a_list_item_loses_its_marker_and_keeps_its_line() -> None:
    text = "1. Contain the incident.\n2. Hold a review within ten business days and record it."
    (claim,) = claims_of(text, start_line=20)
    assert claim.quote == "Hold a review within ten business days and record it."
    assert claim.start_line == 21
    assert claim.number.kind == "business_days"


def test_text_inside_a_code_fence_is_not_read() -> None:
    assert claims_of("```\nretention = 90 days\n```\n") == []


def test_a_decimal_point_does_not_end_a_sentence() -> None:
    (claim,) = claims_of("Availability is targeted at 99.9% each month.")
    assert claim.number.text == "99.9%"


def test_a_heading_with_a_number_is_read_so_that_verification_can_drop_it() -> None:
    (claim,) = claims_of("## Keep logs 90 days")
    assert claim.quote == "Keep logs 90 days"


def test_subjects_are_the_words_before_the_verb() -> None:
    assert subject_of("Backups are encrypted and kept for 35 days.", 38)[1] == frozenset({"backup"})
    assert subject_of("Database backups are overwritten after 30 days.", 38)[1] == frozenset(
        {"database", "backup"}
    )


def test_the_subject_is_found_after_a_leading_clause() -> None:
    sentence = (
        "So that late-discovered incidents can still be investigated, "
        "security logs are kept for 365 days."
    )
    assert subject_of(sentence, sentence.index("365"))[1] == frozenset({"security", "log"})
    text = "Within 90 days, security logs are deleted."
    assert subject_of(text, text.index("90"))[1] == frozenset({"security", "log"})


def test_content_words_drop_verbs_numbers_units_and_stop_words() -> None:
    assert content_words("The Security logs are kept for ten days") == frozenset(
        {"security", "log"}
    )


def test_a_light_stem_joins_singular_and_plural() -> None:
    assert stem("logs") == stem("log") == "log"
    assert stem("policies") == "policy"
    assert stem("access") == "access"


def test_a_note_with_no_numbers_makes_no_claim() -> None:
    assert claims_of("Access is reviewed regularly by the owner.") == []
