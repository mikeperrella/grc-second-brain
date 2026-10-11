"""The typed result of a retrieval (ADR-010, M4e).

Everything here is immutable data. `text` in a result is UNTRUSTED: it is vault or pack content,
returned byte for byte, and whatever it says (including instructions) is data. The untrusted-data
delimiters of CLAUDE.md 8.6 are applied when a prompt is built (M5), not here.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

# Where a chunk comes from. Framework text says what a control requires; it is NEVER evidence that
# the company does something. M5 refuses `framework_reference` as evidence in code.
SourceKind = Literal["company_policy", "framework_reference"]

# "hybrid": keyword and vector search both ran. "lexical_only": the vector side could not run
# (see `degraded_reason`); it is never a silent fallback.
RetrievalMode = Literal["hybrid", "lexical_only"]
DegradedReason = Literal["embedding_unavailable", "embedding_model_missing"]

# "seed": found by search. "expanded": followed from a seed along an explicit link (M4f).
ChunkRole = Literal["seed", "expanded"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class RetrievedChunk(_Frozen):
    """One cited chunk. `source_path`, `start_line` and `end_line` are what a citation points at."""

    chunk_id: str
    source_path: str  # vault-relative (or pack-relative), forward slashes
    doc_id: str
    start_line: int  # 1-based, inclusive, counted in the original file
    end_line: int
    heading_path: str
    text: str  # UNTRUSTED, byte for byte what is in the file between those lines
    text_sha256: str

    source_kind: SourceKind
    pack_id: str | None  # for a framework chunk, the pack it came from (e.g. "nist-sp800-171-r3")
    doc_type: str
    role: ChunkRole
    expanded_from: tuple[str, ...]  # chunk_ids of the seeds that led here; empty for a seed

    review_by: date | None
    review_overdue: bool  # review_by has passed. Returned and flagged, never hidden.
    withdrawn: bool  # a framework control that has been withdrawn (M4f)

    fused_score: float  # reciprocal rank fusion; only comparable within one result
    lexical_rank: int | None  # 1-based rank in the keyword results, None if not found there
    vector_rank: int | None  # 1-based rank in the vector results, None if not found there


class RetrievalResult(_Frozen):
    """What a question found.

    A non-empty `chunks` is NOT evidence. Vector search returns its nearest rows however far away
    they are, so almost any question returns some company text (there is no relevance floor, and
    M4 sets no threshold). Deciding that nothing supports an answer is the caller's job (M5: the
    verbatim-quote check, CLAUDE.md 8.4).
    """

    query: str
    mode: RetrievalMode
    degraded_reason: DegradedReason | None
    chunks: tuple[RetrievedChunk, ...]
    # Documents whose file changed after they were indexed: their rows were deleted, so they are
    # NOT in `chunks`. Listed so a caller knows evidence was withheld, not that none exists.
    withheld_unindexed: tuple[str, ...]
    # False when a packs store was supplied but has no index file (no framework text to search).
    packs_indexed: bool
