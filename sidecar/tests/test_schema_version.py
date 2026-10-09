"""Schema versioning (ADR-009): the index is disposable, so a mismatch means rebuild."""

import asyncio
import sqlite3
from pathlib import Path

import pytest

from grc_sidecar.errors import IndexRebuildRequiredError
from grc_sidecar.repositories.sqlite_vec_repo import SCHEMA_VERSION, SqliteVecRepository

from .repokit import PROFILE, axis, file_sha256, make_chunk, make_doc, scalar, table_counts
from .test_vector_repository import opened


@pytest.fixture
def index_path(tmp_path: Path) -> Path:
    return tmp_path / "index.sqlite3"


def make_old_index(path: Path, version: int) -> None:
    """A file from "another schema version", holding a sentinel table that must not survive."""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE sentinel (note TEXT)")
    con.execute("INSERT INTO sentinel VALUES ('old index')")
    con.execute(f"PRAGMA user_version = {int(version)}")
    con.commit()
    con.close()


@pytest.mark.parametrize("old_version", [SCHEMA_VERSION - 1, SCHEMA_VERSION + 1, 99])
def test_version_mismatch_rebuilds_without_writing_old_file(
    index_path: Path, old_version: int
) -> None:  # [NEG] mutation row 19
    make_old_index(index_path, old_version)
    before = file_sha256(index_path)

    # auto_rebuild=False: refuse, and leave the old file byte-identical.
    async def refuse() -> None:
        async with opened(index_path, auto_rebuild=False):
            pass

    with pytest.raises(IndexRebuildRequiredError):
        asyncio.run(refuse())
    assert file_sha256(index_path) == before

    # auto_rebuild=True: a fresh index at the current version, with nothing from the old file.
    async def rebuild() -> None:
        async with opened(index_path, auto_rebuild=True):
            pass

    asyncio.run(rebuild())
    assert scalar(index_path, "PRAGMA user_version") == SCHEMA_VERSION
    assert scalar(index_path, "SELECT count(*) FROM sqlite_master WHERE name = 'sentinel'") == 0
    assert table_counts(index_path)["chunks"] == 0


def test_a_current_index_is_not_rebuilt(index_path: Path) -> None:
    doc = make_doc()

    async def first() -> None:
        async with opened(index_path) as repo:
            await repo.replace_document(doc, [make_chunk(doc, 5, "keep me")], [axis(0)], [])

    async def second() -> None:
        async with opened(index_path, auto_rebuild=True) as repo:
            assert len(await repo.chunks_for(doc.source_path)) == 1

    asyncio.run(first())
    asyncio.run(second())


def test_an_empty_file_is_treated_as_new(index_path: Path) -> None:
    index_path.write_bytes(b"")

    async def scenario() -> None:
        async with opened(index_path, auto_rebuild=False):
            pass

    asyncio.run(scenario())
    assert scalar(index_path, "PRAGMA user_version") == SCHEMA_VERSION


@pytest.mark.parametrize("auto_rebuild", [True, False])
def test_a_file_that_is_not_a_database_is_never_deleted(
    index_path: Path, auto_rebuild: bool
) -> None:  # [NEG]
    index_path.write_bytes(b"this is somebody's notes, not a SQLite database " * 10)
    before = file_sha256(index_path)

    async def scenario() -> None:
        async with opened(index_path, auto_rebuild=auto_rebuild):
            pass

    with pytest.raises(IndexRebuildRequiredError) as caught:
        asyncio.run(scenario())
    assert file_sha256(index_path) == before
    # The sqlite3 error ("file is not a database", with the path) is not attached to ours.
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert str(index_path) not in str(caught.value)


def test_current_version_with_damaged_meta_requires_a_rebuild(index_path: Path) -> None:  # [NEG]
    async def create() -> None:
        async with opened(index_path):
            pass

    asyncio.run(create())
    con = sqlite3.connect(index_path)
    con.execute("DELETE FROM meta WHERE key = 'doc_prefix'")
    con.commit()
    con.close()
    before = file_sha256(index_path)

    async def reopen() -> None:
        async with opened(index_path, auto_rebuild=True):
            pass

    with pytest.raises(IndexRebuildRequiredError):
        asyncio.run(reopen())
    assert file_sha256(index_path) == before


def test_a_directory_in_place_of_the_index_file_is_refused(tmp_path: Path) -> None:  # [NEG]
    folder = tmp_path / "index.sqlite3"
    folder.mkdir()

    async def scenario() -> None:
        repo = SqliteVecRepository(folder)
        try:
            await repo.open_or_create(PROFILE)
        finally:
            await repo.close()

    from grc_sidecar.errors import IndexStoreError

    with pytest.raises(IndexStoreError):
        asyncio.run(scenario())
