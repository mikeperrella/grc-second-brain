"""Verification: a pair is shown only if both quotes are real (ADR-019 decision 8, F33-3).

Every check is code. A pair that fails any of them is dropped and counted by reason, never shown:

* both claims' chunks are among the chunks the pair was generated from (company policy only);
* each quote is found verbatim, whitespace being the only leniency, in the raw text of its chunk
  (`questionnaire/grounding.py`: the same rule as an M5 answer's quote);
* each quote meets ADR-013's character and token floor, and is not a Markdown heading (the heading
  rule is this detector's own: `long_enough` checks characters and tokens only);
* every number shown is one the code finds again in that quote;
* two claims in the same chunk must sit on different lines.

"Every reason that applies is recorded", as in M5.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from grc_sidecar.contradictions.models import ChunkText, Claim, DropReason, Pair, PairVerdict
from grc_sidecar.contradictions.numbers import find_numbers
from grc_sidecar.questionnaire.grounding import locate_all_quotes, long_enough


@dataclass(frozen=True)
class Verification:
    reasons: tuple[DropReason, ...]  # empty means verified
    lines_a: tuple[int, int] | None
    lines_b: tuple[int, int] | None

    @property
    def ok(self) -> bool:
        return not self.reasons


def _verify_claim(
    claim: Claim, texts: Mapping[str, ChunkText], context_ids: frozenset[str]
) -> tuple[set[DropReason], tuple[int, int] | None]:
    reasons: set[DropReason] = set()
    if claim.chunk_id not in context_ids:
        reasons.add("chunk_not_in_context")
    chunk = texts.get(claim.chunk_id)
    if chunk is None:
        reasons.add("quote_not_verified")
        return reasons, None
    found = locate_all_quotes(chunk.text, claim.quote, chunk.start_line)
    if not found:
        reasons.add("quote_not_verified")
        return reasons, None
    # A quote that occurs more than once is cited where the claim itself sits, else at the first.
    own = (claim.start_line, claim.end_line)
    place = next((p for p in found if (p.start_line, p.end_line) == own), found[0])
    if not long_enough(claim.quote):
        reasons.add("quote_too_short")
    if place.heading_only:
        reasons.add("quote_is_heading")
    if claim.number not in [number for number, _, _ in find_numbers(claim.quote)]:
        reasons.add("number_not_in_quote")
    return reasons, (place.start_line, place.end_line)


def model_quote_problem(verdict: PairVerdict, text_a: str, text_b: str) -> str | None:
    """A fixed sentence saying why the model's quotes are not usable, or None if they are.

    A quote the model returns is re-verified like any other (ADR-019 decision 10): every non-empty
    one must occur verbatim, whitespace being the only leniency, in the text of the source it names
    (`quote_a` in S1, `quote_b` in S2). A conflict needs both quotes: it cannot be kept on a
    passage the model did not copy. An empty quote is allowed only with `conflict` false. The
    sentence never repeats the model's own text, so it is safe to show the model on attempt 2.
    """
    for field, source_id, quote, text in (
        ("quote_a", "S1", verdict.quote_a, text_a),
        ("quote_b", "S2", verdict.quote_b, text_b),
    ):
        if not quote.strip():
            if verdict.conflict:
                return f"{field} is empty, but a conflict needs a passage copied from {source_id}"
            continue
        if not locate_all_quotes(text, quote):
            return f"{field} is not copied exactly from {source_id}"
    return None


def verify_pair(
    pair: Pair, texts: Mapping[str, ChunkText], context_ids: frozenset[str]
) -> Verification:
    """Check both quotes of `pair`. `texts` is every chunk the detector could read; `context_ids`
    is the set the pair was generated from. A pair is verified only if `reasons` is empty."""
    reasons_a, lines_a = _verify_claim(pair.a, texts, context_ids)
    reasons_b, lines_b = _verify_claim(pair.b, texts, context_ids)
    reasons = reasons_a | reasons_b
    same_chunk = pair.a.chunk_id == pair.b.chunk_id
    if same_chunk and lines_a is not None and lines_b is not None and lines_a[0] == lines_b[0]:
        reasons.add("same_line_pair")
    order: tuple[DropReason, ...] = (
        "chunk_not_in_context",
        "quote_not_verified",
        "quote_too_short",
        "quote_is_heading",
        "number_not_in_quote",
        "same_line_pair",
    )
    return Verification(
        reasons=tuple(reason for reason in order if reason in reasons),
        lines_a=lines_a,
        lines_b=lines_b,
    )
