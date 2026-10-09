"""The indexer end to end: a synthetic vault, a fake embedder, a real SQLite index.

Every assertion about "the rows are gone" counts rows with raw SQL (indexkit), not through the
repository's own read methods.
"""

import asyncio
import json
import threading
from pathlib import Path

import httpx
import pytest

from grc_sidecar.errors import (
    ConfigurationError,
    EmbeddingDimensionError,
    EmbeddingUnavailableError,
    IndexProfileMismatchError,
    ModelMissingError,
)
from grc_sidecar.repositories import sqlite_vec_repo
from grc_sidecar.repositories.sqlite_vec_repo import SqliteVecRepository
from grc_sidecar.repositories.worker import THREAD_NAME_PREFIX
from grc_sidecar.vault import indexer as indexer_module
from grc_sidecar.vault.indexer import Indexer

from .fakes import FakeEmbeddingClient
from .indexkit import (
    assert_document_has_no_rows,
    assert_no_orphans,
    rows_for,
    stale_flag,
    sync_once,
    sync_with,
    write_doc,
)
from .repokit import file_sha256, scalar, table_counts
from .test_embeddings_ollama import FakeOllama, make_client
from .vaultkit import SAMPLE_VAULT


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    return tmp_path / "vault"


@pytest.fixture
def index(tmp_path: Path) -> Path:
    return tmp_path / "index" / "i.sqlite3"


def chunk_texts(index: Path) -> list[str]:
    from .repokit import open_raw

    con = open_raw(index)
    try:
        return [str(r[0]) for r in con.execute("SELECT text FROM chunks ORDER BY rowid")]
    finally:
        con.close()


# ----- the sample vault --------------------------------------------------------------------


def test_the_sample_vault_indexes_with_no_errors(index: Path) -> None:
    fake = FakeEmbeddingClient()
    report = sync_once(SAMPLE_VAULT, index, fake)
    assert report.complete and report.failed == ()
    assert report.issues == ()
    assert report.documents_seen == 6
    assert len(report.indexed) == 6
    counts = table_counts(index)
    assert counts["documents"] == 6
    assert counts["chunks"] == report.chunks_embedded > 0
    assert counts["vec_chunks"] == counts["fts_chunks"] == counts["chunks"]
    assert_no_orphans(index)


def test_links_in_the_sample_vault_are_resolved_after_a_sync(index: Path) -> None:
    async def scenario() -> None:
        report, repo = await sync_with(SAMPLE_VAULT, index, FakeEmbeddingClient())
        try:
            assert report.complete
            links = await repo.links_for("access-control-and-mfa-policy.md")
            by_target = {link.link.target: link for link in links}
            assert by_target["mfa-enrollment-procedure"].status == "resolved"
            assert by_target["mfa-enrollment-procedure"].target_doc_id == "HR-PRC-001"
            assert (await repo.verify_integrity()).ok
        finally:
            await repo.close()

    asyncio.run(scenario())


# ----- change-driven re-indexing -----------------------------------------------------------


def test_a_second_sync_embeds_nothing(vault: Path, index: Path) -> None:
    write_doc(vault, "a.md", "# One\nalpha text\n")
    write_doc(vault, "b.md", "# Two\nbeta text\n")
    fake = FakeEmbeddingClient()
    first = sync_once(vault, index, fake)
    embedded = fake.texts_embedded
    second = sync_once(vault, index, fake)
    assert first.indexed == ("a.md", "b.md") and embedded > 0
    assert second.indexed == () and second.unchanged == 2 and second.complete
    assert fake.texts_embedded == embedded  # nothing was sent to the embedder
    assert second.marked_stale == () and second.removed == ()


def test_edit_leaves_no_stale_chunks(vault: Path, index: Path) -> None:  # [NEG]
    write_doc(vault, "a.md", "# One\nzebrafish retention rule\n")
    write_doc(vault, "b.md", "# Two\nbeta text\n")
    fake = FakeEmbeddingClient()
    sync_once(vault, index, fake)
    before = len(fake.document_calls)

    write_doc(vault, "a.md", "# One\nquokka retention rule\n")
    report = sync_once(vault, index, fake)

    assert report.indexed == ("a.md",) and report.unchanged == 1
    assert len(fake.document_calls) == before + 1  # only a.md went to the embedder
    texts = chunk_texts(index)
    assert any("quokka" in t for t in texts)
    assert not any("zebrafish" in t for t in texts)  # the old text is gone, not just hidden
    assert_no_orphans(index)
    assert stale_flag(index, "a.md") == 0


def test_a_deleted_file_loses_every_row(vault: Path, index: Path) -> None:  # [NEG]
    write_doc(vault, "a.md", "# One\nalpha\n", links=["b"])
    write_doc(vault, "b.md", "# Two\nbeta\n")
    fake = FakeEmbeddingClient()
    sync_once(vault, index, fake)
    (vault / "a.md").unlink()

    report = sync_once(vault, index, fake)

    assert report.removed == ("a.md",)
    assert rows_for(index, "a.md") == {"chunks": 0, "links": 0, "documents": 0}
    assert_no_orphans(index)
    assert rows_for(index, "b.md")["chunks"] > 0


def test_a_renamed_file_is_indexed_under_the_new_path_only(vault: Path, index: Path) -> None:
    write_doc(vault, "old-name.md", "# One\nalpha\n", doc_id="DOC-1")
    fake = FakeEmbeddingClient()
    sync_once(vault, index, fake)
    (vault / "old-name.md").rename(vault / "new-name.md")

    report = sync_once(vault, index, fake)

    assert report.removed == ("old-name.md",) and report.indexed == ("new-name.md",)
    assert rows_for(index, "old-name.md") == {"chunks": 0, "links": 0, "documents": 0}
    assert rows_for(index, "new-name.md")["chunks"] > 0
    assert_no_orphans(index)


def test_a_frontmatter_only_change_changes_every_chunk_id_and_re_embeds(
    vault: Path, index: Path
) -> None:
    write_doc(vault, "a.md", "# One\nalpha\n\n# Two\nbeta\n", owner="First Owner")
    fake = FakeEmbeddingClient()
    sync_once(vault, index, fake)
    ids_before = _chunk_ids(index)
    embedded_before = fake.texts_embedded

    write_doc(vault, "a.md", "# One\nalpha\n\n# Two\nbeta\n", owner="Second Owner")
    report = sync_once(vault, index, fake)

    assert report.indexed == ("a.md",)  # the file hash changed, so the file is re-embedded
    assert fake.texts_embedded > embedded_before
    ids_after = _chunk_ids(index)
    assert len(ids_after) == len(ids_before) > 0
    assert ids_after.isdisjoint(ids_before)  # the hash is part of every chunk ID (CLAUDE.md 8.1)


def _chunk_ids(index: Path) -> set[str]:
    from .repokit import open_raw

    con = open_raw(index)
    try:
        return {str(r[0]) for r in con.execute("SELECT chunk_id FROM chunks")}
    finally:
        con.close()


def test_a_previously_good_file_that_becomes_malformed_is_excluded(
    vault: Path, index: Path
) -> None:  # [NEG]
    write_doc(vault, "a.md", "# One\nalpha\n")
    write_doc(vault, "b.md", "# Two\nbeta\n")
    fake = FakeEmbeddingClient()
    sync_once(vault, index, fake)
    (vault / "a.md").write_text("no frontmatter at all\n", encoding="utf-8")

    report = sync_once(vault, index, fake)

    assert report.removed == ("a.md",)
    assert [issue.code for issue in report.issues] == ["missing_frontmatter"]
    assert rows_for(index, "a.md") == {"chunks": 0, "links": 0, "documents": 0}
    assert_no_orphans(index)


def test_duplicate_doc_id_excludes_all_copies(vault: Path, index: Path) -> None:  # [NEG] row 11
    write_doc(vault, "a.md", "# One\nalpha rule\n", doc_id="SAME-ID")
    write_doc(vault, "b.md", "# Two\nbeta\n", doc_id="OTHER-ID")
    fake = FakeEmbeddingClient()
    sync_once(vault, index, fake)
    assert rows_for(index, "a.md")["chunks"] > 0

    write_doc(vault, "copy-of-a.md", "# Copy\ngamma rule\n", doc_id="SAME-ID")
    report = sync_once(vault, index, fake)

    codes = sorted((issue.source_path or "", issue.code) for issue in report.issues)
    assert codes == [("a.md", "duplicate_doc_id"), ("copy-of-a.md", "duplicate_doc_id")]
    # "First one wins" would depend on directory order, so NEITHER copy is served:
    assert rows_for(index, "a.md") == {"chunks": 0, "links": 0, "documents": 0}
    assert rows_for(index, "copy-of-a.md") == {"chunks": 0, "links": 0, "documents": 0}
    assert not any("rule" in text for text in chunk_texts(index))
    assert rows_for(index, "b.md")["chunks"] > 0
    assert_no_orphans(index)


def test_a_new_chunker_version_re_indexes_every_document(
    vault: Path, index: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_doc(vault, "a.md", "# One\nalpha\n")
    write_doc(vault, "b.md", "# Two\nbeta\n")
    fake = FakeEmbeddingClient()
    sync_once(vault, index, fake)
    embedded = fake.texts_embedded

    monkeypatch.setattr(indexer_module, "CHUNKER_VERSION", "999")
    report = sync_once(vault, index, fake)

    assert report.indexed == ("a.md", "b.md") and report.unchanged == 0
    assert fake.texts_embedded == 2 * embedded
    assert scalar(index, "SELECT count(*) FROM documents WHERE chunker_version = '999'") == 2


def test_a_document_with_no_chunks_is_indexed_with_no_rows(vault: Path, index: Path) -> None:
    write_doc(vault, "empty.md", "")
    report = sync_once(vault, index, FakeEmbeddingClient())
    assert report.indexed == ("empty.md",)
    assert rows_for(index, "empty.md") == {"chunks": 0, "links": 0, "documents": 1}


# ----- links follow documents --------------------------------------------------------------


def test_deleting_target_unresolves_inbound_links(vault: Path, index: Path) -> None:  # [NEG] row 17
    write_doc(vault, "a.md", "# One\nSee [[b]] for details.\n")
    write_doc(vault, "b.md", "# Two\nbeta\n")
    fake = FakeEmbeddingClient()

    async def statuses() -> dict[str, str]:
        repo = SqliteVecRepository(index)
        try:
            await Indexer(repo, fake, vault).sync()
            return {link.link.target: link.status for link in await repo.links_for("a.md")}
        finally:
            await repo.close()

    assert asyncio.run(statuses()) == {"b": "resolved"}
    (vault / "b.md").unlink()
    # a.md is unchanged, so it is not re-embedded: only the link resolution can notice b is gone.
    assert asyncio.run(statuses()) == {"b": "unresolved"}
    write_doc(vault, "b.md", "# Two\nbeta\n")
    assert asyncio.run(statuses()) == {"b": "resolved"}


# ----- fail closed before anything is written ----------------------------------------------


def test_a_missing_model_stops_before_any_database_write(vault: Path, index: Path) -> None:  # [NEG]
    write_doc(vault, "a.md", "# One\nalpha\n")
    with pytest.raises(ModelMissingError):
        sync_once(vault, index, FakeEmbeddingClient(model_missing=True))
    assert not index.exists() and not index.parent.exists()


def test_a_missing_model_leaves_an_existing_index_byte_identical(
    vault: Path, index: Path
) -> None:  # [NEG]
    write_doc(vault, "a.md", "# One\nalpha\n")
    sync_once(vault, index, FakeEmbeddingClient())
    before = file_sha256(index)
    write_doc(vault, "a.md", "# One\nchanged\n")  # a change a sync would have acted on
    with pytest.raises(ModelMissingError):
        sync_once(vault, index, FakeEmbeddingClient(model_missing=True))
    assert file_sha256(index) == before


def test_probe_dimension_mismatch_aborts_before_any_db_write(
    vault: Path, index: Path
) -> None:  # [NEG] mutation: dimension check
    write_doc(vault, "a.md", "# One\nalpha\n")
    fake = FakeOllama(dimension=5)  # the model answers with 5 numbers; the settings expect 4
    client = make_client(fake, expected_dimension=4)
    with pytest.raises(EmbeddingDimensionError):
        sync_once(vault, index, client)
    assert not index.exists() and not index.parent.exists()


def test_a_different_model_digest_is_refused_and_changes_nothing(
    vault: Path, index: Path
) -> None:  # [NEG]
    write_doc(vault, "a.md", "# One\nalpha\n")
    sync_once(vault, index, FakeEmbeddingClient(digest="sha256:one"))
    before = file_sha256(index)
    with pytest.raises(IndexProfileMismatchError) as caught:
        sync_once(vault, index, FakeEmbeddingClient(digest="sha256:two"))
    assert caught.value.fields == ("embedding_model_digest",)
    assert file_sha256(index) == before


def test_an_unusable_vault_folder_is_a_configuration_error_and_writes_nothing(
    tmp_path: Path, index: Path
) -> None:  # [NEG]
    with pytest.raises(ConfigurationError):
        sync_once(tmp_path / "no-such-vault", index, FakeEmbeddingClient())
    assert not index.exists() and not index.parent.exists()


def test_a_timeout_during_the_run_aborts_it_with_a_typed_error(
    vault: Path, index: Path
) -> None:  # [NEG]
    write_doc(vault, "a.md", "# One\nalpha\n")
    write_doc(vault, "b.md", "# Two\nbeta\n")
    fake = FakeOllama()
    client = make_client(fake)
    sync_once(vault, index, client)  # a good first run through the real client

    write_doc(vault, "a.md", "# One\nalpha changed\n")
    write_doc(vault, "b.md", "# Two\nbeta changed\n")

    def slow(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["input"][0].endswith("dimension probe"):
            texts = json.loads(request.content)["input"]
            return httpx.Response(200, json={"embeddings": [[0.1] * 4 for _ in texts]})
        raise httpx.ReadTimeout("slow")  # every real document batch times out

    fake.embed_response = slow
    with pytest.raises(EmbeddingUnavailableError):
        sync_once(vault, index, make_client(fake))
    # Both edited files were marked stale first, so neither is served from its old rows.
    for name in ("a.md", "b.md"):
        assert stale_flag(index, name) == 1
        assert_document_has_no_rows(index, name)


# ----- reports -----------------------------------------------------------------------------


def test_a_report_never_contains_document_text(vault: Path, index: Path) -> None:
    secret = "CONFIDENTIAL POLICY TEXT"
    write_doc(vault, "a.md", f"# One\n{secret}\n")
    write_doc(vault, "b.md", f"# Two\n{secret} EMBEDFAIL\n")
    report = sync_once(vault, index, FakeEmbeddingClient(fail_marker="EMBEDFAIL"))
    assert [f.source_path for f in report.failed] == ["b.md"] and not report.complete
    assert secret not in report.model_dump_json()


def test_one_files_embedding_failure_does_not_stop_the_others(vault: Path, index: Path) -> None:
    write_doc(vault, "a.md", "# One\nalpha\n")
    write_doc(vault, "b.md", "# Two\nEMBEDFAIL beta\n")
    write_doc(vault, "c.md", "# Three\ngamma\n")
    report = sync_once(vault, index, FakeEmbeddingClient(fail_marker="EMBEDFAIL"))
    assert report.indexed == ("a.md", "c.md")
    assert [(f.source_path, f.code) for f in report.failed] == [("b.md", "embedding")]
    assert rows_for(index, "b.md")["chunks"] == 0
    assert_no_orphans(index)


# ----- concurrency -------------------------------------------------------------------------


def test_sync_runs_under_asyncio_and_every_sqlite_call_is_on_the_db_thread(
    vault: Path, index: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_doc(vault, "a.md", "# One\nalpha\n")
    seen: list[tuple[int, str]] = []
    store_class = sqlite_vec_repo._SqliteStore
    for name in ("begin_sync", "replace_document", "document_states", "re_resolve_links"):
        original = getattr(store_class, name)

        def make_wrapper(target: object) -> object:
            def wrapper(self: object, *args: object, **kwargs: object) -> object:
                seen.append((threading.get_ident(), threading.current_thread().name))
                return target(self, *args, **kwargs)  # type: ignore[operator]

            return wrapper

        monkeypatch.setattr(store_class, name, make_wrapper(original))

    loop_thread: list[int] = []

    async def scenario() -> None:
        loop_thread.append(threading.get_ident())
        repo = SqliteVecRepository(index)
        try:
            await Indexer(repo, FakeEmbeddingClient(), vault).sync()
        finally:
            await repo.close()

    asyncio.run(scenario())
    assert len(seen) >= 4
    assert len({ident for ident, _ in seen}) == 1 and seen[0][0] != loop_thread[0]
    assert all(name.startswith(THREAD_NAME_PREFIX) for _, name in seen)
