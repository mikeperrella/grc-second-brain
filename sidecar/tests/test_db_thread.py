"""One dedicated thread owns the SQLite connection (ADR-009, concurrency model)."""

import asyncio
import sqlite3
import threading
from pathlib import Path

import pytest

from grc_sidecar.repositories import sqlite_vec_repo
from grc_sidecar.repositories.sqlite_vec_repo import SqliteVecRepository
from grc_sidecar.repositories.worker import THREAD_NAME_PREFIX

from .repokit import PROFILE, axis, make_chunk, make_doc
from .test_vector_repository import snapshot

_CORE_METHODS = (
    "document_states",
    "begin_sync",
    "replace_document",
    "remove_document",
    "re_resolve_links",
    "search",
    "chunks_for",
    "links_for",
    "verify_integrity",
    "close",
)


def test_sqlite_calls_run_on_the_single_db_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # [NEG] mutation row 24
    seen: list[tuple[str, int, str]] = []

    def record(name: str) -> None:
        current = threading.current_thread()
        seen.append((name, threading.get_ident(), current.name))

    store_class = sqlite_vec_repo._SqliteStore
    original_init = store_class.__init__

    def recording_init(self: object, *args: object, **kwargs: object) -> None:
        record("__init__")
        original_init(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(store_class, "__init__", recording_init)
    for method_name in _CORE_METHODS:
        original = getattr(store_class, method_name)

        def make_wrapper(name: str, target: object) -> object:
            def wrapper(self: object, *args: object, **kwargs: object) -> object:
                record(name)
                return target(self, *args, **kwargs)  # type: ignore[operator]

            return wrapper

        monkeypatch.setattr(store_class, method_name, make_wrapper(method_name, original))

    doc = make_doc()
    loop_thread: list[int] = []

    async def scenario() -> None:
        loop_thread.append(threading.get_ident())
        repo = SqliteVecRepository(tmp_path / "t.sqlite3")
        await repo.open_or_create(PROFILE)
        await repo.replace_document(doc, [make_chunk(doc, 5, "x")], [axis(0)], [])
        await repo.search(axis(0), k=1)
        await repo.chunks_for(doc.source_path)
        await repo.document_states()
        await repo.begin_sync(snapshot(doc))
        await repo.verify_integrity()
        await repo.close()

    asyncio.run(scenario())

    called = {name for name, _, _ in seen}
    assert called >= {"__init__", "replace_document", "search", "begin_sync", "close"}
    assert len({ident for _, ident, _ in seen}) == 1, "SQLite was used from more than one thread"
    assert seen[0][1] != loop_thread[0], "SQLite was used on the event-loop thread"
    assert all(thread_name.startswith(THREAD_NAME_PREFIX) for _, _, thread_name in seen)


def test_the_connection_cannot_be_used_from_another_thread(tmp_path: Path) -> None:  # [NEG]
    # check_same_thread is left on, so a call that bypasses the worker fails loudly.
    async def scenario() -> None:
        repo = SqliteVecRepository(tmp_path / "t.sqlite3")
        await repo.open_or_create(PROFILE)
        try:
            store = repo._core
            assert store is not None
            with pytest.raises(sqlite3.ProgrammingError):
                store.chunks_for("x.md")  # called here, on the test's own thread
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_two_overlapping_calls_are_serialized_not_interleaved(tmp_path: Path) -> None:
    doc_a = make_doc("a.md", doc_id="A")
    doc_b = make_doc("b.md", doc_id="B")

    async def scenario() -> None:
        repo = SqliteVecRepository(tmp_path / "t.sqlite3")
        await repo.open_or_create(PROFILE)
        try:
            await asyncio.gather(
                repo.replace_document(doc_a, [make_chunk(doc_a, 5, "a")], [axis(0)], []),
                repo.replace_document(doc_b, [make_chunk(doc_b, 5, "b")], [axis(1)], []),
            )
            assert set(await repo.document_states()) == {"a.md", "b.md"}
            assert (await repo.verify_integrity()).ok
        finally:
            await repo.close()

    asyncio.run(scenario())
