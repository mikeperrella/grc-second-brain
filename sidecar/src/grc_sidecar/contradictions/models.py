"""Typed data for the contradiction detector (ADR-019). Everything here is immutable.

Only `verdict.py` talks to a model; this module holds plain data, so the status code can use the
verdict's shape without importing the llm package. A `Finding` is `VALUE_CONFLICT` only if the code
facts allow it AND a valid model verdict keeps it (`status.py`).
"""

from fractions import Fraction
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

# What a number measures, after conversion (ADR-019 decisions 5 and 6). Two claims are comparable
# only when their kinds are equal, with one exception: `days` against `months` is a candidate and
# never a conflict, because a month is not a fixed number of days.
Kind = Literal["days", "months", "business_days", "percent", "characters", "attempts", "frequency"]

FindingStatus = Literal["VALUE_CONFLICT", "CANDIDATE"]
Overlap = Literal["strong", "weak"]

# Why a pair is dropped before it can be shown (ADR-019 decision 8). Each is counted.
DropReason = Literal[
    "quote_not_verified",
    "quote_too_short",
    "quote_is_heading",
    "number_not_in_quote",
    "chunk_not_in_context",
    "same_line_pair",
    "over_cap",
]

# Why a finding that survived is only a candidate.
CandidateReason = Literal[
    "days_vs_months",
    "weak_subject_overlap",
    "no_model_verdict",
    "model_lowered",
    "model_quote_not_verified",
]
Flag = Literal["model_not_consulted"]

# What the model said the two statements are (ADR-019 decision 10).
Relation = Literal["same_subject_value_mismatch", "scope_difference", "unrelated", "unclear"]
RELATIONS: Final = ("same_subject_value_mismatch", "scope_difference", "unrelated", "unclear")

# How the model fared in a run: fixed words only, on every response (owner decision at 4b).
# `partial` means at least one pair got a verdict and at least one pair did not.
ModelUse = Literal["available", "unavailable", "model_missing", "non_local_refused", "partial"]

SUBJECT_MAX_CHARS: Final = 120  # starting values, unmeasured (ADR-019)
QUOTE_MAX_CHARS: Final = 400
EXPLANATION_MAX_CHARS: Final = 300


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)


class ChunkText(_Frozen):
    """One company-policy chunk as the detector reads it from the index."""

    chunk_id: str
    source_path: str
    start_line: int
    text: str


class Number(_Frozen):
    """A number the code read out of a quote: its written text, kind and converted value."""

    text: str  # as written, for example "90 days" or "twice a year"
    kind: Kind
    value: Fraction  # after conversion: days, months, per-year count, percent, ...


class Claim(_Frozen):
    """A number with a unit and a subject, found in one sentence or one Markdown table row."""

    claim_id: str
    chunk_id: str
    source_path: str
    start_line: int  # 1-based, inclusive, counted in the original file
    end_line: int
    quote: str  # the sentence or the whole table row, as written
    subject: str  # shown to the reader
    subject_words: frozenset[str]  # normalized content words, used for overlap
    number: Number


class Pair(_Frozen):
    """Two claims of comparable kinds, with different values and overlapping subjects."""

    pair_id: str
    a: Claim
    b: Claim
    overlap: Overlap


class PairVerdict(BaseModel):
    """The model's reply about one pair, after the strict parse. Every field is required.

    Unknown keys and wrong JSON types are refused (`model_validate_json(strict=True)`). The reply
    can keep a finding at its ceiling or lower it; it can never add a pair or change a fact.
    `model_confidence` is recorded and shown, and decides nothing (owner decision at 4b).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    conflict: bool
    relation: Relation
    subject: Annotated[str, Field(max_length=SUBJECT_MAX_CHARS)]
    quote_a: Annotated[str, Field(max_length=QUOTE_MAX_CHARS)]
    quote_b: Annotated[str, Field(max_length=QUOTE_MAX_CHARS)]
    explanation: Annotated[str, Field(max_length=EXPLANATION_MAX_CHARS)]
    model_confidence: Annotated[float, Field(ge=0.0, le=1.0)]


class ModelVerdictView(_Frozen):
    """What a finding shows of the model's reply: untrusted text, length-capped, never written."""

    conflict: bool
    relation: Relation
    explanation: str
    model_confidence: float


class Finding(_Frozen):
    """What the reader sees: two verified quotes with their lines, and a status set by code."""

    finding_id: str
    status: FindingStatus
    quote_a: str
    source_a: str  # "path:start-end"
    quote_b: str
    source_b: str
    subject: str
    numbers: tuple[str, ...]
    flags: tuple[Flag, ...]
    reasons: tuple[CandidateReason, ...]
    verdict: ModelVerdictView | None = None  # None when the model gave no valid verdict


class DetectionReport(_Frozen):
    """A run's outcome. `says` is the fixed statement of what was and was not checked (F33-9)."""

    findings: tuple[Finding, ...]
    dropped: dict[str, int]  # DropReason -> count
    chunks_read: int
    excluded_chunks: int  # framework text and withdrawn controls, never compared
    claims_found: int
    pairs_generated: int
    withheld_stale: int  # documents with no rows because the file changed after indexing
    says: str
    over_cap: int = 0  # verified pairs left out by the cap (strong pairs are kept first)
    model_status: ModelUse = "unavailable"
    model_calls: int = 0  # replies received, over all attempts
    # The model's RAW valid verdicts, for every pair it answered, even when the code ceiling capped
    # the status: relation -> {"conflict_true": n, "conflict_false": n}. Never persisted.
    verdict_counts: dict[str, dict[str, int]] = Field(default_factory=dict)
    no_verdict: int = 0  # pairs asked about that ended with no valid verdict


NUMERIC_ONLY_STATEMENT = (
    "This check compares numbers only (durations, percentages, counts, frequencies). A conflict "
    "without numbers is not found, and no finding does not mean the policies agree."
)
