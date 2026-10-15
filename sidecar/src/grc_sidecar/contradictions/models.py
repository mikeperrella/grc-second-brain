"""Typed data for the contradiction detector (ADR-019). Everything here is immutable.

Chunk 4a-2 is the code half: nothing in this package calls a model. A `Finding` in this half is
never `VALUE_CONFLICT`, because that status needs a model verdict that does not exist yet.
"""

from fractions import Fraction
from typing import Literal

from pydantic import BaseModel, ConfigDict

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
    "days_vs_months", "weak_subject_overlap", "no_model_verdict", "model_lowered"
]
Flag = Literal["model_not_consulted"]


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


NUMERIC_ONLY_STATEMENT = (
    "This check compares numbers only (durations, percentages, counts, frequencies). A conflict "
    "without numbers is not found, and no finding does not mean the policies agree."
)
