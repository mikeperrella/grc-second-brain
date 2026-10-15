"""The vault indexer: keeps the search index equal to the vault (CLAUDE.md 8.1, ADR-009).

`sync()` flow, in this order (the order is the safety property):

1. Ask the embedding client whether the model is installed and measure its vector size. Any
   failure stops here, before anything is written.
2. Scan and chunk the vault (on a worker thread, because it reads files).
3. `begin_sync`, BEFORE any embedding, in one transaction: delete documents that are gone or no
   longer valid, and for every changed document delete its chunk, vector, full-text and link rows
   and mark it stale. From this commit on nothing searchable derives from an old version of a file,
   whatever happens next (a failed embed, an abort, a crash).
4. For each new or stale document: embed ALL its chunks first (no database touched), then
   `replace_document` writes them in one transaction and clears `stale`.
5. Unchanged documents (same hash, same chunker version) are never embedded again.
6. Re-resolve every link, so links follow documents that appeared or disappeared.

`refresh_staleness()` is steps 2 and 3 only. It needs no embedding model, so it works with Ollama
down, and it can only delete.

Library code never calls `asyncio.run`: `sync` is a coroutine, so M4 can await it from a request
handler and tests wrap it in `asyncio.run`.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from grc_sidecar.errors import EmbeddingResponseError
from grc_sidecar.llm.embeddings import EmbeddingClient
from grc_sidecar.repositories.base import IndexedDocument, ScanSnapshot, VectorRepository
from grc_sidecar.vault.chunker import CHUNKER_VERSION, chunk_document, embedding_input
from grc_sidecar.vault.links import ResolutionIndex, extract_links
from grc_sidecar.vault.models import Chunk, IndexIssue, ParsedDocument, RawLink, ScanResult
from grc_sidecar.vault.scan import scan_vault

DEFAULT_COLLECTION = "vault"

ProgressCallback = Callable[[int, int], None]  # (documents done, documents total)


class FailedDocument(BaseModel):
    """A document that could not be embedded. Never contains document text."""

    model_config = ConfigDict(frozen=True)

    source_path: str
    code: str


class SyncReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    complete: bool  # False if any document could not be embedded (see `failed`)
    documents_seen: int  # valid documents found by the scan
    indexed: tuple[str, ...]  # embedded and written during this run
    unchanged: int  # skipped: same hash and chunker version, already indexed
    removed: tuple[str, ...]  # gone or no longer valid: all their rows deleted
    marked_stale: tuple[str, ...]  # changed: old rows deleted, awaiting (re)indexing
    failed: tuple[FailedDocument, ...]  # still stale, holding no rows
    chunks_embedded: int
    issues: tuple[IndexIssue, ...]  # per-file problems found by the scan


class RefreshReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    index_found: bool  # False: there was no index file, and nothing was done
    removed: tuple[str, ...]
    marked_stale: tuple[str, ...]


@dataclass(frozen=True)
class _Prepared:
    doc: ParsedDocument
    chunks: tuple[Chunk, ...]
    links: tuple[RawLink, ...]


def _scan_and_chunk(
    scan: Callable[[], ScanResult],
) -> tuple[list[_Prepared], tuple[IndexIssue, ...]]:
    result = scan()
    prepared = [
        _Prepared(doc=doc, chunks=chunk_document(doc), links=extract_links(doc))
        for doc in result.documents
    ]
    return prepared, result.issues


def _indexed_document(doc: ParsedDocument, collection: str) -> IndexedDocument:
    front = doc.frontmatter
    return IndexedDocument(
        source_path=doc.source_path,
        collection=collection,
        doc_id=front.doc_id,
        file_sha256=doc.file_sha256,
        chunker_version=CHUNKER_VERSION,
        type=front.type,
        owner=front.owner,
        review_by=front.review_by.isoformat() if front.review_by is not None else None,
    )


def _snapshot(documents: list[ParsedDocument]) -> ScanSnapshot:
    return ScanSnapshot(
        chunker_version=CHUNKER_VERSION,
        documents={doc.source_path: doc.file_sha256 for doc in documents},
    )


class Indexer:
    def __init__(
        self,
        repo: VectorRepository,
        embedder: EmbeddingClient,
        vault_dir: Path,
        *,
        collection: str = DEFAULT_COLLECTION,
        collection_of: Callable[[str], str] | None = None,
        scan: Callable[[], ScanResult] | None = None,
    ) -> None:
        self._repo = repo
        self._embedder = embedder
        self._vault_dir = vault_dir
        # `scan()` finds the documents. By default it reads `vault_dir` from disk; the pack loader
        # passes a scan over bytes it already verified, so no file is read a second time (ADR-008).
        self._scan: Callable[[], ScanResult] = scan or (lambda: scan_vault(vault_dir))
        # `collection_of(source_path)` labels each document separately (the pack loader uses the
        # pack folder name); without it every document gets `collection`.
        self._collection_of = collection_of or (lambda _source_path: collection)

    async def sync(self, on_progress: ProgressCallback | None = None) -> SyncReport:
        """Bring the index up to date with the vault. See the module docstring for the order.

        `on_progress(done, total)` is called before each document with the number already dealt
        with (embedded, unchanged or failed), and once at the end with `done == total`. It carries
        two integers and nothing else. The callback must be cheap and must not raise; it may be
        called from a thread other than the one that reads the counters, so it has to be
        thread-safe.

        Raises ModelMissingError, EmbeddingDimensionError, EmbeddingUnavailableError (a
        connection-level problem aborts the run) or a repository error. Whatever was marked stale
        and not yet replaced stays stale, holding no rows.
        """
        profile = await self._embedder.ensure_ready()  # nothing is written if this fails
        prepared, issues = await asyncio.to_thread(_scan_and_chunk, self._scan)
        if self._repo.mode != "full":
            await self._repo.open_or_create(profile)

        sync = await self._repo.begin_sync(_snapshot([p.doc for p in prepared]))
        states = await self._repo.document_states()

        indexed: list[str] = []
        failed: list[FailedDocument] = []
        unchanged = 0
        chunks_embedded = 0
        total = len(prepared)
        for done, item in enumerate(prepared):
            if on_progress is not None:
                on_progress(done, total)  # `done` documents are finished before this one starts
            state = states.get(item.doc.source_path)
            if state is not None and not state.stale:
                unchanged += 1
                continue
            texts = [embedding_input(c.heading_path, c.text)[0] for c in item.chunks]
            failure = False
            vectors: list[list[float]] = []
            try:
                vectors = await self._embedder.embed_documents(texts)
            except EmbeddingResponseError:
                failure = True  # this file only: it stays stale, the run goes on
            if failure:
                failed.append(FailedDocument(source_path=item.doc.source_path, code="embedding"))
                continue
            await self._repo.replace_document(
                _indexed_document(item.doc, self._collection_of(item.doc.source_path)),
                item.chunks,
                vectors,
                item.links,
            )
            indexed.append(item.doc.source_path)
            chunks_embedded += len(item.chunks)
        if on_progress is not None:
            on_progress(total, total)

        known = ResolutionIndex((p.doc.source_path, p.doc.frontmatter.doc_id) for p in prepared)
        await self._repo.re_resolve_links(known)
        return SyncReport(
            complete=not failed,
            documents_seen=len(prepared),
            indexed=tuple(indexed),
            unchanged=unchanged,
            removed=sync.removed,
            marked_stale=sync.marked_stale,
            failed=tuple(failed),
            chunks_embedded=chunks_embedded,
            issues=issues,
        )

    async def refresh_staleness(self) -> RefreshReport:
        """Steps 2 and 3 of `sync`: delete the rows of every file that changed since the last scan.

        No embedding model and no Ollama are involved, and nothing can be inserted: if the index
        is not open it is opened for deletions only, using the profile stored in it. With no index
        file it does nothing and says so (`index_found=False`). M4 calls this before answering, so
        a file edited since the last sync is excluded at query time.
        """
        if self._repo.mode == "closed" and not await self._repo.open_for_deletions():
            return RefreshReport(index_found=False, removed=(), marked_stale=())
        result = await asyncio.to_thread(self._scan)
        report = await self._repo.begin_sync(_snapshot(list(result.documents)))
        return RefreshReport(
            index_found=True, removed=report.removed, marked_stale=report.marked_stale
        )
