"""The status of one answer, computed from facts (CLAUDE.md 8.4, ADR-013).

**The rule.** The model's reply is untrusted. Everything that can GRANT a status comes from facts
the code checks: the quote is real and in the cited chunk, the chunk was in the context, it is
company policy, its review is current, retrieval was complete, and the cited file is unchanged. The
model's own words (its confidence, its coverage, a conflict it names, a "no evidence" verdict) can
only ADD a reason, so they can only lower the result. Nothing the model writes can raise a status.

`judge` returns either an `AnswerResult` (a final decision) or a `Rejection` (a deterministic check
failed, so the attempt may be retried once, ADR-013 decision 12).
"""

from collections.abc import Mapping
from typing import Final

from grc_sidecar.llm.schemas import ModelAnswer
from grc_sidecar.questionnaire.grounding import (
    contains_identifier,
    locate_quote,
    long_enough,
    normalize,
)
from grc_sidecar.questionnaire.models import (
    AnswerResult,
    Citation,
    ReasonCode,
    Rejection,
    ordered,
)
from grc_sidecar.retrieval.models import RetrievedChunk
from grc_sidecar.retrieval.query import build_lexical_query

SUPPORTED_MIN_CONFIDENCE: Final = 0.90
REVIEW_MIN_CONFIDENCE: Final = 0.40  # below this the answer is not written at all


def blank_result(*reasons: ReasonCode, model_confidence: float | None = None) -> AnswerResult:
    """A cell that stays blank. Used for every outcome that is not a usable answer."""
    return AnswerResult(
        status="NEEDS_REVIEW",
        reasons=ordered(set(reasons)),
        answer=None,
        model_confidence=model_confidence,
        citation=None,
    )


def context_limits(*, lexical_only: bool, withheld: bool) -> set[ReasonCode]:
    """What retrieval itself could not promise for this question (ADR-013, decision 10)."""
    limits: set[ReasonCode] = set()
    if lexical_only:
        limits.add("lexical_only")
    if withheld:
        limits.add("evidence_withheld")
    return limits


def _is_no_evidence(reply: ModelAnswer) -> bool:
    """Schema precedence (ADR-013): any of these means no evidence, and any contradictory reply is
    read the same way. An empty answer cannot be written, so it counts too."""
    return (
        reply.no_evidence
        or reply.coverage == "none"
        or reply.source_id is None
        or not reply.answer.strip()
    )


def no_evidence_result(
    reply: ModelAnswer | None, *, lexical_only: bool, withheld: bool
) -> AnswerResult:
    """Blank, because there is nothing to cite. If a file is withheld, absence cannot be asserted,
    so `evidence_withheld` leads (ADR-013, decision 10)."""
    reasons = context_limits(lexical_only=lexical_only, withheld=withheld)
    reasons.add("no_evidence")
    return blank_result(
        *reasons, model_confidence=None if reply is None else reply.model_confidence
    )


def _rejection_message(reasons: set[ReasonCode], source_id: str) -> str:
    """What the model sees on the next attempt: the failed checks and the source id, nothing the
    model itself wrote."""
    lines = []
    if "framework_only" in reasons:
        lines.append(
            f"{source_id} is framework reference text. It is not evidence of what this company "
            "does. Cite company policy text, or report no evidence."
        )
    if "quote_not_verified" in reasons:
        lines.append(
            f"exact_quote does not appear word for word in {source_id}. Copy it exactly from "
            f"{source_id}, or report no evidence."
        )
    if "quote_too_short" in reasons:
        lines.append(
            "exact_quote is too short or is only a heading. Quote a whole sentence or clause "
            "that answers the question."
        )
    if "identifier_not_in_source" in reasons:
        lines.append(
            f"The question asks about a control identifier, and {source_id} does not contain "
            "that identifier. Cite a source that does, or report no evidence."
        )
    return "\n".join(lines)


def judge(
    question: str,
    reply: ModelAnswer,
    sources: Mapping[str, RetrievedChunk],
    *,
    lexical_only: bool,
    withheld: bool,
) -> AnswerResult | Rejection:
    """Decide one parsed reply. `sources` is exactly what the model was shown (id to chunk)."""
    if _is_no_evidence(reply):
        return no_evidence_result(reply, lexical_only=lexical_only, withheld=withheld)

    cited_id = reply.source_id
    chunk = None if cited_id is None else sources.get(cited_id)
    if cited_id is None or chunk is None:  # cannot happen after the strict parse; checked anyway
        return Rejection(
            reasons=("source_not_in_context",),
            message="source_id is not one of the sources you were given. Use one of them.",
        )

    # ----- the checks that must all pass; each failure is recorded, then the attempt is rejected --
    failed: set[ReasonCode] = set()
    if chunk.source_kind != "company_policy":
        failed.add("framework_only")  # framework text is never evidence (G4); this covers withdrawn
    location = locate_quote(chunk, reply.exact_quote)
    if location is None:
        failed.add("quote_not_verified")
    if not long_enough(reply.exact_quote) or (location is not None and location.heading_only):
        failed.add("quote_too_short")
    lexical = build_lexical_query(question)
    if lexical.identifiers_only and not any(
        contains_identifier(chunk.text, forms) for forms in lexical.identifier_forms
    ):
        failed.add("identifier_not_in_source")
    if failed or location is None:  # a missing location always comes with quote_not_verified
        return Rejection(reasons=ordered(failed), message=_rejection_message(failed, cited_id))

    # ----- verified: now only the limits decide between SUPPORTED, PARTIAL and NEEDS_REVIEW -----
    citation = Citation(
        doc_id=chunk.doc_id,
        source_path=chunk.source_path,
        start_line=location.start_line,
        end_line=location.end_line,
        quote=normalize(reply.exact_quote),
        file_sha256=chunk.file_sha256,
    )
    reasons = context_limits(lexical_only=lexical_only, withheld=withheld)
    if chunk.review_overdue:
        reasons.add("review_overdue")
    others = {i for i in reply.conflicting_source_ids if i != cited_id and i in sources}
    if others:
        reasons.add("possible_conflict")
    if reply.model_confidence < REVIEW_MIN_CONFIDENCE:
        reasons.add("low_confidence")
    elif reply.model_confidence < SUPPORTED_MIN_CONFIDENCE:
        reasons.add("moderate_confidence")
    if reply.coverage == "partial":
        reasons.add("partial_coverage")

    blocking = {"possible_conflict", "low_confidence"} & reasons
    if blocking:
        return AnswerResult(
            status="NEEDS_REVIEW",
            reasons=ordered(reasons),
            answer=None,
            model_confidence=reply.model_confidence,
            citation=citation,  # a verified quote helps the reviewer; the cell stays blank
        )
    return AnswerResult(
        status="PARTIAL" if reasons else "SUPPORTED",
        reasons=ordered(reasons),
        answer=reply.answer.strip(),
        model_confidence=reply.model_confidence,
        citation=citation,
    )


def finalize_rejection(
    rejection: Rejection, *, lexical_only: bool, withheld: bool, model_confidence: float | None
) -> AnswerResult:
    """The result when the retry budget is spent and the last attempt was still rejected."""
    reasons = set(rejection.reasons) | context_limits(lexical_only=lexical_only, withheld=withheld)
    return blank_result(*reasons, model_confidence=model_confidence)


def lower_for_changed_source(result: AnswerResult, current_file_sha256: str | None) -> AnswerResult:
    """At export time: a cited file that changed (or is gone) since retrieval blanks the answer.

    This can only lower a result (ADR-013, check 7). `current_file_sha256` is the hash of the file
    as it is now, in the same normalized form the index used, or None if it cannot be read.
    """
    citation = result.citation
    if citation is None or current_file_sha256 == citation.file_sha256:
        return result
    return blank_result("source_changed", *result.reasons, model_confidence=result.model_confidence)
