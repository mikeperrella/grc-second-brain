"""The VectorRepository protocol and its typed boundary models (ADR-001, ADR-009).

Nothing outside `repositories/` knows that the store is SQLite. Every method is `async` so that
callers (the indexer now, FastAPI handlers in M4) never block the event loop; how an
implementation reaches its database is its own business.

One repository call is one transactional unit: a document's chunks, vectors, full-text rows and
links change together or not at all.
"""

from collections.abc import Sequence
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from grc_sidecar.vault.links import ResolutionIndex
from grc_sidecar.vault.models import Chunk, RawLink, ResolvedLink

# Why a document or row is reported by verify_integrity().
IntegrityCode = Literal[
    "vec_without_chunk",
    "fts_without_chunk",
    "chunk_without_vec",
    "chunk_without_fts",
    "chunk_without_document",
    "link_without_document",
    "rows_for_stale_document",
]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class EmbeddingProfile(_Frozen):
    """Everything that decides what a stored vector means. A change to any field of it means
    every stored vector is from another "space", so the index must be rebuilt by a human."""

    model: str = Field(min_length=1)
    model_digest: str = Field(min_length=1)  # from Ollama /api/tags, so a re-pulled model differs
    dimension: int = Field(gt=0, le=8192)
    doc_prefix: str
    query_prefix: str


class DocState(_Frozen):
    """What the index currently holds for one document."""

    file_sha256: str
    chunker_version: str
    stale: bool


class IndexedDocument(_Frozen):
    """The `documents` row written together with a document's chunks."""

    source_path: str  # vault-relative, forward slashes
    collection: str = Field(min_length=1)
    doc_id: str
    file_sha256: str
    chunker_version: str
    type: str
    owner: str
    review_by: str | None = None  # ISO date text, or None


class DocInfo(_Frozen):
    """What a retrieval result needs to know about the document a chunk came from."""

    source_path: str
    collection: str  # "vault" for the user's own documents, "pack:<id>" for a framework pack
    doc_id: str
    type: str
    review_by: str | None  # ISO date text, or None


class ScanSnapshot(_Frozen):
    """The result of a vault scan, reduced to what `begin_sync` needs."""

    chunker_version: str
    # Every valid document found by the scan: source_path -> sha256 of its normalized text.
    documents: dict[str, str]


class StaleReport(_Frozen):
    removed: tuple[str, ...]  # no longer valid: deleted entirely, `documents` row included
    marked_stale: tuple[str, ...]  # changed: rows deleted, `documents` row kept with stale = 1


class ScoredChunk(_Frozen):
    chunk: Chunk
    distance: float  # cosine distance, 0.0 = same direction, 1.0 = unrelated, 2.0 = opposite


class LexicalHit(_Frozen):
    chunk: Chunk
    bm25: float  # SQLite's bm25(): LOWER is a better match (the values are negative)


class IntegrityProblem(_Frozen):
    code: IntegrityCode
    detail: str  # a row id or a vault-relative path, never document text


class IntegrityReport(_Frozen):
    problems: tuple[IntegrityProblem, ...]
    stale_documents: tuple[str, ...]  # informational: awaiting re-index, holding no rows

    @property
    def ok(self) -> bool:
        return not self.problems


RepositoryMode = Literal["closed", "deletions_only", "full"]


class VectorRepository(Protocol):
    @property
    def mode(self) -> RepositoryMode:
        """`closed`, `deletions_only` (see `open_for_deletions`) or `full`."""

    async def open_or_create(self, profile: EmbeddingProfile) -> None:
        """Open the index, or create it. Fails closed if the file does not match `profile`.

        An index that is open for deletions only is closed and reopened in full mode.
        """

    async def open_for_deletions(self) -> bool:
        """Open an EXISTING index with the profile stored in it, for deletions only.

        Returns False, changing nothing, when there is no index file. In this mode `begin_sync`,
        `remove_document` and the read methods work; `replace_document` and `re_resolve_links`
        raise. It needs no embedding model, so staleness can be refreshed with Ollama down.
        """

    async def document_states(self) -> dict[str, DocState]: ...

    async def begin_sync(self, scan: ScanSnapshot) -> StaleReport:
        """One transaction: delete documents that are gone, and for every changed document
        delete its chunk, vector, full-text and link rows, keep its `documents` row and set
        stale = 1. After it commits, nothing searchable derives from an old version of a file."""

    async def replace_document(
        self,
        doc: IndexedDocument,
        chunks: Sequence[Chunk],
        vectors: Sequence[Sequence[float]],
        links: Sequence[RawLink],
    ) -> None:
        """One transaction: delete any rows the document already has, insert the new chunks,
        vectors, full-text rows and links, and clear stale. Correct without `begin_sync`."""

    async def remove_document(self, source_path: str) -> None: ...

    async def re_resolve_links(self, known: ResolutionIndex) -> None: ...

    async def search(self, query: Sequence[float], k: int) -> list[ScoredChunk]:
        """The k nearest chunks. There is no stale filter: stale documents have no rows."""

    async def lexical_search(self, match: str, k: int) -> list[LexicalHit]:
        """Up to k chunks matching an FTS5 query, best first (bm25, then chunk_id).

        `match` must come from `retrieval.query.build_lexical_query`: every word quoted, so user
        text can never change the meaning of the query. An empty string returns no chunks.
        """

    async def documents_info(self, source_paths: Sequence[str]) -> dict[str, DocInfo]:
        """The `documents` row of each path that has one (unknown paths are simply absent)."""

    async def chunks_for(self, source_path: str) -> list[Chunk]: ...

    async def links_for(self, source_path: str) -> list[ResolvedLink]: ...

    async def verify_integrity(self) -> IntegrityReport: ...

    async def close(self) -> None: ...
