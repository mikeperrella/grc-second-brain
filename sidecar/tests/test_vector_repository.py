"""The SQLite repository: one transaction per change, rows deleted (never hidden), fail closed.

Row counts are read from the tables directly (repokit), not from the repository, so "the rows
are gone" means gone from `chunks`, `vec_chunks`, `fts_chunks` and `links`.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from grc_sidecar.errors import (
    ExtensionLoadBlockedError,
    IndexProfileMismatchError,
    IndexStoreError,
    PathNotAllowedError,
    VectorDimensionError,
)
from grc_sidecar.repositories.base import (
    EmbeddingProfile,
    IndexedDocument,
    ScanSnapshot,
    VectorRepository,
)
from grc_sidecar.repositories.sqlite_vec_repo import (
    SCHEMA_VERSION,
    SqliteVecRepository,
    rebuild_index,
    vault_index_path,
)
from grc_sidecar.vault.links import ResolutionIndex
from grc_sidecar.vault.models import Chunk

from .repokit import (
    DIM,
    PROFILE,
    axis,
    file_sha256,
    make_chunk,
    make_doc,
    make_link,
    open_raw,
    rowids,
    scalar,
    table_counts,
)


@pytest.fixture
def index_path(tmp_path: Path) -> Path:
    return tmp_path / "index" / "test.sqlite3"  # the folder does not exist yet


@asynccontextmanager
async def opened(
    path: Path, profile: EmbeddingProfile = PROFILE, *, auto_rebuild: bool = True
) -> AsyncIterator[SqliteVecRepository]:
    repo = SqliteVecRepository(path, auto_rebuild=auto_rebuild)
    try:
        await repo.open_or_create(profile)
        yield repo
    finally:
        await repo.close()


def snapshot(*docs: IndexedDocument, chunker_version: str = "1") -> ScanSnapshot:
    return ScanSnapshot(
        chunker_version=chunker_version,
        documents={d.source_path: d.file_sha256 for d in docs},
    )


def test_the_sqlite_repository_satisfies_the_protocol(index_path: Path) -> None:
    repo: VectorRepository = SqliteVecRepository(index_path)
    assert repo is not None


def test_a_new_index_is_created_with_the_current_schema_and_profile(index_path: Path) -> None:
    async def scenario() -> None:
        async with opened(index_path):
            pass

    asyncio.run(scenario())
    assert scalar(index_path, "PRAGMA user_version") == SCHEMA_VERSION
    assert (
        scalar(index_path, "SELECT value FROM meta WHERE key = 'embedding_model'") == "test-embed"
    )
    assert scalar(index_path, "SELECT value FROM meta WHERE key = 'embedding_dim'") == str(DIM)
    assert scalar(index_path, "SELECT value FROM meta WHERE key = 'doc_prefix'") == (
        PROFILE.doc_prefix
    )
    assert table_counts(index_path) == dict.fromkeys(
        ("documents", "chunks", "vec_chunks", "fts_chunks", "links"), 0
    )


def test_replace_then_search_returns_the_nearest_chunk_first(index_path: Path) -> None:
    doc = make_doc()
    near = make_chunk(doc, 5, "Accounts are reviewed quarterly.")
    far = make_chunk(doc, 9, "Backups run nightly.", heading="Backups")

    async def scenario() -> None:
        async with opened(index_path) as repo:
            await repo.replace_document(doc, [near, far], [axis(0), axis(1)], [])
            hits = await repo.search([1.0, 0.1, 0.0, 0.0], k=2)
            assert [h.chunk for h in hits] == [near, far]
            assert hits[0].distance < hits[1].distance
            assert await repo.chunks_for(doc.source_path) == [near, far]
            states = await repo.document_states()
            assert states[doc.source_path].file_sha256 == doc.file_sha256
            assert not states[doc.source_path].stale

    asyncio.run(scenario())


def test_cosine_distance_is_zero_for_the_same_direction_and_one_for_orthogonal(
    index_path: Path,
) -> None:
    doc = make_doc()
    a = make_chunk(doc, 5, "a")
    b = make_chunk(doc, 7, "b")

    async def scenario() -> None:
        async with opened(index_path) as repo:
            await repo.replace_document(doc, [a, b], [axis(0), axis(1)], [])
            hits = await repo.search([2.0, 0.0, 0.0, 0.0], k=2)  # length does not matter
            assert [round(h.distance, 6) for h in hits] == [0.0, 1.0]

    asyncio.run(scenario())


def test_chunk_flags_survive_a_round_trip(index_path: Path) -> None:
    doc = make_doc()
    chunk = make_chunk(doc, 5, "x", flags=("split_at_line", "embedding_truncated"))

    async def scenario() -> None:
        async with opened(index_path) as repo:
            await repo.replace_document(doc, [chunk], [axis(0)], [])
            assert (await repo.chunks_for(doc.source_path))[0].flags == chunk.flags

    asyncio.run(scenario())


def test_replace_document_twice_without_begin_sync_leaves_no_old_rows(
    index_path: Path,
) -> None:  # [NEG] mutation row 5
    old_doc = make_doc(version="v1")
    old_chunks = [make_chunk(old_doc, 5, "old text one"), make_chunk(old_doc, 9, "old text two")]
    old_links = [make_link(old_doc.source_path, "old-a"), make_link(old_doc.source_path, "old-b")]
    new_doc = make_doc(version="v2")
    new_chunk = make_chunk(new_doc, 5, "new text")
    new_links = [make_link(new_doc.source_path, "new-a")]

    async def scenario() -> None:
        async with opened(index_path) as repo:
            await repo.replace_document(old_doc, old_chunks, [axis(0), axis(1)], old_links)
            assert table_counts(index_path)["chunks"] == 2
            # No begin_sync in between: replace_document alone must remove every old row.
            await repo.replace_document(new_doc, [new_chunk], [axis(2)], new_links)

    asyncio.run(scenario())
    assert table_counts(index_path) == {
        "documents": 1,
        "chunks": 1,
        "vec_chunks": 1,
        "fts_chunks": 1,
        "links": 1,
    }
    surviving = rowids(index_path, "chunks")
    assert rowids(index_path, "vec_chunks") == surviving
    assert rowids(index_path, "fts_chunks") == surviving
    assert scalar(index_path, "SELECT text FROM chunks") == "new text"
    assert scalar(index_path, "SELECT target FROM links") == "new-a"
    assert scalar(index_path, "SELECT file_sha256 FROM documents") == new_doc.file_sha256


def test_failed_replace_keeps_previous_state(index_path: Path) -> None:  # [NEG] mutation row 6
    doc = make_doc(version="v1")
    kept = [make_chunk(doc, 5, "kept one"), make_chunk(doc, 9, "kept two")]
    kept_links = [make_link(doc.source_path, "kept-link")]
    edited = make_doc(version="v2")
    replacement = [make_chunk(edited, 5, "new one"), make_chunk(edited, 9, "new two")]

    async def scenario() -> None:
        async with opened(index_path) as repo:
            await repo.replace_document(doc, kept, [axis(0), axis(1)], kept_links)
            before = table_counts(index_path)
            # The second vector has the wrong length, so the failure happens after the old rows
            # were deleted and the first new row was inserted: only a rollback restores them.
            with pytest.raises(VectorDimensionError):
                await repo.replace_document(edited, replacement, [axis(0), [1.0, 2.0]], [])
            assert table_counts(index_path) == before
            assert await repo.chunks_for(doc.source_path) == kept
            assert [link.link.target for link in await repo.links_for(doc.source_path)] == [
                "kept-link"
            ]
            assert (await repo.document_states())[doc.source_path].file_sha256 == doc.file_sha256
            assert (await repo.verify_integrity()).ok
            hits = await repo.search(axis(0), k=5)
            assert {h.chunk.text for h in hits} == {"kept one", "kept two"}

    asyncio.run(scenario())


def test_a_duplicate_chunk_id_rolls_back_and_raises_a_typed_error(index_path: Path) -> None:
    doc = make_doc()
    chunk = make_chunk(doc, 5, "same id twice")

    async def scenario() -> None:
        async with opened(index_path) as repo:
            with pytest.raises(IndexStoreError):
                await repo.replace_document(doc, [chunk, chunk], [axis(0), axis(1)], [])
            assert table_counts(index_path)["chunks"] == 0
            assert table_counts(index_path)["documents"] == 0

    asyncio.run(scenario())


def test_chunks_from_another_document_are_refused(index_path: Path) -> None:  # [NEG]
    doc = make_doc()
    other = make_doc("policies/other.md", doc_id="POL-OTHER")

    async def scenario() -> None:
        async with opened(index_path) as repo:
            with pytest.raises(IndexStoreError):
                await repo.replace_document(doc, [make_chunk(other, 5, "x")], [axis(0)], [])
            with pytest.raises(IndexStoreError):
                await repo.replace_document(
                    doc, [make_chunk(doc, 5, "x")], [axis(0)], [make_link("other.md", "t")]
                )
            with pytest.raises(IndexStoreError):  # more chunks than vectors
                await repo.replace_document(doc, [make_chunk(doc, 5, "x")], [], [])
            assert table_counts(index_path)["documents"] == 0

    asyncio.run(scenario())


def test_dimension_mismatch_fails_closed(index_path: Path) -> None:  # [NEG] mutation row 12
    async def create() -> None:
        async with opened(index_path):
            pass

    asyncio.run(create())
    before = file_sha256(index_path)
    bigger = PROFILE.model_copy(update={"dimension": DIM * 2})

    async def reopen() -> None:
        async with opened(index_path, bigger):
            pass

    with pytest.raises(IndexProfileMismatchError) as caught:
        asyncio.run(reopen())
    assert caught.value.fields == ("embedding_dim",)
    assert file_sha256(index_path) == before  # nothing was written


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"model": "other-embed"}, "embedding_model"),
        ({"model_digest": "sha256:1111"}, "embedding_model_digest"),
        ({"doc_prefix": ""}, "doc_prefix"),
        ({"query_prefix": ""}, "query_prefix"),
    ],
)
def test_any_profile_difference_fails_closed_and_names_only_the_field(
    index_path: Path, change: dict[str, object], field: str
) -> None:  # [NEG]
    async def create() -> None:
        async with opened(index_path):
            pass

    asyncio.run(create())
    before = file_sha256(index_path)

    async def reopen() -> None:
        async with opened(index_path, PROFILE.model_copy(update=change)):
            pass

    with pytest.raises(IndexProfileMismatchError) as caught:
        asyncio.run(reopen())
    assert caught.value.fields == (field,)
    assert str(caught.value) == "Index was built with a different embedding profile"
    assert file_sha256(index_path) == before


def test_reopening_with_the_same_profile_keeps_the_data(index_path: Path) -> None:
    doc = make_doc()
    chunk = make_chunk(doc, 5, "persisted")

    async def first() -> None:
        async with opened(index_path) as repo:
            await repo.replace_document(doc, [chunk], [axis(0)], [])

    async def second() -> None:
        async with opened(index_path) as repo:
            assert await repo.chunks_for(doc.source_path) == [chunk]
            assert [h.chunk for h in await repo.search(axis(0), k=1)] == [chunk]

    asyncio.run(first())
    asyncio.run(second())


# ----- begin_sync: deletion, not filtering ------------------------------------------------


async def _seed(
    repo: SqliteVecRepository, *docs_and_chunks: tuple[IndexedDocument, list[Chunk]]
) -> None:
    for index, (doc, chunks) in enumerate(docs_and_chunks):
        links = [make_link(doc.source_path, f"target-{index}")]
        await repo.replace_document(doc, chunks, [axis(i % DIM) for i in range(len(chunks))], links)


def test_begin_sync_deletes_a_changed_documents_rows_in_all_four_tables(
    index_path: Path,
) -> None:  # [NEG]
    edited = make_doc("a.md", doc_id="A")
    steady = make_doc("b.md", doc_id="B")

    async def scenario() -> None:
        async with opened(index_path) as repo:
            await _seed(
                repo,
                (edited, [make_chunk(edited, 5, "a one"), make_chunk(edited, 9, "a two")]),
                (steady, [make_chunk(steady, 5, "b one")]),
            )
            now_edited = make_doc("a.md", doc_id="A", version="v2")
            report = await repo.begin_sync(snapshot(now_edited, steady))
            assert report.marked_stale == ("a.md",)
            assert report.removed == ()

    asyncio.run(scenario())
    assert table_counts(index_path) == {
        "documents": 2,  # the edited document's row is kept, marked stale
        "chunks": 1,
        "vec_chunks": 1,
        "fts_chunks": 1,
        "links": 1,
    }
    assert scalar(index_path, "SELECT stale FROM documents WHERE source_path = 'a.md'") == 1
    assert scalar(index_path, "SELECT stale FROM documents WHERE source_path = 'b.md'") == 0
    assert scalar(index_path, "SELECT count(*) FROM chunks WHERE source_path = 'a.md'") == 0
    assert scalar(index_path, "SELECT count(*) FROM links WHERE source_path = 'a.md'") == 0
    assert rowids(index_path, "vec_chunks") == rowids(index_path, "chunks")
    assert rowids(index_path, "fts_chunks") == rowids(index_path, "chunks")


def test_begin_sync_removes_documents_that_are_gone_entirely(index_path: Path) -> None:
    gone = make_doc("gone.md", doc_id="GONE")
    kept = make_doc("kept.md", doc_id="KEPT")

    async def scenario() -> None:
        async with opened(index_path) as repo:
            await _seed(
                repo,
                (gone, [make_chunk(gone, 5, "gone text")]),
                (kept, [make_chunk(kept, 5, "kept text")]),
            )
            report = await repo.begin_sync(snapshot(kept))
            assert report.removed == ("gone.md",)
            assert set(await repo.document_states()) == {"kept.md"}

    asyncio.run(scenario())
    assert table_counts(index_path) == {
        "documents": 1,
        "chunks": 1,
        "vec_chunks": 1,
        "fts_chunks": 1,
        "links": 1,
    }


def test_a_new_chunker_version_marks_every_document_stale(index_path: Path) -> None:
    one = make_doc("one.md", doc_id="ONE")
    two = make_doc("two.md", doc_id="TWO")

    async def scenario() -> None:
        async with opened(index_path) as repo:
            await _seed(
                repo,
                (one, [make_chunk(one, 5, "one")]),
                (two, [make_chunk(two, 5, "two")]),
            )
            report = await repo.begin_sync(snapshot(one, two, chunker_version="2"))
            assert report.marked_stale == ("one.md", "two.md")
            assert table_counts(index_path)["chunks"] == 0

    asyncio.run(scenario())


def test_an_unchanged_document_is_left_alone(index_path: Path) -> None:
    doc = make_doc()
    chunk = make_chunk(doc, 5, "unchanged")

    async def scenario() -> None:
        async with opened(index_path) as repo:
            await repo.replace_document(doc, [chunk], [axis(0)], [])
            report = await repo.begin_sync(snapshot(doc))
            assert report.removed == () and report.marked_stale == ()
            assert await repo.chunks_for(doc.source_path) == [chunk]

    asyncio.run(scenario())


def test_search_never_returns_a_stale_documents_chunk_and_is_not_starved(
    index_path: Path,
) -> None:  # [NEG]
    # vec0 returns its top k before any outer filter. If stale rows were merely hidden, the
    # nearest rows (all from the edited document) would fill k and the answer would be empty.
    edited = make_doc("edited.md", doc_id="EDITED")
    steady = make_doc("steady.md", doc_id="STEADY")
    edited_chunks = [make_chunk(edited, 5 + 4 * i, f"edited chunk {i}") for i in range(6)]
    steady_chunks = [make_chunk(steady, 5 + 4 * i, f"steady chunk {i}") for i in range(4)]
    near = [1.0, 0.0, 0.0, 0.0]

    async def scenario() -> None:
        async with opened(index_path) as repo:
            await repo.replace_document(
                edited, edited_chunks, [[1.0, 0.01 * i, 0.0, 0.0] for i in range(6)], []
            )
            await repo.replace_document(steady, steady_chunks, [axis(1)] * 4, [])
            assert {h.chunk.source_path for h in await repo.search(near, k=3)} == {"edited.md"}
            await repo.begin_sync(
                snapshot(make_doc("edited.md", doc_id="EDITED", version="v2"), steady)
            )
            hits = await repo.search(near, k=3)
            assert len(hits) == 3
            assert {h.chunk.source_path for h in hits} == {"steady.md"}
            assert await repo.chunks_for("edited.md") == []

    asyncio.run(scenario())


def test_verify_integrity_reports_a_stale_document_without_calling_it_corrupt(
    index_path: Path,
) -> None:
    doc = make_doc()

    async def scenario() -> None:
        async with opened(index_path) as repo:
            await _seed(repo, (doc, [make_chunk(doc, 5, "x")]))
            assert (await repo.verify_integrity()).stale_documents == ()
            await repo.begin_sync(snapshot(make_doc(version="v2")))
            report = await repo.verify_integrity()
            assert report.stale_documents == (doc.source_path,)
            assert report.ok

    asyncio.run(scenario())


def test_verify_integrity_flags_rows_that_belong_to_a_stale_document(
    index_path: Path,
) -> None:  # [NEG]
    doc = make_doc()

    async def scenario() -> None:
        async with opened(index_path) as repo:
            await _seed(repo, (doc, [make_chunk(doc, 5, "x")]))
            await repo.begin_sync(snapshot(make_doc(version="v2")))
            con = open_raw(index_path)
            con.execute(
                "INSERT INTO links (source_path, source_line, target, is_embed, origin, status) "
                "VALUES (?, 1, 't', 0, 'body', 'unresolved')",
                (doc.source_path,),
            )
            con.close()
            report = await repo.verify_integrity()
            assert not report.ok
            assert [p.code for p in report.problems] == ["rows_for_stale_document"]
            assert report.problems[0].detail == doc.source_path

    asyncio.run(scenario())


def test_verify_integrity_detects_every_kind_of_orphan(index_path: Path) -> None:  # [NEG]
    doc = make_doc()

    async def scenario() -> None:
        async with opened(index_path) as repo:
            await _seed(repo, (doc, [make_chunk(doc, 5, "x"), make_chunk(doc, 9, "y")]))
            assert (await repo.verify_integrity()).ok
            con = open_raw(index_path)
            rowid = int(con.execute("SELECT min(rowid) FROM chunks").fetchone()[0])
            con.execute("DELETE FROM fts_chunks WHERE rowid = ?", (rowid,))
            con.execute("DELETE FROM vec_chunks WHERE rowid = ?", (rowid + 1,))
            con.execute("INSERT INTO fts_chunks (rowid, text, heading_path) VALUES (900, 'o', 'o')")
            con.execute("DELETE FROM documents")
            con.close()
            codes = sorted(p.code for p in (await repo.verify_integrity()).problems)
            assert codes == sorted(
                [
                    "chunk_without_fts",
                    "chunk_without_vec",
                    "fts_without_chunk",
                    "chunk_without_document",
                    "link_without_document",
                ]
            )

    asyncio.run(scenario())


# ----- remove, links, validation ----------------------------------------------------------


def test_a_deleted_chunks_rowid_is_never_reused(index_path: Path) -> None:  # [NEG]
    # vec_chunks and fts_chunks share chunks.rowid. If a rowid were reused, a leftover row from
    # a bug would be adopted by the next chunk and no longer look like an orphan.
    first = make_doc(version="v1")
    second = make_doc(version="v2")
    other = make_doc("other.md", doc_id="OTHER")
    seen: list[set[int]] = []

    async def scenario() -> None:
        async with opened(index_path) as repo:
            await repo.replace_document(
                first,
                [make_chunk(first, 5, "one"), make_chunk(first, 9, "two")],
                [axis(0), axis(1)],
                [],
            )
            seen.append(rowids(index_path, "chunks"))
            await repo.replace_document(second, [make_chunk(second, 5, "three")], [axis(2)], [])
            seen.append(rowids(index_path, "chunks"))
            await repo.remove_document(second.source_path)  # the table is now empty
            await repo.replace_document(other, [make_chunk(other, 5, "four")], [axis(3)], [])
            seen.append(rowids(index_path, "chunks"))

    asyncio.run(scenario())
    everything = [rowid for step in seen for rowid in step]
    assert len(everything) == len(set(everything)) == 4, "a rowid was handed out twice"
    assert min(seen[1]) > max(seen[0])  # strictly increasing, never refilled
    assert min(seen[2]) > max(seen[1])  # even after the table was emptied


def test_a_leftover_vector_row_stays_visible_as_an_orphan_after_new_chunks(
    index_path: Path,
) -> None:  # [NEG]
    old = make_doc(version="v1")
    new = make_doc(version="v2")

    async def scenario() -> None:
        async with opened(index_path) as repo:
            await repo.replace_document(old, [make_chunk(old, 5, "old")], [axis(0)], [])
            stray = min(rowids(index_path, "chunks"))
            await repo.replace_document(new, [make_chunk(new, 5, "new")], [axis(1)], [])
            # A bug (simulated) leaves a vector behind under the first, long-deleted rowid.
            con = open_raw(index_path)
            con.execute(
                "INSERT INTO vec_chunks (rowid, embedding) VALUES (?, ?)",
                (stray, b"\x00\x00\x80\x3f" + b"\x00" * 12),
            )
            con.close()
            report = await repo.verify_integrity()
            assert [(p.code, p.detail) for p in report.problems] == [
                ("vec_without_chunk", str(stray))
            ]
            # ...and the next chunk does not silently adopt it.
            third = make_doc(version="v3")
            await repo.replace_document(third, [make_chunk(third, 5, "third")], [axis(2)], [])
            assert [p.code for p in (await repo.verify_integrity()).problems] == [
                "vec_without_chunk"
            ]

    asyncio.run(scenario())


def test_remove_document_deletes_every_row_including_the_documents_row(index_path: Path) -> None:
    doc = make_doc()

    async def scenario() -> None:
        async with opened(index_path) as repo:
            await _seed(repo, (doc, [make_chunk(doc, 5, "x")]))
            await repo.remove_document(doc.source_path)
            await repo.remove_document("never-existed.md")  # a no-op, not an error

    asyncio.run(scenario())
    assert table_counts(index_path) == dict.fromkeys(
        ("documents", "chunks", "vec_chunks", "fts_chunks", "links"), 0
    )


def test_links_are_pending_until_resolved_then_follow_the_documents(index_path: Path) -> None:
    source = make_doc("source.md", doc_id="SRC")
    links = [
        make_link("source.md", "target"),
        make_link("source.md", "../../outside"),
        make_link("source.md", "nowhere"),
    ]

    async def scenario() -> None:
        async with opened(index_path) as repo:
            await repo.replace_document(source, [make_chunk(source, 5, "s")], [axis(0)], links)
            before = await repo.links_for("source.md")
            assert {(r.status, r.code) for r in before} == {("unresolved", "pending_resolution")}

            both = ResolutionIndex([("source.md", "SRC"), ("target.md", "TGT")])
            await repo.re_resolve_links(both)
            resolved = {
                r.link.target: (r.status, r.target_doc_id)
                for r in await repo.links_for("source.md")
            }
            assert resolved == {
                "target": ("resolved", "TGT"),
                "../../outside": ("rejected", None),
                "nowhere": ("unresolved", None),
            }

            # The target disappears: the inbound link must stop pointing at it.
            await repo.re_resolve_links(ResolutionIndex([("source.md", "SRC")]))
            after = {r.link.target: r.status for r in await repo.links_for("source.md")}
            assert after["target"] == "unresolved"

    asyncio.run(scenario())


def test_search_validates_its_arguments(index_path: Path) -> None:  # [NEG]
    async def scenario() -> None:
        async with opened(index_path) as repo:
            assert await repo.search(axis(0), k=3) == []  # an empty index is not an error
            with pytest.raises(VectorDimensionError):
                await repo.search([1.0, 0.0], k=3)
            for bad_k in (0, -1, 100_000):
                with pytest.raises(ValueError, match="k is out of range"):
                    await repo.search(axis(0), k=bad_k)

    asyncio.run(scenario())


def test_using_the_repository_before_open_or_after_close_is_an_error(
    index_path: Path,
) -> None:  # [NEG]
    async def scenario() -> None:
        repo = SqliteVecRepository(index_path)
        with pytest.raises(IndexStoreError):
            await repo.document_states()
        await repo.open_or_create(PROFILE)
        with pytest.raises(IndexStoreError):
            await repo.open_or_create(PROFILE)
        await repo.close()
        with pytest.raises(IndexStoreError):
            await repo.search(axis(0), k=1)

    asyncio.run(scenario())


def test_sqlite_errors_never_escape_the_repository(index_path: Path) -> None:  # [NEG]
    async def scenario() -> None:
        async with opened(index_path) as repo:
            con = open_raw(index_path)
            con.execute("DROP TABLE links")  # simulate a damaged file
            con.close()
            with pytest.raises(IndexStoreError) as caught:
                await repo.links_for("x.md")
            assert str(caught.value) == "Index store error"
            assert caught.value.__cause__ is None
            assert caught.value.__context__ is None

    asyncio.run(scenario())


# ----- file naming and the explicit rebuild -----------------------------------------------


def test_the_index_file_is_keyed_to_the_vault(tmp_path: Path) -> None:
    index_dir = tmp_path / "index"
    vault_a = tmp_path / "vault-a"
    vault_b = tmp_path / "vault-b"
    vault_a.mkdir()
    vault_b.mkdir()
    path_a = vault_index_path(index_dir, vault_a)
    assert path_a == vault_index_path(index_dir, vault_a)
    assert path_a != vault_index_path(index_dir, vault_b)
    assert path_a.parent == index_dir
    assert path_a.name.startswith("vault-") and path_a.suffix == ".sqlite3"
    assert len(path_a.stem) == len("vault-") + 16


def test_rebuild_index_deletes_only_the_named_file_inside_the_folder(tmp_path: Path) -> None:
    folder = tmp_path / "index"
    folder.mkdir()
    target = folder / "vault-aaaa.sqlite3"
    target.write_bytes(b"old")
    journal = folder / "vault-aaaa.sqlite3-journal"
    journal.write_bytes(b"j")
    other = folder / "packs.sqlite3"
    other.write_bytes(b"keep")
    precious = tmp_path / "precious.sqlite3"
    precious.write_bytes(b"keep")

    rebuild_index(folder, "vault-aaaa.sqlite3")

    assert not target.exists() and not journal.exists()
    assert other.read_bytes() == b"keep" and precious.read_bytes() == b"keep"


@pytest.mark.parametrize(
    "name",
    ["../precious.sqlite3", "sub/x.sqlite3", "..\\x.sqlite3", "notes.md", "", "x.sqlite3.bak"],
)
def test_rebuild_index_refuses_anything_but_a_plain_index_file_name(
    tmp_path: Path, name: str
) -> None:  # [NEG]
    folder = tmp_path / "index"
    folder.mkdir()
    precious = tmp_path / "precious.sqlite3"
    precious.write_bytes(b"keep")
    with pytest.raises(PathNotAllowedError):
        rebuild_index(folder, name)
    assert precious.read_bytes() == b"keep"


def test_rebuild_index_refuses_a_missing_folder(tmp_path: Path) -> None:  # [NEG]
    with pytest.raises(PathNotAllowedError) as caught:
        rebuild_index(tmp_path / "no-such-folder", "x.sqlite3")
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_rebuild_index_turns_an_os_error_into_a_clean_domain_error(tmp_path: Path) -> None:  # [NEG]
    folder = tmp_path / "index"
    folder.mkdir()
    (folder / "x.sqlite3").mkdir()  # a folder where the file should be: unlink fails on every OS
    with pytest.raises(IndexStoreError) as caught:
        rebuild_index(folder, "x.sqlite3")
    assert_clean_error(caught.value, str(tmp_path))


# ----- error mapping: domain errors only, and nothing chained --------------------------------


def assert_clean_error(error: BaseException, *secrets: str) -> None:
    """A fixed-message IndexStoreError that carries none of the original error."""
    assert type(error) is IndexStoreError
    assert str(error) == "Index store error"
    for secret in secrets:
        assert secret not in str(error)
        assert secret not in repr(error)
    # `raise ... from None` would leave __context__ set (and the text reachable in a traceback).
    assert error.__cause__ is None
    assert error.__context__ is None


def test_a_file_in_the_folder_path_becomes_a_clean_index_store_error(
    tmp_path: Path,
) -> None:  # [NEG]
    (tmp_path / "afile").write_text("x", encoding="utf-8")

    async def scenario() -> None:
        repo = SqliteVecRepository(tmp_path / "afile" / "sub" / "i.sqlite3")
        try:
            await repo.open_or_create(PROFILE)  # mkdir fails with a raw FileExistsError
        finally:
            await repo.close()

    with pytest.raises(IndexStoreError) as caught:
        asyncio.run(scenario())
    assert_clean_error(caught.value, str(tmp_path), "afile")


SENTINEL = "CONFIDENTIAL POLICY TEXT"


def test_a_corrupt_row_becomes_a_clean_error_without_leaking_its_value(
    index_path: Path,
) -> None:  # [NEG] mutation: ValidationError mapping
    doc = make_doc()

    async def scenario() -> None:
        async with opened(index_path) as repo:
            await repo.replace_document(doc, [make_chunk(doc, 5, "x")], [axis(0)], [])
            con = open_raw(index_path)
            con.execute("UPDATE chunks SET start_line = ?", (SENTINEL,))  # wrong type in a column
            con.close()
            # Both read paths build a Chunk, and pydantic's error message quotes the bad value.
            with pytest.raises(IndexStoreError) as from_chunks_for:
                await repo.chunks_for(doc.source_path)
            assert_clean_error(from_chunks_for.value, SENTINEL)
            with pytest.raises(IndexStoreError) as from_search:
                await repo.search(axis(0), k=1)
            assert_clean_error(from_search.value, SENTINEL)

    asyncio.run(scenario())


def test_a_domain_error_raised_on_the_worker_is_not_remapped(
    index_path: Path,
) -> None:  # [NEG] mutation: GrcError re-raise
    # ExtensionLoadBlockedError is a PermissionError, and so an OSError: it must arrive as itself.
    def blocked() -> None:
        raise ExtensionLoadBlockedError

    async def scenario() -> None:
        repo = SqliteVecRepository(index_path)
        try:
            with pytest.raises(ExtensionLoadBlockedError):
                await repo._run(blocked)
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_deleting_rows_outside_a_transaction_is_refused(index_path: Path) -> None:  # [NEG]
    doc = make_doc()

    async def scenario() -> None:
        async with opened(index_path) as repo:
            await repo.replace_document(doc, [make_chunk(doc, 5, "x")], [axis(0)], [])
            store = repo._store()
            # On the worker thread (the connection belongs to it), but with no transaction open.
            with pytest.raises(IndexStoreError, match="inside a transaction"):
                await repo._worker.run(lambda: store._delete_rows(doc.source_path))
            assert table_counts(index_path)["chunks"] == 1  # nothing was deleted

    asyncio.run(scenario())
