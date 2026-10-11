"""SQLite + sqlite-vec + FTS5 implementation of VectorRepository (ADR-001, ADR-009, ADR-010).

This is the only module that imports `sqlite3` or `sqlite_vec` (Semgrep enforces it). One file
holds five tables that must always agree:

    documents    one row per source file (kept, stale = 1, while a changed file awaits re-index)
    chunks       the citable text, with its file, line range and hashes
    vec_chunks   vec0 virtual table, one cosine vector per chunk, rowid == chunks.rowid
    fts_chunks   FTS5 table over chunk text and heading path, rowid == chunks.rowid
    links        explicit links as written, plus how they resolved

A document's rows in the last four change in ONE transaction. Stale content is removed, not
hidden: a document marked stale has no rows in any searchable table, so no read path needs a
stale filter (a vec0 nearest-neighbour search returns its top k before any outer filter, so
hidden rows could starve the result).

Threading: `_SqliteStore` is the synchronous core. It is created on, and only ever touched from,
the single worker thread (worker.py). `SqliteVecRepository` is the async facade that dispatches
to it.
"""

import contextlib
import hashlib
import json
import os
import sqlite3
import struct
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any, Final, Self, TypeVar

import sqlite_vec
from pydantic import ValidationError

from grc_sidecar.errors import (
    ExtensionUnavailableError,
    GrcError,
    IndexProfileMismatchError,
    IndexRebuildRequiredError,
    IndexStoreError,
    PathNotAllowedError,
    VectorDimensionError,
)
from grc_sidecar.repositories.base import (
    DocState,
    EmbeddingProfile,
    IndexedDocument,
    IntegrityProblem,
    IntegrityReport,
    LexicalHit,
    RepositoryMode,
    ScanSnapshot,
    ScoredChunk,
    StaleReport,
)
from grc_sidecar.repositories.worker import SqliteWorker
from grc_sidecar.vault.links import ResolutionIndex, resolve_link
from grc_sidecar.vault.models import Chunk, RawLink, ResolvedLink

# Bump when any table below changes. A different number means rebuild, never migrate.
# 2: chunks.rowid is AUTOINCREMENT, so a rowid is never reused (see the comment on `chunks`).
SCHEMA_VERSION: Final = 2
INDEX_SUFFIX: Final = ".sqlite3"
MAX_K: Final = 4096

# The vector dimension cannot be a bound parameter in DDL, so it is substituted after int().
_VEC_DDL: Final = (
    "CREATE VIRTUAL TABLE vec_chunks USING vec0(embedding float[__DIM__] distance_metric=cosine)"
)

# PRAGMA cannot take a bound parameter either, so the version is substituted after int() right
# where it is used (a value checked with int(), not a literal copy that could drift from
# SCHEMA_VERSION).
_USER_VERSION_PRAGMA: Final = "PRAGMA user_version = __VERSION__"

_SCHEMA: Final = (
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID",
    """CREATE TABLE documents (
        source_path TEXT PRIMARY KEY,
        collection TEXT NOT NULL,
        doc_id TEXT NOT NULL,
        file_sha256 TEXT NOT NULL,
        chunker_version TEXT NOT NULL,
        stale INTEGER NOT NULL DEFAULT 0 CHECK (stale IN (0, 1)),
        type TEXT NOT NULL,
        owner TEXT NOT NULL,
        review_by TEXT
    )""",
    # AUTOINCREMENT: a rowid is never handed out twice. vec_chunks and fts_chunks share the rowid,
    # so with reuse a leftover row from a bug would be silently adopted by the next chunk that got
    # the same rowid (a wrong vector or stale text) and would no longer look like an orphan.
    """CREATE TABLE chunks (
        rowid INTEGER PRIMARY KEY AUTOINCREMENT,
        chunk_id TEXT NOT NULL UNIQUE,
        source_path TEXT NOT NULL,
        doc_id TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        heading_path TEXT NOT NULL,
        text TEXT NOT NULL,
        text_sha256 TEXT NOT NULL,
        flags TEXT NOT NULL
    )""",
    "CREATE INDEX chunks_by_source ON chunks (source_path)",
    "CREATE INDEX chunks_by_doc_and_text ON chunks (doc_id, text_sha256)",
    "CREATE INDEX chunks_by_text ON chunks (text_sha256)",
    """CREATE TABLE links (
        id INTEGER PRIMARY KEY,
        source_path TEXT NOT NULL,
        source_line INTEGER NOT NULL,
        target TEXT NOT NULL,
        alias TEXT,
        fragment TEXT,
        is_embed INTEGER NOT NULL,
        origin TEXT NOT NULL,
        status TEXT NOT NULL,
        target_doc_id TEXT,
        code TEXT
    )""",
    "CREATE INDEX links_by_source ON links (source_path)",
)

# `-` is a token character so `AC-2` is one token; `.` is a separator so `CC6.1.` at the end of
# a sentence still matches. Identifier queries must be phrase-quoted (ADR-010).
_FTS_DDL: Final = (
    "CREATE VIRTUAL TABLE fts_chunks USING fts5("
    "text, heading_path, tokenize = \"unicode61 tokenchars '-'\")"
)

T = TypeVar("T")


def vec_extension_path() -> Path:
    """The one native extension the process guard may allow (ADR-007 amendment)."""
    return Path(sqlite_vec.loadable_path())


def vault_index_path(index_dir: Path, vault_dir: Path) -> Path:
    """`index_dir/vault-<16 hex>.sqlite3`, keyed to the canonical vault root so that two vaults
    never share an index."""
    root = os.path.normcase(str(vault_dir.resolve(strict=True)))
    digest = hashlib.sha256(root.encode("utf-8")).hexdigest()[:16]
    return index_dir / f"vault-{digest}{INDEX_SUFFIX}"


def rebuild_index(index_dir: Path, file_name: str) -> None:
    """Delete one index file (and a leftover journal) inside `index_dir`, nothing else.

    The explicit "start over" for a profile mismatch or a damaged file. The name must be a plain
    `*.sqlite3` file name: no separator, no `..`, and the target must not be a link.
    """
    # Both separators are refused on every platform: a backslash is only a separator on Windows,
    # and the same call must behave the same everywhere.
    if (
        "/" in file_name
        or "\\" in file_name
        or Path(file_name).name != file_name
        or not file_name.endswith(INDEX_SUFFIX)
    ):
        raise PathNotAllowedError
    # Errors are raised AFTER their `except` block, never inside it, so the OS error (whose
    # message can contain a path) is not attached to ours as __context__ or __cause__.
    root: Path | None = None
    with contextlib.suppress(OSError):
        root = index_dir.resolve(strict=True)
    if root is None:
        raise PathNotAllowedError
    target = root / file_name
    if target.is_symlink():
        raise PathNotAllowedError
    deleted = True
    try:
        _delete_index_files(target)
    except OSError:
        deleted = False
    if not deleted:
        raise IndexStoreError


def _delete_index_files(path: Path) -> None:
    path.unlink(missing_ok=True)
    path.with_name(path.name + "-journal").unlink(missing_ok=True)


def _pack(vector: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


_META_KEYS: Final = frozenset(
    {"embedding_model", "embedding_model_digest", "embedding_dim", "doc_prefix", "query_prefix"}
)


def _meta_for(profile: EmbeddingProfile) -> dict[str, str]:
    return {
        "embedding_model": profile.model,
        "embedding_model_digest": profile.model_digest,
        "embedding_dim": str(profile.dimension),
        "doc_prefix": profile.doc_prefix,
        "query_prefix": profile.query_prefix,
    }


def _check_profile(stored: dict[str, str], profile: EmbeddingProfile) -> None:
    wanted = _meta_for(profile)
    if stored.keys() != wanted.keys():
        raise IndexRebuildRequiredError
    differing = tuple(sorted(name for name, value in wanted.items() if stored[name] != value))
    if differing:
        raise IndexProfileMismatchError(differing)


def _inspect(path: Path) -> tuple[int, int, dict[str, str]]:
    """(user_version, table count, meta) of an existing file, opened READ-ONLY.

    The old file is never written to, so a refusal to open it leaves it byte-identical. A file
    that is not a SQLite database at all is never deleted automatically.
    """
    # The error is raised after the `except` block, so nothing is chained (see rebuild_index).
    con: sqlite3.Connection | None = None
    version, tables, meta = 0, 0, {}
    failed = False
    try:
        con = sqlite3.connect(f"{path.resolve(strict=True).as_uri()}?mode=ro", uri=True)
        version = int(con.execute("PRAGMA user_version").fetchone()[0])
        tables = int(con.execute("SELECT count(*) FROM sqlite_master").fetchone()[0])
        if version == SCHEMA_VERSION and tables:
            meta = {str(k): str(v) for k, v in con.execute("SELECT key, value FROM meta")}
    except (sqlite3.Error, OSError):
        failed = True
    finally:
        if con is not None:
            con.close()
    if failed:
        raise IndexRebuildRequiredError
    return version, tables, meta


def _connect(path: Path) -> sqlite3.Connection:
    """Open read-write with sqlite-vec loaded. Extension loading is on only while loading."""
    con = sqlite3.connect(path, isolation_level=None)  # explicit BEGIN/COMMIT; check_same_thread on
    try:
        failed = False
        try:
            con.enable_load_extension(True)
            sqlite_vec.load(con)
        except (sqlite3.Error, AttributeError):
            # AttributeError: this Python's sqlite3 was built without extension support.
            failed = True
        finally:
            with contextlib.suppress(sqlite3.Error, AttributeError):
                con.enable_load_extension(False)
        if failed:  # raised after the `except` block, so nothing is chained
            raise ExtensionUnavailableError
    except BaseException:
        con.close()
        raise
    return con


class _SqliteStore:
    """The synchronous core. Never call it from outside the worker thread."""

    def __init__(
        self, con: sqlite3.Connection, dimension: int, *, deletions_only: bool = False
    ) -> None:
        self._con = con
        self._dimension = dimension
        # An index opened with the profile stored in it (no embedding model involved): it may
        # delete, but never insert. `replace_document` and `re_resolve_links` check this flag.
        self.deletions_only = deletions_only

    # ----- open / create -------------------------------------------------------------------

    @classmethod
    def open_for_deletions(cls, path: Path) -> Self | None:
        """Open an existing index using the profile in its `meta` table; None if there is none.

        A file from another schema version, or with damaged `meta`, is never touched or deleted
        here: that is IndexRebuildRequiredError, and a human decides.
        """
        if path.is_dir():
            raise IndexStoreError
        if not path.exists():
            return None
        version, tables, stored = _inspect(path)
        if version == 0 and tables == 0:
            return None  # an empty file is not an index
        if version != SCHEMA_VERSION or stored.keys() != _META_KEYS:
            raise IndexRebuildRequiredError
        dimension = 0
        with contextlib.suppress(ValueError):
            dimension = int(stored["embedding_dim"])
        if dimension <= 0:
            raise IndexRebuildRequiredError
        return cls(_connect(path), dimension, deletions_only=True)

    @classmethod
    def open(cls, path: Path, profile: EmbeddingProfile, *, auto_rebuild: bool) -> Self:
        if path.is_dir():
            raise IndexStoreError
        if path.exists():
            version, tables, stored = _inspect(path)
            if version == 0 and tables == 0:
                return cls._create(path, profile)  # an empty file, e.g. a creation that never ran
            if version != SCHEMA_VERSION:
                if not auto_rebuild:
                    raise IndexRebuildRequiredError
                _delete_index_files(path)
                return cls._create(path, profile)
            _check_profile(stored, profile)
            return cls(_connect(path), profile.dimension)
        return cls._create(path, profile)

    @classmethod
    def _create(cls, path: Path, profile: EmbeddingProfile) -> Self:
        path.parent.mkdir(parents=True, exist_ok=True)
        store = cls(_connect(path), profile.dimension)
        try:
            with store._transaction():
                for statement in _SCHEMA:
                    store._con.execute(statement)
                store._con.execute(_VEC_DDL.replace("__DIM__", str(int(profile.dimension))))
                store._con.execute(_FTS_DDL)
                store._con.executemany(
                    "INSERT INTO meta (key, value) VALUES (?, ?)", _meta_for(profile).items()
                )
                store._con.execute(
                    _USER_VERSION_PRAGMA.replace("__VERSION__", str(int(SCHEMA_VERSION)))
                )
        except BaseException:
            store.close()
            raise
        return store

    # ----- transactions --------------------------------------------------------------------

    @contextlib.contextmanager
    def _transaction(self) -> Iterator[None]:
        """BEGIN IMMEDIATE ... COMMIT, or ROLLBACK if anything at all goes wrong."""
        self._con.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._con.execute("COMMIT")
        except BaseException:
            if self._con.in_transaction:
                self._con.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self._con.close()

    # ----- row deletion (shared by begin_sync, replace_document, remove_document) ----------

    def _delete_rows(self, source_path: str) -> None:
        """Every searchable row of one document: vectors, FTS rows, chunks, links.

        Only ever part of a larger transaction: outside one, each DELETE would commit by itself
        and a failure half way would leave the four tables disagreeing.
        """
        if not self._con.in_transaction:
            raise IndexStoreError("Rows can only be deleted inside a transaction")
        rowids = [
            (row[0],)
            for row in self._con.execute(
                "SELECT rowid FROM chunks WHERE source_path = ?", (source_path,)
            )
        ]
        self._con.executemany("DELETE FROM vec_chunks WHERE rowid = ?", rowids)
        self._con.executemany("DELETE FROM fts_chunks WHERE rowid = ?", rowids)
        self._con.execute("DELETE FROM chunks WHERE source_path = ?", (source_path,))
        self._con.execute("DELETE FROM links WHERE source_path = ?", (source_path,))

    # ----- the repository operations -------------------------------------------------------

    def document_states(self) -> dict[str, DocState]:
        rows = self._con.execute(
            "SELECT source_path, file_sha256, chunker_version, stale FROM documents"
        )
        return {
            str(path): DocState(
                file_sha256=str(sha), chunker_version=str(version), stale=bool(stale)
            )
            for path, sha, version, stale in rows
        }

    def begin_sync(self, scan: ScanSnapshot) -> StaleReport:
        removed: list[str] = []
        marked: list[str] = []
        with self._transaction():
            for path, state in sorted(self.document_states().items()):
                scanned_sha = scan.documents.get(path)
                if scanned_sha is None:
                    self._delete_rows(path)
                    self._con.execute("DELETE FROM documents WHERE source_path = ?", (path,))
                    removed.append(path)
                elif (
                    scanned_sha != state.file_sha256
                    or scan.chunker_version != state.chunker_version
                ) and not state.stale:
                    self._delete_rows(path)
                    self._con.execute(
                        "UPDATE documents SET stale = 1 WHERE source_path = ?", (path,)
                    )
                    marked.append(path)
        return StaleReport(removed=tuple(removed), marked_stale=tuple(marked))

    def replace_document(
        self,
        doc: IndexedDocument,
        chunks: Sequence[Chunk],
        vectors: Sequence[Sequence[float]],
        links: Sequence[RawLink],
    ) -> None:
        if self.deletions_only:
            raise IndexStoreError("Index is open for deletions only")
        if len(chunks) != len(vectors):
            raise IndexStoreError("Chunk and vector counts differ")
        if any(c.source_path != doc.source_path or c.doc_id != doc.doc_id for c in chunks) or any(
            link.source_path != doc.source_path for link in links
        ):
            raise IndexStoreError("Chunk or link does not belong to the document")
        with self._transaction():
            # Correct even when begin_sync did not run first: no old row can survive a replace.
            self._delete_rows(doc.source_path)
            self._con.execute(
                """INSERT INTO documents
                       (source_path, collection, doc_id, file_sha256, chunker_version, stale,
                        type, owner, review_by)
                   VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
                   ON CONFLICT (source_path) DO UPDATE SET
                       collection = excluded.collection, doc_id = excluded.doc_id,
                       file_sha256 = excluded.file_sha256,
                       chunker_version = excluded.chunker_version, stale = 0,
                       type = excluded.type, owner = excluded.owner,
                       review_by = excluded.review_by""",
                (
                    doc.source_path,
                    doc.collection,
                    doc.doc_id,
                    doc.file_sha256,
                    doc.chunker_version,
                    doc.type,
                    doc.owner,
                    doc.review_by,
                ),
            )
            for chunk, vector in zip(chunks, vectors, strict=True):
                if len(vector) != self._dimension:
                    raise VectorDimensionError
                cursor = self._con.execute(
                    """INSERT INTO chunks
                           (chunk_id, source_path, doc_id, start_line, end_line, heading_path,
                            text, text_sha256, flags)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        chunk.chunk_id,
                        chunk.source_path,
                        chunk.doc_id,
                        chunk.start_line,
                        chunk.end_line,
                        chunk.heading_path,
                        chunk.text,
                        chunk.text_sha256,
                        json.dumps(list(chunk.flags)),
                    ),
                )
                rowid = cursor.lastrowid
                if rowid is None:
                    raise IndexStoreError
                self._con.execute(
                    "INSERT INTO vec_chunks (rowid, embedding) VALUES (?, ?)",
                    (rowid, _pack(vector)),
                )
                self._con.execute(
                    "INSERT INTO fts_chunks (rowid, text, heading_path) VALUES (?, ?, ?)",
                    (rowid, chunk.text, chunk.heading_path),
                )
            self._con.executemany(
                """INSERT INTO links
                       (source_path, source_line, target, alias, fragment, is_embed, origin,
                        status, target_doc_id, code)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'unresolved', NULL, 'pending_resolution')""",
                [
                    (
                        link.source_path,
                        link.source_line,
                        link.target,
                        link.alias,
                        link.fragment,
                        int(link.is_embed),
                        link.origin,
                    )
                    for link in links
                ],
            )

    def remove_document(self, source_path: str) -> None:
        with self._transaction():
            self._delete_rows(source_path)
            self._con.execute("DELETE FROM documents WHERE source_path = ?", (source_path,))

    def re_resolve_links(self, known: ResolutionIndex) -> None:
        if self.deletions_only:
            raise IndexStoreError("Index is open for deletions only")
        rows = self._con.execute(
            "SELECT id, source_path, source_line, target, alias, fragment, is_embed, origin "
            "FROM links"
        ).fetchall()
        updates: list[tuple[str, str | None, str | None, int]] = []
        for link_id, source_path, line, target, alias, fragment, is_embed, origin in rows:
            raw = RawLink(
                source_path=source_path,
                source_line=line,
                target=target,
                alias=alias,
                fragment=fragment,
                is_embed=bool(is_embed),
                origin=origin,
            )
            resolved = resolve_link(raw, known)
            updates.append((resolved.status, resolved.target_doc_id, resolved.code, link_id))
        with self._transaction():
            self._con.executemany(
                "UPDATE links SET status = ?, target_doc_id = ?, code = ? WHERE id = ?", updates
            )

    def search(self, query: Sequence[float], k: int) -> list[ScoredChunk]:
        if not 1 <= k <= MAX_K:
            raise ValueError("k is out of range")
        if len(query) != self._dimension:
            raise VectorDimensionError
        rows = self._con.execute(
            "SELECT c.chunk_id, c.source_path, c.doc_id, c.start_line, c.end_line, "
            "c.heading_path, c.text, c.text_sha256, c.flags, v.distance "
            "FROM vec_chunks AS v JOIN chunks AS c ON c.rowid = v.rowid "
            "WHERE v.embedding MATCH ? AND k = ? ORDER BY v.distance",
            (_pack(query), k),
        )
        return [ScoredChunk(chunk=_chunk_from_row(row), distance=float(row[9])) for row in rows]

    def lexical_search(self, match: str, k: int) -> list[LexicalHit]:
        if not 1 <= k <= MAX_K:
            raise ValueError("k is out of range")
        if not match:
            return []
        # `match` is an FTS5 query built by retrieval/query.py (every word quoted) and is passed as
        # a bound parameter. bm25() is lower for a better match; chunk_id breaks ties, so the order
        # never depends on rowids or insertion order.
        rows = self._con.execute(
            "SELECT c.chunk_id, c.source_path, c.doc_id, c.start_line, c.end_line, "
            "c.heading_path, c.text, c.text_sha256, c.flags, bm25(fts_chunks) AS score "
            "FROM fts_chunks JOIN chunks AS c ON c.rowid = fts_chunks.rowid "
            "WHERE fts_chunks MATCH ? ORDER BY score, c.chunk_id LIMIT ?",
            (match, k),
        )
        return [LexicalHit(chunk=_chunk_from_row(row), bm25=float(row[9])) for row in rows]

    def chunks_for(self, source_path: str) -> list[Chunk]:
        rows = self._con.execute(
            "SELECT c.chunk_id, c.source_path, c.doc_id, c.start_line, c.end_line, "
            "c.heading_path, c.text, c.text_sha256, c.flags "
            "FROM chunks AS c WHERE c.source_path = ? ORDER BY c.start_line, c.end_line",
            (source_path,),
        )
        return [_chunk_from_row(row) for row in rows]

    def links_for(self, source_path: str) -> list[ResolvedLink]:
        rows = self._con.execute(
            "SELECT source_path, source_line, target, alias, fragment, is_embed, origin, "
            "status, target_doc_id, code FROM links WHERE source_path = ? ORDER BY id",
            (source_path,),
        )
        return [
            ResolvedLink(
                link=RawLink(
                    source_path=row[0],
                    source_line=row[1],
                    target=row[2],
                    alias=row[3],
                    fragment=row[4],
                    is_embed=bool(row[5]),
                    origin=row[6],
                ),
                status=row[7],
                target_doc_id=row[8],
                code=row[9],
            )
            for row in rows
        ]

    def verify_integrity(self) -> IntegrityReport:
        def ids(sql: str) -> set[int]:
            return {int(row[0]) for row in self._con.execute(sql)}

        def texts(sql: str) -> list[str]:
            return [str(row[0]) for row in self._con.execute(sql)]

        chunk_ids = ids("SELECT rowid FROM chunks")
        vec_ids = ids("SELECT rowid FROM vec_chunks")
        fts_ids = ids("SELECT rowid FROM fts_chunks")
        problems: list[IntegrityProblem] = []

        def report(code: Any, details: Sequence[object]) -> None:
            problems.extend(IntegrityProblem(code=code, detail=str(d)) for d in details)

        report("vec_without_chunk", sorted(vec_ids - chunk_ids))
        report("fts_without_chunk", sorted(fts_ids - chunk_ids))
        report("chunk_without_vec", sorted(chunk_ids - vec_ids))
        report("chunk_without_fts", sorted(chunk_ids - fts_ids))
        report(
            "chunk_without_document",
            texts(
                "SELECT DISTINCT source_path FROM chunks "
                "WHERE source_path NOT IN (SELECT source_path FROM documents) ORDER BY 1"
            ),
        )
        report(
            "link_without_document",
            texts(
                "SELECT DISTINCT source_path FROM links "
                "WHERE source_path NOT IN (SELECT source_path FROM documents) ORDER BY 1"
            ),
        )
        stale = texts("SELECT source_path FROM documents WHERE stale = 1 ORDER BY 1")
        holding_rows = texts(
            "SELECT source_path FROM documents WHERE stale = 1 AND ("
            "source_path IN (SELECT source_path FROM chunks) "
            "OR source_path IN (SELECT source_path FROM links)) ORDER BY 1"
        )
        report("rows_for_stale_document", holding_rows)
        return IntegrityReport(problems=tuple(problems), stale_documents=tuple(stale))


def _chunk_from_row(row: Sequence[Any]) -> Chunk:
    return Chunk(
        chunk_id=row[0],
        source_path=row[1],
        doc_id=row[2],
        start_line=row[3],
        end_line=row[4],
        heading_path=row[5],
        text=row[6],
        text_sha256=row[7],
        flags=tuple(json.loads(row[8])),
    )


class SqliteVecRepository:
    """The async facade. Every call runs on the single DB thread; sqlite3 errors never escape."""

    def __init__(self, path: Path, *, auto_rebuild: bool = True) -> None:
        self._path = path
        self._auto_rebuild = auto_rebuild
        self._worker = SqliteWorker()
        self._core: _SqliteStore | None = None

    async def _run(self, call: Callable[[], T]) -> T:
        failed = False
        try:
            return await self._worker.run(call)
        except GrcError:
            raise  # already a domain error (ExtensionLoadBlockedError is also a PermissionError)
        except (sqlite3.Error, OSError, ValidationError):
            # sqlite3 errors can quote SQL, OS errors a path, and a pydantic ValidationError the
            # value of the bad field (document text). ValidationError is a ValueError, so it is
            # caught here by name, while the plain ValueError for `k` passes through untouched.
            failed = True
        # Raised after the `except` block, never inside it: `raise ... from None` would only hide
        # the original from the traceback, and it would stay attached as __context__.
        if failed:
            raise IndexStoreError
        raise AssertionError  # pragma: no cover  (the try either returned or raised)

    def _store(self) -> _SqliteStore:
        if self._core is None:
            raise IndexStoreError("Index is not open")
        return self._core

    @property
    def mode(self) -> RepositoryMode:
        if self._core is None:
            return "closed"
        return "deletions_only" if self._core.deletions_only else "full"

    async def open_or_create(self, profile: EmbeddingProfile) -> None:
        if self._core is not None:
            if not self._core.deletions_only:
                raise IndexStoreError("Index is already open")
            # Upgrade: close the deletions-only connection (on the DB thread), open a full one.
            store, self._core = self._core, None
            await self._run(store.close)
        path, auto = self._path, self._auto_rebuild
        self._core = await self._run(lambda: _SqliteStore.open(path, profile, auto_rebuild=auto))

    async def open_for_deletions(self) -> bool:
        if self._core is not None:
            raise IndexStoreError("Index is already open")
        path = self._path
        self._core = await self._run(lambda: _SqliteStore.open_for_deletions(path))
        return self._core is not None

    async def document_states(self) -> dict[str, DocState]:
        return await self._run(self._store().document_states)

    async def begin_sync(self, scan: ScanSnapshot) -> StaleReport:
        store = self._store()
        return await self._run(lambda: store.begin_sync(scan))

    async def replace_document(
        self,
        doc: IndexedDocument,
        chunks: Sequence[Chunk],
        vectors: Sequence[Sequence[float]],
        links: Sequence[RawLink],
    ) -> None:
        store = self._store()
        await self._run(lambda: store.replace_document(doc, chunks, vectors, links))

    async def remove_document(self, source_path: str) -> None:
        store = self._store()
        await self._run(lambda: store.remove_document(source_path))

    async def re_resolve_links(self, known: ResolutionIndex) -> None:
        store = self._store()
        await self._run(lambda: store.re_resolve_links(known))

    async def search(self, query: Sequence[float], k: int) -> list[ScoredChunk]:
        store = self._store()
        return await self._run(lambda: store.search(query, k))

    async def lexical_search(self, match: str, k: int) -> list[LexicalHit]:
        store = self._store()
        return await self._run(lambda: store.lexical_search(match, k))

    async def chunks_for(self, source_path: str) -> list[Chunk]:
        store = self._store()
        return await self._run(lambda: store.chunks_for(source_path))

    async def links_for(self, source_path: str) -> list[ResolvedLink]:
        store = self._store()
        return await self._run(lambda: store.links_for(source_path))

    async def verify_integrity(self) -> IntegrityReport:
        return await self._run(self._store().verify_integrity)

    async def close(self) -> None:
        store, self._core = self._core, None
        try:
            if store is not None:
                await self._run(store.close)
        finally:
            self._worker.shutdown()
