"""Verification (ADR-019 decision 8, F33-3): a pair is shown only if both quotes are real.

Every drop is counted by reason, and every reason that applies is recorded. Pairs that cannot arise
from the extractor (a tampered quote, a chunk outside the context) are built by hand and sent
straight to `verify_pair`.
"""

from grc_sidecar.contradictions.models import ChunkText, Claim, DetectionReport, Pair
from grc_sidecar.contradictions.pairs import generate_pairs, pair_id_of
from grc_sidecar.contradictions.service import build_report
from grc_sidecar.contradictions.verify import verify_pair

from .contradictionkit import claims_of, text_chunk

A = text_chunk("Intro.\n\nSecurity logs are kept for 90 days.\n", chunk_id="a", path="a.md")
B = text_chunk("Intro.\n\nSecurity logs are kept for 365 days.\n", chunk_id="b", path="b.md")
TEXTS = {"a": A, "b": B}
CONTEXT = frozenset({"a", "b"})


def claim_in(chunk: ChunkText) -> Claim:
    (found,) = claims_of(chunk.text, chunk_id=chunk.chunk_id, start_line=chunk.start_line)
    return found.model_copy(update={"source_path": chunk.source_path})


def pair_of(a: Claim, b: Claim) -> Pair:
    return Pair(pair_id=pair_id_of(a, b), a=a, b=b, overlap="strong")


def report_of(*chunks: ChunkText) -> DetectionReport:
    texts = {c.chunk_id: c for c in chunks}
    claims = [x for c in chunks for x in claims_of(c.text, chunk_id=c.chunk_id)]
    everywhere = {x.claim_id: frozenset(texts) for x in claims}
    return build_report(texts, claims, everywhere, cap=100)


def test_a_real_pair_is_verified_with_its_exact_lines() -> None:
    check = verify_pair(pair_of(claim_in(A), claim_in(B)), TEXTS, CONTEXT)
    assert check.ok
    assert check.lines_a == check.lines_b == (3, 3)


def test_a_repeated_sentence_is_cited_where_the_claim_sits_else_at_the_first() -> None:
    repeated = text_chunk(
        "Security logs are kept for 90 days.\n\nOther text.\n\nSecurity logs are kept for 90 days.",
        chunk_id="r",
        path="r.md",
    )
    first, second = claims_of(repeated.text, chunk_id="r")
    texts = {**TEXTS, "r": repeated}
    context = CONTEXT | {"r"}
    assert verify_pair(pair_of(first, claim_in(B)), texts, context).lines_a == (1, 1)
    assert verify_pair(pair_of(second, claim_in(B)), texts, context).lines_a == (5, 5)
    # A claim whose own lines match no occurrence falls back to the first occurrence.
    moved = second.model_copy(update={"start_line": 9, "end_line": 9})
    assert verify_pair(pair_of(moved, claim_in(B)), texts, context).lines_a == (1, 1)


def test_a_quote_in_another_letter_case_is_not_verified() -> None:  # [NEG] verbatim means verbatim
    a = claim_in(A)
    shouted = a.model_copy(update={"quote": a.quote.upper()})
    assert verify_pair(pair_of(shouted, claim_in(B)), TEXTS, CONTEXT).reasons == (
        "quote_not_verified",
    )


def test_a_quote_that_is_not_in_the_chunk_is_not_verified() -> None:  # [NEG] an invented quote
    a = claim_in(A)
    invented = a.model_copy(update={"quote": "Security logs are kept for 90 days, always."})
    assert (
        "quote_not_verified" in verify_pair(pair_of(invented, claim_in(B)), TEXTS, CONTEXT).reasons
    )


def test_whitespace_is_the_only_leniency() -> None:
    chunk = text_chunk("Security logs are\n  kept   for 90 days.", chunk_id="a")
    a = claim_in(chunk)
    assert verify_pair(pair_of(a, claim_in(B)), {"a": chunk, "b": B}, CONTEXT).ok


def test_a_chunk_outside_the_context_is_refused_even_if_its_quote_is_real() -> None:  # [NEG]
    # The framework chunk's quote is verbatim in its own text; only the context check stops it.
    framework = text_chunk("Security logs are kept for 30 days.", chunk_id="fw", path="fw.md")
    texts = {**TEXTS, "fw": framework}
    check = verify_pair(pair_of(claim_in(A), claim_in(framework)), texts, CONTEXT)
    assert check.reasons == ("chunk_not_in_context",)


def test_a_number_the_code_cannot_find_in_the_quote_is_refused() -> None:  # [NEG]
    a = claim_in(A)
    tampered = a.model_copy(update={"number": a.number.model_copy(update={"text": "91 days"})})
    assert verify_pair(pair_of(tampered, claim_in(B)), TEXTS, CONTEXT).reasons == (
        "number_not_in_quote",
    )


def test_a_quote_below_the_length_floor_is_refused() -> None:
    short = text_chunk("| Logs | 90 days |", chunk_id="s")  # 18 characters
    check = verify_pair(
        pair_of(claim_in(short), claim_in(B)), {**TEXTS, "s": short}, CONTEXT | {"s"}
    )
    assert check.reasons == ("quote_too_short",)


def test_the_demo_table_row_clears_the_floor_and_is_not_a_heading() -> None:
    row = text_chunk("| Security logs | 90 days |", chunk_id="r")  # 27 characters, 7 tokens
    check = verify_pair(pair_of(claim_in(row), claim_in(B)), {**TEXTS, "r": row}, CONTEXT | {"r"})
    assert check.ok


def test_a_heading_is_dropped_and_counted_but_a_table_row_is_not() -> None:  # [NEG]
    heading = text_chunk("## Security logs kept for 90 days\n", chunk_id="h", path="h.md")
    report = report_of(heading, B)
    assert report.findings == ()
    assert report.dropped == {"quote_is_heading": 1}
    row = text_chunk("| Security logs | 90 days |\n", chunk_id="r", path="r.md")
    assert len(report_of(row, B).findings) == 1


def test_a_heading_quote_fails_the_floor_with_its_own_reason() -> None:
    heading = text_chunk("### Security logs retention policy\n", chunk_id="h")
    a = claim_in(A).model_copy(update={"chunk_id": "h", "quote": "Security logs retention policy"})
    check = verify_pair(pair_of(a, claim_in(B)), {**TEXTS, "h": heading}, CONTEXT | {"h"})
    assert "quote_is_heading" in check.reasons
    assert "quote_too_short" not in check.reasons  # it passes ADR-013's characters and tokens


def test_two_claims_on_one_line_of_one_chunk_are_refused() -> None:
    chunk = text_chunk("Logs are kept 90 days and backups 35 days.", chunk_id="m")
    claims = claims_of(chunk.text, chunk_id="m")
    (pair,) = generate_pairs(claims, {c.claim_id: frozenset({"m"}) for c in claims})
    assert verify_pair(pair, {"m": chunk}, frozenset({"m"})).reasons == ("same_line_pair",)


def test_two_claims_on_different_lines_of_one_chunk_are_allowed() -> None:
    chunk = text_chunk(
        "Customer tickets are archived after 12 months.\n\n"
        "Customer tickets are archived after 24 months.",
        chunk_id="m",
    )
    assert len(report_of(chunk).findings) == 1


def test_every_reason_that_applies_is_recorded() -> None:
    a = claim_in(A).model_copy(update={"quote": "gone"})
    check = verify_pair(pair_of(a, claim_in(B)), TEXTS, frozenset({"b"}))
    assert check.reasons == ("chunk_not_in_context", "quote_not_verified")


def test_dropped_pairs_are_counted_by_reason_and_never_shown() -> None:
    same_line = text_chunk("Logs are kept 90 days and backups 35 days.", chunk_id="m", path="m.md")
    report = report_of(same_line)
    assert report.dropped == {"same_line_pair": 1}
    assert report.findings == ()
    assert report.pairs_generated == 1
