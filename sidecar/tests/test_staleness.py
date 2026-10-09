"""No stale evidence survives (ADR-009 invariant, plan Section 6).

For every case the rows are counted with raw SQL (the rows are GONE, not hidden), and `search()`
and `chunks_for()` are checked too. Embedding failures, an abort and a crash are injected with the
fake embedder.
"""

import asyncio
from pathlib import Path

import pytest

from grc_sidecar.errors import EmbeddingUnavailableError, IndexRebuildRequiredError, IndexStoreError
from grc_sidecar.repositories.base import IndexedDocument
from grc_sidecar.repositories.sqlite_vec_repo import SqliteVecRepository
from grc_sidecar.vault.indexer import Indexer
from grc_sidecar.vault.links import ResolutionIndex

from .fakes import FakeEmbeddingClient, SimulatedCrash
from .indexkit import (
    assert_document_has_no_rows,
    assert_no_orphans,
    rows_for,
    stale_flag,
    sync_once,
    sync_with,
    write_doc,
)
from .repokit import file_sha256, make_chunk, make_doc, open_raw, scalar
from .test_schema_version import make_old_index


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    return tmp_path / "vault"


@pytest.fixture
def index(tmp_path: Path) -> Path:
    return tmp_path / "index" / "i.sqlite3"


def test_embed_failure_old_chunk_not_returned_by_search(vault: Path, index: Path) -> None:  # [NEG]
    # mutation row 18: begin_sync's row deletes
    write_doc(vault, "a.md", "# One\nzebrafish retention rule\n")
    write_doc(vault, "b.md", "# Two\nbeta text\n")
    fake = FakeEmbeddingClient(fail_marker="EMBEDFAIL")
    sync_once(vault, index, fake)

    write_doc(vault, "a.md", "# One\nquokka retention rule EMBEDFAIL\n")  # its embed will fail
    write_doc(vault, "b.md", "# Two\nbeta text, now with more words\n")  # its embed succeeds

    async def scenario() -> None:
        report, repo = await sync_with(vault, index, fake)
        try:
            assert not report.complete
            assert [f.source_path for f in report.failed] == ["a.md"]
            assert report.indexed == ("b.md",)
            # At the database level: stale, and not one row left behind for the old a.md.
            assert stale_flag(index, "a.md") == 1
            assert_document_has_no_rows(index, "a.md")
            # At the API level: the old chunk can be neither searched nor read.
            query = await fake.embed_query("zebrafish retention rule")
            hits = await repo.search(query, k=10)
            assert "a.md" not in {hit.chunk.source_path for hit in hits}
            assert not any("zebrafish" in hit.chunk.text for hit in hits)
            assert await repo.chunks_for("a.md") == []
            assert any("more words" in c.text for c in await repo.chunks_for("b.md"))
            integrity = await repo.verify_integrity()
            assert integrity.ok and integrity.stale_documents == ("a.md",)
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_a_connection_abort_mid_run_leaves_the_unfinished_documents_stale(
    vault: Path, index: Path
) -> None:  # [NEG]
    for name in ("a.md", "b.md", "c.md"):
        write_doc(vault, name, f"# {name}\nold text of {name}\n")
    sync_once(vault, index, FakeEmbeddingClient())
    for name in ("a.md", "b.md", "c.md"):
        write_doc(vault, name, f"# {name}\nnew text of {name}\n")

    # Ollama answers once (file 1) and then goes away.
    flaky = FakeEmbeddingClient(fail_after_calls=1, fail_mode="unavailable")
    with pytest.raises(EmbeddingUnavailableError):
        sync_once(vault, index, flaky)

    assert stale_flag(index, "a.md") == 0 and rows_for(index, "a.md")["chunks"] > 0
    for name in ("b.md", "c.md"):
        assert stale_flag(index, name) == 1
        assert_document_has_no_rows(index, name)
    assert any("new text of a.md" in c for c in _texts(index))
    assert not any("old text" in c for c in _texts(index))


def _texts(index: Path) -> list[str]:
    con = open_raw(index)
    try:
        return [str(r[0]) for r in con.execute("SELECT text FROM chunks")]
    finally:
        con.close()


def test_a_simulated_crash_then_reopen_still_has_no_stale_rows(
    vault: Path, index: Path
) -> None:  # [NEG]
    write_doc(vault, "a.md", "# One\nzebrafish rule\n")
    write_doc(vault, "b.md", "# Two\nbeta\n")
    sync_once(vault, index, FakeEmbeddingClient())
    write_doc(vault, "a.md", "# One\nquokka rule CRASH\n")

    # The process "dies" (a BaseException) while embedding, AFTER begin_sync committed.
    crashing = FakeEmbeddingClient(fail_marker="CRASH", fail_mode="crash")
    with pytest.raises(SimulatedCrash):
        sync_once(vault, index, crashing)

    # Reopen the same file as a new process would: the edited file is stale with no rows.
    async def reopen() -> None:
        repo = SqliteVecRepository(index)
        try:
            profile = await FakeEmbeddingClient().ensure_ready()
            await repo.open_or_create(profile)
            assert (await repo.document_states())["a.md"].stale
            assert await repo.chunks_for("a.md") == []
            assert (await repo.verify_integrity()).ok
        finally:
            await repo.close()

    asyncio.run(reopen())
    assert stale_flag(index, "a.md") == 1
    assert_document_has_no_rows(index, "a.md")
    assert not any("zebrafish" in c for c in _texts(index))


def test_stale_rows_cannot_starve_a_search(vault: Path, index: Path) -> None:  # [NEG]
    # vec0 returns its top k before any filter. If stale rows were merely hidden, the nearest rows
    # (the edited documents') would fill k and the search would come back empty.
    fake = FakeEmbeddingClient(fail_marker="EMBEDFAIL")
    near = "\n\n".join(f"## Part {n}\nalpha beta gamma delta {n}" for n in range(6))
    for name in ("e1.md", "e2.md", "e3.md"):
        write_doc(vault, name, near)
    for name in ("s1.md", "s2.md"):
        write_doc(vault, name, "## Other\nunrelated words about backups\n\n## More\nnightly jobs\n")
    sync_once(vault, index, fake)

    async def search_sources(repo: SqliteVecRepository) -> set[str]:
        vector = await fake.embed_query("alpha beta gamma delta")
        return {hit.chunk.source_path for hit in await repo.search(vector, k=3)}

    async def before() -> set[str]:
        repo = SqliteVecRepository(index)
        try:
            await repo.open_for_deletions()
            return await search_sources(repo)
        finally:
            await repo.close()

    assert asyncio.run(before()) <= {"e1.md", "e2.md", "e3.md"}  # the edited ones are nearest

    for name in ("e1.md", "e2.md", "e3.md"):
        write_doc(vault, name, "## Changed\nEMBEDFAIL completely different words\n")
    sync_once(vault, index, fake)  # their embeds fail: stale, no rows

    async def after() -> list[str]:
        repo = SqliteVecRepository(index)
        try:
            await repo.open_for_deletions()
            vector = await fake.embed_query("alpha beta gamma delta")
            return sorted(hit.chunk.source_path for hit in await repo.search(vector, k=3))
        finally:
            await repo.close()

    hits = asyncio.run(after())
    assert len(hits) == 3  # still a full answer...
    assert set(hits) <= {"s1.md", "s2.md"}  # ...and none of it from the stale documents


def test_refresh_staleness_alone_deletes_rows_with_ollama_down(
    vault: Path, index: Path
) -> None:  # [NEG]
    write_doc(vault, "a.md", "# One\nzebrafish rule\n")
    write_doc(vault, "b.md", "# Two\nbeta\n")
    write_doc(vault, "c.md", "# Three\ngamma\n")
    sync_once(vault, index, FakeEmbeddingClient())
    write_doc(vault, "a.md", "# One\nquokka rule\n")  # edited since the last sync
    (vault / "c.md").unlink()  # deleted since the last sync

    down = FakeEmbeddingClient(unreachable=True)  # Ollama is not reachable at all

    async def scenario() -> None:
        repo = SqliteVecRepository(index)
        try:
            report = await Indexer(repo, down, vault).refresh_staleness()
            assert report.index_found
            assert report.marked_stale == ("a.md",) and report.removed == ("c.md",)
            assert repo.mode == "deletions_only"
            assert await repo.chunks_for("a.md") == []
        finally:
            await repo.close()

    asyncio.run(scenario())
    assert down.ready_calls == 0 and down.document_calls == []  # the embedder was never touched
    assert stale_flag(index, "a.md") == 1
    assert_document_has_no_rows(index, "a.md")
    assert rows_for(index, "c.md") == {"chunks": 0, "links": 0, "documents": 0}
    assert rows_for(index, "b.md")["chunks"] > 0  # the unchanged file keeps its rows


def test_refresh_staleness_without_an_index_does_nothing_and_says_so(
    vault: Path, index: Path
) -> None:
    write_doc(vault, "a.md", "# One\nalpha\n")

    async def scenario() -> None:
        repo = SqliteVecRepository(index)
        try:
            report = await Indexer(
                repo, FakeEmbeddingClient(unreachable=True), vault
            ).refresh_staleness()
            assert not report.index_found
            assert report.removed == () and report.marked_stale == ()
            assert repo.mode == "closed"
        finally:
            await repo.close()

    asyncio.run(scenario())
    assert not index.exists() and not index.parent.exists()  # it created nothing


def test_refresh_staleness_never_touches_an_index_from_another_schema_version(
    vault: Path, index: Path
) -> None:  # [NEG]
    index.parent.mkdir(parents=True)
    make_old_index(index, 1)
    before = file_sha256(index)

    async def scenario() -> None:
        repo = SqliteVecRepository(index)
        try:
            await Indexer(repo, FakeEmbeddingClient(unreachable=True), vault).refresh_staleness()
        finally:
            await repo.close()

    write_doc(vault, "a.md", "# One\nalpha\n")
    with pytest.raises(IndexRebuildRequiredError):
        asyncio.run(scenario())
    assert file_sha256(index) == before  # not rebuilt, not deleted: a human decides


def test_a_deletions_only_index_refuses_every_insert(vault: Path, index: Path) -> None:  # [NEG]
    write_doc(vault, "a.md", "# One\nalpha\n")
    sync_once(vault, index, FakeEmbeddingClient())
    doc = make_doc("x.md", doc_id="X")

    async def scenario() -> None:
        repo = SqliteVecRepository(index)
        try:
            assert await repo.open_for_deletions()
            before = scalar(index, "SELECT count(*) FROM chunks")
            with pytest.raises(IndexStoreError, match="deletions only"):
                await repo.replace_document(
                    IndexedDocument(**doc.model_dump()),
                    [make_chunk(doc, 5, "x")],
                    [[0.0] * 768],
                    [],
                )
            with pytest.raises(IndexStoreError, match="deletions only"):
                await repo.re_resolve_links(ResolutionIndex([("a.md", "A")]))
            assert scalar(index, "SELECT count(*) FROM chunks") == before
            await repo.remove_document("a.md")  # deleting is allowed
        finally:
            await repo.close()

    asyncio.run(scenario())
    assert rows_for(index, "a.md") == {"chunks": 0, "links": 0, "documents": 0}


def test_a_later_successful_sync_clears_stale_and_returns_the_new_content(
    vault: Path, index: Path
) -> None:
    write_doc(vault, "a.md", "# One\nzebrafish rule\n")
    sync_once(vault, index, FakeEmbeddingClient(fail_marker="EMBEDFAIL"))
    write_doc(vault, "a.md", "# One\nquokka rule EMBEDFAIL\n")
    failed = sync_once(vault, index, FakeEmbeddingClient(fail_marker="EMBEDFAIL"))
    assert not failed.complete and stale_flag(index, "a.md") == 1

    write_doc(vault, "a.md", "# One\nquokka rule, fixed\n")
    healthy = FakeEmbeddingClient()
    report = sync_once(vault, index, healthy)

    assert report.complete and report.indexed == ("a.md",)
    assert stale_flag(index, "a.md") == 0
    texts = _texts(index)
    assert any("quokka rule, fixed" in t for t in texts)
    assert not any("zebrafish" in t for t in texts)
    assert_no_orphans(index)


def test_refresh_then_sync_on_one_repository_object_upgrades_it_to_full_mode(
    vault: Path, index: Path
) -> None:
    write_doc(vault, "a.md", "# One\nalpha\n")
    sync_once(vault, index, FakeEmbeddingClient())
    write_doc(vault, "a.md", "# One\nalpha changed\n")
    fake = FakeEmbeddingClient()

    async def scenario() -> None:
        repo = SqliteVecRepository(index)
        try:
            indexer = Indexer(repo, fake, vault)
            await indexer.refresh_staleness()
            assert repo.mode == "deletions_only" and await repo.chunks_for("a.md") == []
            report = await indexer.sync()
            assert repo.mode == "full" and report.indexed == ("a.md",)
            assert any("alpha changed" in c.text for c in await repo.chunks_for("a.md"))
        finally:
            await repo.close()

    asyncio.run(scenario())
