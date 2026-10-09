"""Helpers for the indexer tests: build a tiny synthetic vault, run a sync, count rows."""

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import TypeVar

from grc_sidecar.llm.embeddings import EmbeddingClient
from grc_sidecar.repositories.sqlite_vec_repo import SqliteVecRepository
from grc_sidecar.vault.indexer import Indexer, SyncReport

from .repokit import open_raw

T = TypeVar("T")


def frontmatter(doc_id: str, links: Sequence[str] = (), owner: str = "Test Owner") -> str:
    lines = ["---", f"doc_id: {doc_id}", "type: policy", f"owner: {owner}", "review_by: 2028-01-01"]
    if links:
        lines.append("links: [" + ", ".join(f'"[[{target}]]"' for target in links) + "]")
    lines.append("---")
    return "\n".join(lines) + "\n"


def write_doc(
    vault: Path,
    name: str,
    body: str,
    *,
    doc_id: str | None = None,
    links: Sequence[str] = (),
    owner: str = "Test Owner",
) -> Path:
    """A valid synthetic vault file. `doc_id` defaults to the file name in upper case."""
    vault.mkdir(parents=True, exist_ok=True)
    path = vault / name
    identifier = doc_id or name.removesuffix(".md").upper()
    path.write_text(frontmatter(identifier, links, owner) + body, encoding="utf-8", newline="\n")
    return path


async def sync_with(
    vault: Path, index: Path, embedder: EmbeddingClient
) -> tuple[SyncReport, SqliteVecRepository]:
    """One sync with a fresh repository (like one process run). The caller closes the repo."""
    repo = SqliteVecRepository(index)
    report = await Indexer(repo, embedder, vault).sync()
    return report, repo


def sync_once(vault: Path, index: Path, embedder: EmbeddingClient) -> SyncReport:
    async def scenario() -> SyncReport:
        repo = SqliteVecRepository(index)
        try:
            return await Indexer(repo, embedder, vault).sync()
        finally:
            await repo.close()

    return asyncio.run(scenario())


def stale_flag(index: Path, source_path: str) -> object:
    con = open_raw(index)
    try:
        row = con.execute(
            "SELECT stale FROM documents WHERE source_path = ?", (source_path,)
        ).fetchone()
        return None if row is None else row[0]
    finally:
        con.close()


def rows_for(index: Path, source_path: str) -> dict[str, int]:
    """Row counts for one document, counted with raw SQL (not through the repository)."""
    con = open_raw(index)
    try:
        sql = "SELECT count(*) FROM chunks WHERE source_path = ?"
        chunks = int(con.execute(sql, (source_path,)).fetchone()[0])
        sql = "SELECT count(*) FROM links WHERE source_path = ?"
        links = int(con.execute(sql, (source_path,)).fetchone()[0])
        sql = "SELECT count(*) FROM documents WHERE source_path = ?"
        documents = int(con.execute(sql, (source_path,)).fetchone()[0])
        return {"chunks": chunks, "links": links, "documents": documents}
    finally:
        con.close()


def assert_no_orphans(index: Path) -> None:
    """vec_chunks and fts_chunks hold exactly the rowids that chunks holds, no more, no fewer."""
    con = open_raw(index)
    try:
        chunk_ids = {int(r[0]) for r in con.execute("SELECT rowid FROM chunks")}
        vec_ids = {int(r[0]) for r in con.execute("SELECT rowid FROM vec_chunks")}
        fts_ids = {int(r[0]) for r in con.execute("SELECT rowid FROM fts_chunks")}
    finally:
        con.close()
    assert vec_ids == chunk_ids
    assert fts_ids == chunk_ids


def assert_document_has_no_rows(index: Path, source_path: str) -> None:
    """The rows are GONE (not hidden): none for the document in any searchable table."""
    assert rows_for(index, source_path)["chunks"] == 0
    assert rows_for(index, source_path)["links"] == 0
    assert_no_orphans(index)  # so no vector or FTS row is left behind for its old chunks
