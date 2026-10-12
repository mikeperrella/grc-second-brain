"""What the engine decides about one question (ADR-013).

`Status` and `ReasonCode` are the vocabulary of the export's Status and Reason columns. A status is
a function of facts the code can check; the model's own output can only add reasons, never remove
one, so it can only lower a status.
"""

from dataclasses import dataclass
from typing import Final, Literal

Status = Literal["SUPPORTED", "PARTIAL", "NEEDS_REVIEW"]
ReviewState = Literal["unreviewed"]  # nothing in M5 is approved (ADR-013, decision 14)

ReasonCode = Literal[
    # the question or the model could not be used at all
    "model_unavailable",
    "question_too_long",
    "empty_question",
    "prompt_too_large",
    "output_truncated",
    "retry_exhausted",
    # a deterministic check failed
    "source_changed",
    "framework_only",
    "quote_not_verified",
    "quote_too_short",
    "source_not_in_context",
    "identifier_not_in_source",
    # the evidence cannot be used as it stands
    "possible_conflict",
    "low_confidence",
    "no_evidence",
    # limits on a verified answer (these cap at PARTIAL)
    "review_overdue",
    "evidence_withheld",
    "lexical_only",
    "partial_coverage",
    "moderate_confidence",
]

# The fixed order reasons are listed in: the first one is the main reason. Blocking reasons come
# before the ones that only cap an answer at PARTIAL.
REASON_ORDER: Final[tuple[ReasonCode, ...]] = (
    "model_unavailable",
    "question_too_long",
    "empty_question",
    "prompt_too_large",
    "output_truncated",
    "retry_exhausted",
    "source_changed",
    "framework_only",
    "quote_not_verified",
    "quote_too_short",
    "source_not_in_context",
    "identifier_not_in_source",
    "possible_conflict",
    "low_confidence",
    "evidence_withheld",  # before no_evidence: with a file withheld, absence cannot be asserted
    "no_evidence",
    "review_overdue",
    "lexical_only",
    "partial_coverage",
    "moderate_confidence",
)

# Only these reasons may accompany a PARTIAL status: they limit an answer, they do not block it.
CAPPING_REASONS: Final[frozenset[ReasonCode]] = frozenset(
    {
        "review_overdue",
        "evidence_withheld",
        "lexical_only",
        "partial_coverage",
        "moderate_confidence",
    }
)


def ordered(
    reasons: set[ReasonCode] | frozenset[ReasonCode] | tuple[ReasonCode, ...],
) -> tuple[ReasonCode, ...]:
    """The reasons, each once, in the fixed order."""
    present = set(reasons)
    return tuple(reason for reason in REASON_ORDER if reason in present)


@dataclass(frozen=True)
class Citation:
    """The exact place a verified quote was found, for the Evidence column."""

    doc_id: str
    source_path: str  # vault-relative, forward slashes: never an absolute path
    start_line: int  # 1-based, inclusive, in the original file
    end_line: int
    quote: str  # the quote as verified, whitespace-normalized
    file_sha256: str  # of the cited file as indexed; compared again when the export is written


@dataclass(frozen=True)
class AnswerResult:
    """The outcome for one question.

    `answer` is None for NEEDS_REVIEW: the cell stays blank and `reasons` say why.
    `model_confidence`
    is the model's own rating, kept for the reviewer and never used to grant a status.
    """

    status: Status
    reasons: tuple[ReasonCode, ...]
    answer: str | None
    model_confidence: float | None
    citation: Citation | None
    review_state: ReviewState = "unreviewed"


@dataclass(frozen=True)
class Rejection:
    """A deterministic check failed, so the attempt is not usable and may be retried.

    `message` is what the model is shown on the next attempt. It names the failed check and the
    source id and never repeats text the model wrote, so a hostile reply cannot steer the retry.
    """

    reasons: tuple[ReasonCode, ...]
    message: str
