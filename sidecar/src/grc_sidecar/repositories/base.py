"""The VectorRepository protocol and its typed boundary models (ADR-001, ADR-009).

Nothing outside `repositories/` knows that the store is SQLite. Every method is `async` so that
callers (the indexer now, FastAPI handlers in M4) never block the event loop; how an
implementation reaches its database is its own business.

One repository call is one transactional unit: a document's chunks, vectors, full-text rows and
links change together or not at all.
"""

from collections.abc import Sequence
from datetime import date
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
    # sha256 of the document's normalized text as indexed (the `documents` row). A later export
    # compares it with the file's current hash, so a cited file that changed is noticed (ADR-013).
    file_sha256: str


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

    async def source_path_of_doc_id(self, doc_id: str) -> str | None:
        """The path of the document with this `doc_id`, or None (used to follow a resolved link)."""

    async def chunks_for(self, source_path: str) -> list[Chunk]: ...

    async def links_for(self, source_path: str) -> list[ResolvedLink]: ...

    async def verify_integrity(self) -> IntegrityReport: ...


# ---------------------------------------------------------------------------------------------
# The promise ledger (ADR-017). Append-only: the protocol has insert and read methods and nothing
# else, so no caller can even ask for an update or a delete.
# ---------------------------------------------------------------------------------------------

LedgerAnswerStatus = Literal["SUPPORTED", "PARTIAL", "NEEDS_REVIEW"]
PromiseStatus = Literal["SUPPORTED", "PARTIAL"]
PromiseEventKind = Literal["created", "recall_state_changed", "acknowledged", "voided"]
RecallState = Literal["intact", "moved", "changed", "missing"]
# A fixed set, so a void carries no free text and opens no new content channel (ADR-017 #13).
VoidReason = Literal["wrong_customer", "imported_in_error", "not_actually_sent", "other"]


class NewRunRecord(_Frozen):
    """What the run recorder supplies. `created_on` and `row_sha256` are added by the store."""

    run_id: str = Field(min_length=1)
    model: str
    digest_before: str
    digest_after: str
    template_sha256: str
    input_sha256: str
    file_name: str
    sheet: str
    header_row: int
    question_col: int
    first_new_column: int
    question_count: int
    complete: bool


class RunRecord(NewRunRecord):
    created_on: date
    row_sha256: str


class RunAnswer(_Frozen):
    """One question of a run, answered or blank. Hashes are sha256 hex (ADR-017 decision 3)."""

    run_id: str = Field(min_length=1)
    row_index: int
    question_sha256: str
    status: LedgerAnswerStatus
    reasons: tuple[str, ...] = ()
    source_path: str | None = None
    doc_id: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    quote: str | None = None
    cited_lines_sha256: str | None = None
    cited_file_sha256: str | None = None
    answer_cell_sha256: str | None = None
    status_cell_sha256: str
    evidence_cell_sha256: str | None = None


class StoredRunAnswer(RunAnswer):
    row_sha256: str


class Customer(_Frozen):
    customer_id: str
    display_name: str  # as typed
    normalized_name: str  # the unique key
    row_sha256: str


class NewPromise(_Frozen):
    """A promise to record. The store adds `promise_id`, `created_on` and `row_sha256`."""

    customer_id: str
    run_id: str
    run_row_index: int
    question_sha256: str  # data (the "rows were not moved" check), never the key
    question_text: str
    answer_text: str
    status: PromiseStatus
    source_path: str
    doc_id: str
    start_line: int
    end_line: int
    quote: str
    cited_lines_sha256: str
    cited_file_sha256: str
    edited_after_draft: bool
    import_workbook_sha256: str
    sent_on: date


class Promise(NewPromise):
    promise_id: str
    created_on: date
    row_sha256: str


class NewPromiseEvent(_Frozen):
    """An event to append. `created` is written by `add_promise` itself, never by a caller."""

    promise_id: str
    kind: Literal["recall_state_changed", "acknowledged", "voided"]
    state: RecallState | None = None  # recall_state_changed only
    detail_code: str | None = None  # a fixed code: a RecallState detail, or a VoidReason


class PromiseEvent(_Frozen):
    event_id: int
    promise_id: str
    kind: PromiseEventKind
    state: RecallState | None
    detail_code: str | None
    on_date: date
    row_sha256: str


class LedgerRepository(Protocol):
    """Append-only store of run records, customers, promises and promise events."""

    async def open_or_create(self) -> None:
        """Open the ledger file, or create it. Fails closed (LedgerSchemaMismatchError) if the
        file is another schema version or not a ledger; nothing is migrated or deleted."""

    async def close(self) -> None: ...

    async def add_run_record(self, record: NewRunRecord, answers: Sequence[RunAnswer]) -> None:
        """One transaction: the run and all its answers, or nothing. Dated from the Clock."""

    async def open_existing(self) -> bool:
        """Open the ledger only if its file already exists and holds the schema; never create it.

        For read-only callers (the review queue). False means there is no ledger yet, and nothing
        was written. Fails closed like `open_or_create` on another schema version.
        """

    async def get_run_record(self, run_id: str) -> RunRecord | None: ...

    async def get_run_record_by_file_name(self, file_name: str) -> RunRecord | None:
        """The run record whose recorded `file_name` equals `file_name` exactly, or None.

        Exact and case-sensitive: never a prefix, never the 8-hex run id in the name. None also
        when more than one record has that name, so an ambiguous name never matches a record.
        """

    async def list_run_answers(self, run_id: str) -> list[StoredRunAnswer]: ...

    async def add_customer(self, display_name: str) -> Customer:
        """Refuses a bad name (`customer_name_invalid`) or one whose normalized form exists
        (`customer_exists`, carrying that customer's id)."""

    async def get_customer(self, customer_id: str) -> Customer | None: ...

    async def find_customer_by_name(self, name: str) -> Customer | None:
        """The customer whose normalized name equals the normalized `name`, or None."""

    async def list_customers(self) -> list[Customer]: ...

    async def add_promise(self, promise: NewPromise) -> Promise:
        """One transaction: the promise and its `created` event. Refuses a second promise for
        the same customer, run and row (`promise_exists`)."""

    async def get_promise(self, promise_id: str) -> Promise | None: ...

    async def list_promises(self, customer_id: str | None = None) -> list[Promise]: ...

    async def add_event(self, event: NewPromiseEvent) -> PromiseEvent: ...

    async def list_events(self, promise_id: str) -> list[PromiseEvent]:
        """Oldest first, by event id (an integer, so the order never depends on a timestamp)."""
