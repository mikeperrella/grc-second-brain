"""Shared helpers for the index tests: small synthetic documents, vectors and raw SQL checks.

Tests open the SQLite file directly (with the sqlite-vec extension loaded) to count rows, so a
"rows are gone" assertion looks at the tables themselves and not at what the repository returns.
Everything here is synthetic.
"""

import hashlib
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import sqlite_vec

from grc_sidecar.repositories.base import EmbeddingProfile, IndexedDocument
from grc_sidecar.vault.models import Chunk, RawLink

DIM = 4

PROFILE = EmbeddingProfile(
    model="test-embed",
    model_digest="sha256:0000",
    dimension=DIM,
    doc_prefix="search_document: ",
    query_prefix="search_query: ",
)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_doc(
    source_path: str = "policies/access.md",
    *,
    doc_id: str = "POL-ACCESS",
    version: str = "v1",
    chunker_version: str = "1",
) -> IndexedDocument:
    """A document whose hash follows `version`, so a new version looks like an edited file."""
    return IndexedDocument(
        source_path=source_path,
        collection="vault",
        doc_id=doc_id,
        file_sha256=sha(f"{source_path}:{version}"),
        chunker_version=chunker_version,
        type="policy",
        owner="Test Owner",
        review_by="2028-01-01",
    )


def make_chunk(
    doc: IndexedDocument,
    start: int,
    text: str,
    *,
    heading: str = "Access Control",
    flags: tuple[str, ...] = (),
) -> Chunk:
    end = start + text.count("\n")
    return Chunk(
        chunk_id=sha(f"{doc.source_path}|{doc.doc_id}|{doc.file_sha256}|{start}|{end}"),
        source_path=doc.source_path,
        doc_id=doc.doc_id,
        start_line=start,
        end_line=end,
        heading_path=heading,
        text=text,
        text_sha256=sha(text),
        flags=flags,
    )


def make_link(source_path: str, target: str, line: int = 5) -> RawLink:
    return RawLink(source_path=source_path, source_line=line, target=target)


def axis(index: int) -> list[float]:
    """A unit vector along one axis: orthogonal to every other axis (cosine distance 1.0)."""
    vector = [0.0] * DIM
    vector[index] = 1.0
    return vector


def open_raw(path: Path) -> sqlite3.Connection:
    """A direct connection with sqlite-vec loaded, for counting rows behind the repository."""
    con = sqlite3.connect(path, isolation_level=None)
    con.enable_load_extension(True)
    sqlite_vec.load(con)
    con.enable_load_extension(False)
    return con


_COUNT_SQL = {
    "documents": "SELECT count(*) FROM documents",
    "chunks": "SELECT count(*) FROM chunks",
    "vec_chunks": "SELECT count(*) FROM vec_chunks",
    "fts_chunks": "SELECT count(*) FROM fts_chunks",
    "links": "SELECT count(*) FROM links",
}
_ROWID_SQL = {
    "chunks": "SELECT rowid FROM chunks",
    "vec_chunks": "SELECT rowid FROM vec_chunks",
    "fts_chunks": "SELECT rowid FROM fts_chunks",
}


def table_counts(path: Path) -> dict[str, int]:
    con = open_raw(path)
    try:
        return {name: int(con.execute(sql).fetchone()[0]) for name, sql in _COUNT_SQL.items()}
    finally:
        con.close()


def rowids(path: Path, table: str) -> set[int]:
    con = open_raw(path)
    try:
        return {int(r[0]) for r in con.execute(_ROWID_SQL[table])}
    finally:
        con.close()


def scalar(path: Path, sql: str, params: Sequence[object] = ()) -> object:
    con = open_raw(path)
    try:
        row = con.execute(sql, tuple(params)).fetchone()
        return None if row is None else row[0]
    finally:
        con.close()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
