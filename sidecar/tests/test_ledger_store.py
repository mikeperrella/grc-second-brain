"""The promise ledger store (ADR-017 decisions 6, 7, 8, 10, 14, 20). Offline, in tmp_path. All data
is synthetic."""

import asyncio
import inspect
import logging
import re
import sqlite3
from collections.abc import Awaitable
from datetime import date
from pathlib import Path
from typing import TypeVar

import httpx
import pytest
from fastapi import FastAPI

from grc_sidecar.api.errors import register_error_handlers
from grc_sidecar.errors import (
    LedgerRefusedError,
    LedgerRootNotConfiguredError,
    LedgerSchemaMismatchError,
    LedgerStoreError,
)
from grc_sidecar.repositories import sqlite_ledger_repo
from grc_sidecar.repositories.base import (
    NewPromise,
    NewPromiseEvent,
    NewRunRecord,
    RunAnswer,
)
from grc_sidecar.repositories.sqlite_ledger_repo import (
    LEDGER_FILE_NAME,
    SqliteLedgerRepository,
    normalize_customer_name,
    row_hash,
)
from grc_sidecar.security.net import create_async_client
from grc_sidecar.vault.clock import FixedClock

T = TypeVar("T")

TODAY = date(2026, 10, 14)
SENT = date(2026, 10, 10)
RUN = "a" * 32
HASH = "b" * 64
# Built with chr() so no invisible character sits in this file's source.
NBSP = chr(0xA0)
SHARP_S = chr(0xDF)
E_ACUTE = chr(0xE9)
COMBINING_ACUTE = chr(0x301)
IDEOGRAPHIC_SPACE = chr(0x3000)
ZERO_WIDTH_SPACE = chr(0x200B)
RIGHT_TO_LEFT_OVERRIDE = chr(0x202E)
LONE_SURROGATE = chr(0xD800)
TABLES = ["schema_info", "run_record", "run_answer", "customer", "promise", "promise_event"]


def run(awaitable: Awaitable[T]) -> T:
    async def go() -> T:
        return await awaitable

    return asyncio.run(go())


def new_run(run_id: str = RUN) -> NewRunRecord:
    return NewRunRecord(
        run_id=run_id,
        model="llama3.1:8b",
        digest_before="d1",
        digest_after="d2",
        template_sha256=HASH,
        input_sha256=HASH,
        file_name="q.xlsx",
        sheet="Sheet1",
        header_row=1,
        question_col=1,
        first_new_column=3,
        question_count=3,
        complete=True,
    )


def answer(row: int, question_sha: str = HASH, run_id: str = RUN) -> RunAnswer:
    return RunAnswer(
        run_id=run_id,
        row_index=row,
        question_sha256=question_sha,
        status="SUPPORTED",
        reasons=("ok",),
        source_path="policies/access.md",
        doc_id="POL-1",
        start_line=3,
        end_line=4,
        quote="Access is reviewed quarterly.",
        cited_lines_sha256=HASH,
        cited_file_sha256=HASH,
        answer_cell_sha256=HASH,
        status_cell_sha256=HASH,
        evidence_cell_sha256=HASH,
    )


def new_promise(customer_id: str, row: int, question_sha: str = HASH) -> NewPromise:
    return NewPromise(
        customer_id=customer_id,
        run_id=RUN,
        run_row_index=row,
        question_sha256=question_sha,
        question_text="Do you review access?",
        answer_text="Yes, quarterly.",
        status="SUPPORTED",
        source_path="policies/access.md",
        doc_id="POL-1",
        start_line=3,
        end_line=4,
        quote="Access is reviewed quarterly.",
        cited_lines_sha256=HASH,
        cited_file_sha256=HASH,
        edited_after_draft=False,
        import_workbook_sha256=HASH,
        sent_on=SENT,
    )


@pytest.fixture
def ledger_file(tmp_path: Path) -> Path:
    return tmp_path / "ledger" / LEDGER_FILE_NAME


async def open_repo(path: Path) -> SqliteLedgerRepository:
    repo = SqliteLedgerRepository(path, FixedClock(TODAY))
    await repo.open_or_create()
    return repo


@pytest.fixture
def populated(ledger_file: Path) -> Path:
    """A ledger holding one row in every table, closed again."""

    async def build() -> None:
        repo = await open_repo(ledger_file)
        await repo.add_run_record(new_run(), [answer(2), answer(3)])
        customer = await repo.add_customer("Acme Corp")
        promise = await repo.add_promise(new_promise(customer.customer_id, 2))
        await repo.add_event(
            NewPromiseEvent(
                promise_id=promise.promise_id, kind="recall_state_changed", state="intact"
            )
        )
        await repo.close()

    asyncio.run(build())
    return ledger_file


# ----- append-only, enforced by the database ------------------------------------------------


@pytest.mark.parametrize("table", TABLES)
def test_a_raw_update_of_any_table_is_aborted_by_a_trigger(  # [NEG]
    populated: Path, table: str
) -> None:
    con = sqlite3.connect(populated, isolation_level=None)
    try:
        column = con.execute("SELECT name FROM pragma_table_info(?) LIMIT 1", (table,)).fetchone()[
            0
        ]
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            con.execute("UPDATE " + table + " SET " + column + " = " + column)  # noqa: S608
    finally:
        con.close()


@pytest.mark.parametrize("table", TABLES)
def test_a_raw_delete_from_any_table_is_aborted_by_a_trigger(  # [NEG]
    populated: Path, table: str
) -> None:
    con = sqlite3.connect(populated, isolation_level=None)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            con.execute("DELETE FROM " + table)  # noqa: S608  (table is from the fixed list above)
        count = con.execute("SELECT count(*) FROM " + table).fetchone()[0]  # noqa: S608
        assert count >= 1  # nothing went
    finally:
        con.close()


def test_every_table_has_both_triggers(populated: Path) -> None:
    con = sqlite3.connect(populated)
    try:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    finally:
        con.close()
    assert names == {f"{t}_no_{kind}" for t in TABLES for kind in ("update", "delete")}


def test_the_repository_has_no_method_that_updates_or_deletes() -> None:  # [NEG]
    forbidden = re.compile(
        r"^_?(update|delete|remove|replace|set|edit|drop|clear|purge|void)", re.I
    )
    for cls in (SqliteLedgerRepository, sqlite_ledger_repo._LedgerStore):
        for name, _member in inspect.getmembers(cls, callable):
            if not name.startswith("__"):
                assert not forbidden.match(name), f"{cls.__name__}.{name}"


def test_no_sql_in_the_module_changes_or_removes_a_row() -> None:  # [NEG]
    source = inspect.getsource(sqlite_ledger_repo)
    # These words may appear only in the trigger DDL ("BEFORE UPDATE ON", "BEFORE DELETE ON").
    assert not re.search(r"(?<!BEFORE )\bUPDATE\b", source)
    assert not re.search(r"\bDELETE\s+FROM\b|\bREPLACE\b|\bINSERT\s+OR\b|\bUPSERT\b", source)
    assert "ON CONFLICT" not in source


# ----- uniqueness: customer, run and row ----------------------------------------------------


def test_two_promises_for_the_same_question_text_at_different_rows_both_insert(
    ledger_file: Path,
) -> None:
    async def scenario() -> None:
        repo = await open_repo(ledger_file)
        await repo.add_run_record(new_run(), [answer(2), answer(3)])
        customer = await repo.add_customer("Acme")
        first = await repo.add_promise(new_promise(customer.customer_id, 2, question_sha=HASH))
        second = await repo.add_promise(new_promise(customer.customer_id, 3, question_sha=HASH))
        assert first.question_sha256 == second.question_sha256
        assert [p.run_row_index for p in await repo.list_promises()] == [2, 3]
        await repo.close()

    asyncio.run(scenario())


def test_the_same_customer_run_and_row_twice_is_a_typed_refusal(  # [NEG]
    ledger_file: Path,
) -> None:
    async def scenario() -> None:
        repo = await open_repo(ledger_file)
        await repo.add_run_record(new_run(), [answer(2)])
        customer = await repo.add_customer("Acme")
        await repo.add_promise(new_promise(customer.customer_id, 2))
        with pytest.raises(LedgerRefusedError) as info:
            await repo.add_promise(new_promise(customer.customer_id, 2, question_sha="c" * 64))
        assert info.value.code == "promise_exists"
        assert len(await repo.list_promises()) == 1  # the refusal rolled back whole
        await repo.close()

    asyncio.run(scenario())


def test_another_customer_may_hold_a_promise_for_the_same_run_and_row(ledger_file: Path) -> None:
    async def scenario() -> None:
        repo = await open_repo(ledger_file)
        await repo.add_run_record(new_run(), [answer(2)])
        one = await repo.add_customer("One")
        two = await repo.add_customer("Two")
        await repo.add_promise(new_promise(one.customer_id, 2))
        await repo.add_promise(new_promise(two.customer_id, 2))
        assert len(await repo.list_promises()) == 2
        await repo.close()

    asyncio.run(scenario())


def test_a_promise_for_an_unknown_customer_or_run_is_refused(ledger_file: Path) -> None:  # [NEG]
    async def scenario() -> None:
        repo = await open_repo(ledger_file)
        await repo.add_run_record(new_run(), [answer(2)])
        with pytest.raises(LedgerRefusedError) as info:
            await repo.add_promise(new_promise("nobody", 2))
        assert info.value.code == "customer_unknown"
        customer = await repo.add_customer("Acme")
        other = new_promise(customer.customer_id, 2).model_copy(update={"run_id": "f" * 32})
        with pytest.raises(LedgerRefusedError) as info2:
            await repo.add_promise(other)
        assert info2.value.code == "run_unknown"
        await repo.close()

    asyncio.run(scenario())


def test_a_run_is_recorded_once_and_whole(ledger_file: Path) -> None:  # [NEG]
    async def scenario() -> None:
        repo = await open_repo(ledger_file)
        await repo.add_run_record(new_run(), [answer(2), answer(3)])
        with pytest.raises(LedgerRefusedError) as info:
            await repo.add_run_record(new_run(), [answer(2)])
        assert info.value.code == "run_exists"
        stray = answer(2, run_id="e" * 32)
        with pytest.raises(LedgerRefusedError):
            await repo.add_run_record(new_run("c" * 32), [stray])
        assert await repo.get_run_record("c" * 32) is None
        stored = await repo.get_run_record(RUN)
        assert stored is not None and stored.created_on == TODAY and stored.complete is True
        assert [a.row_index for a in await repo.list_run_answers(RUN)] == [2, 3]
        assert (await repo.list_run_answers(RUN))[0].reasons == ("ok",)
        await repo.close()

    asyncio.run(scenario())


# ----- lookup by the recorded file name (ADR-021 decision 2) ---------------------------------


def named_run(run_id: str, file_name: str) -> NewRunRecord:
    return new_run(run_id).model_copy(update={"file_name": file_name})


def test_a_run_is_found_by_its_full_recorded_file_name_only(ledger_file: Path) -> None:  # [NEG]
    shared = "aaaaaaaa"  # two runs whose ids (and so output names) share the same 8 hex digits
    first, second = shared + "1" * 24, shared + "2" * 24
    name_first = f"Vendor-UNREVIEWED-DRAFT-{shared}.xlsx"
    name_second = f"Other-UNREVIEWED-DRAFT-{shared}.xlsx"

    async def scenario() -> None:
        repo = await open_repo(ledger_file)
        await repo.add_run_record(named_run(first, name_first), [answer(2, run_id=first)])
        await repo.add_run_record(named_run(second, name_second), [answer(2, run_id=second)])
        found = await repo.get_run_record_by_file_name(name_first)
        assert found is not None and found.run_id == first
        found = await repo.get_run_record_by_file_name(name_second)
        assert found is not None and found.run_id == second
        # Not by the 8 hex digits, a prefix, a different case or a renamed copy.
        assert await repo.get_run_record_by_file_name(shared) is None
        assert await repo.get_run_record_by_file_name(name_first[:-5]) is None
        assert await repo.get_run_record_by_file_name(name_first.upper()) is None
        assert await repo.get_run_record_by_file_name("Copy of " + name_first) is None
        assert await repo.get_run_record_by_file_name("%") is None
        assert await repo.get_run_record_by_file_name("") is None
        await repo.close()

    asyncio.run(scenario())


def test_a_file_name_shared_by_two_records_matches_neither(ledger_file: Path) -> None:  # [NEG]
    async def scenario() -> None:
        repo = await open_repo(ledger_file)
        await repo.add_run_record(named_run("a" * 32, "same.xlsx"), [answer(2, run_id="a" * 32)])
        assert (await repo.get_run_record_by_file_name("same.xlsx")) is not None
        await repo.add_run_record(named_run("b" * 32, "same.xlsx"), [answer(2, run_id="b" * 32)])
        assert await repo.get_run_record_by_file_name("same.xlsx") is None
        await repo.close()

    asyncio.run(scenario())


def test_open_existing_never_creates_the_ledger(tmp_path: Path) -> None:  # [NEG]
    folder = tmp_path / "ledger"
    path = folder / LEDGER_FILE_NAME

    async def scenario() -> None:
        repo = SqliteLedgerRepository(path, FixedClock(TODAY))
        assert await repo.open_existing() is False
        assert not folder.exists()
        folder.mkdir()
        path.write_bytes(b"")  # an empty file is not a ledger yet
        assert await repo.open_existing() is False
        assert path.read_bytes() == b""
        await repo.open_or_create()
        await repo.close()
        again = SqliteLedgerRepository(path, FixedClock(TODAY))
        assert await again.open_existing() is True
        assert await again.get_run_record_by_file_name("none.xlsx") is None
        await again.close()

    asyncio.run(scenario())


def test_open_existing_refuses_a_file_that_is_not_this_ledger(tmp_path: Path) -> None:  # [NEG]
    path = tmp_path / LEDGER_FILE_NAME
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE unrelated (x INTEGER)")
    con.commit()
    con.close()
    before = path.read_bytes()

    async def scenario() -> None:
        repo = SqliteLedgerRepository(path, FixedClock(TODAY))
        with pytest.raises(LedgerSchemaMismatchError):
            await repo.open_existing()

    asyncio.run(scenario())
    assert path.read_bytes() == before


# ----- customers ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "normalized"),
    [
        ("Acme Corp", "acme corp"),
        ("  ACME   corp  ", "acme corp"),
        ("Acme" + NBSP + "Corp", "acme corp"),  # no-break space is whitespace
        ("Stra" + SHARP_S + "e GmbH", "strasse gmbh"),  # casefold, not lower
        ("Cafe" + COMBINING_ACUTE, "caf" + E_ACUTE),  # NFD in, NFC out
    ],
)
def test_customer_normalization(typed: str, normalized: str) -> None:
    assert normalize_customer_name(typed) == normalized


def test_a_name_that_normalizes_to_an_existing_customer_is_refused_with_its_id(  # [NEG]
    ledger_file: Path,
) -> None:
    async def scenario() -> None:
        repo = await open_repo(ledger_file)
        typed = "  Caf" + E_ACUTE + "   Noir "
        first = await repo.add_customer(typed)
        assert first.display_name == typed  # kept as typed
        for variant in (
            "CAF" + E_ACUTE.upper() + " noir",
            "Cafe" + COMBINING_ACUTE + " Noir",  # NFD
            "caf" + E_ACUTE + NBSP + "noir",
        ):
            with pytest.raises(LedgerRefusedError) as info:
                await repo.add_customer(variant)
            assert info.value.code == "customer_exists"
            assert info.value.customer_id == first.customer_id
        assert len(await repo.list_customers()) == 1
        found = await repo.find_customer_by_name("caf" + E_ACUTE + " NOIR")
        assert found is not None and found.customer_id == first.customer_id
        assert await repo.find_customer_by_name("someone else") is None
        await repo.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        IDEOGRAPHIC_SPACE,
        "x" * 201,
        "tab\there",
        "line\nbreak",
        "nul\x00byte",
        "del\x7f",
        "zero" + ZERO_WIDTH_SPACE + "width",
        "rtl" + RIGHT_TO_LEFT_OVERRIDE + "override",
        "lone" + LONE_SURROGATE + "surrogate",
    ],
)
def test_a_bad_customer_name_is_refused_and_nothing_is_stored(  # [NEG]
    ledger_file: Path, bad: str
) -> None:
    async def scenario() -> None:
        repo = await open_repo(ledger_file)
        with pytest.raises(LedgerRefusedError) as info:
            await repo.add_customer(bad)
        assert info.value.code == "customer_name_invalid"
        assert info.value.customer_id is None
        assert await repo.list_customers() == []
        await repo.close()

    asyncio.run(scenario())


def test_a_name_of_exactly_200_characters_is_accepted(ledger_file: Path) -> None:
    async def scenario() -> None:
        repo = await open_repo(ledger_file)
        customer = await repo.add_customer("x" * 200)
        assert customer.display_name == "x" * 200
        await repo.close()

    asyncio.run(scenario())


# ----- events and dates ---------------------------------------------------------------------


def test_events_are_ordered_by_integer_id_and_dated_by_the_clock(populated: Path) -> None:
    async def scenario() -> None:
        repo = await open_repo(populated)
        promise = (await repo.list_promises())[0]
        await repo.add_event(NewPromiseEvent(promise_id=promise.promise_id, kind="acknowledged"))
        await repo.add_event(
            NewPromiseEvent(
                promise_id=promise.promise_id, kind="voided", detail_code="wrong_customer"
            )
        )
        events = await repo.list_events(promise.promise_id)
        assert [e.kind for e in events] == [
            "created",
            "recall_state_changed",
            "acknowledged",
            "voided",
        ]
        assert [e.event_id for e in events] == sorted(e.event_id for e in events)
        assert {e.on_date for e in events} == {TODAY}
        assert promise.created_on == TODAY and promise.sent_on == SENT
        await repo.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "event",
    [
        {"kind": "recall_state_changed"},  # a state change needs a state
        {"kind": "acknowledged", "state": "intact"},
        {"kind": "voided"},  # a void needs a reason code
        {"kind": "voided", "detail_code": "free text is not allowed"},
        {"kind": "voided", "state": "intact", "detail_code": "other"},
    ],
)
def test_an_event_outside_the_fixed_vocabulary_is_refused(  # [NEG]
    populated: Path, event: dict[str, str]
) -> None:
    async def scenario() -> None:
        repo = await open_repo(populated)
        promise = (await repo.list_promises())[0]
        before = len(await repo.list_events(promise.promise_id))
        with pytest.raises(LedgerRefusedError) as info:
            await repo.add_event(
                NewPromiseEvent.model_validate({"promise_id": promise.promise_id, **event})
            )
        assert info.value.code == "event_invalid"
        assert len(await repo.list_events(promise.promise_id)) == before
        await repo.close()

    asyncio.run(scenario())


def test_an_event_for_an_unknown_promise_is_refused(populated: Path) -> None:  # [NEG]
    async def scenario() -> None:
        repo = await open_repo(populated)
        with pytest.raises(LedgerRefusedError) as info:
            await repo.add_event(NewPromiseEvent(promise_id="nope", kind="acknowledged"))
        assert info.value.code == "promise_unknown"
        await repo.close()

    asyncio.run(scenario())


# ----- row hashes ---------------------------------------------------------------------------


def test_every_row_carries_a_recomputable_hash(populated: Path) -> None:
    con = sqlite3.connect(populated)
    try:
        for table in TABLES[1:]:
            cols = [r[1] for r in con.execute("SELECT * FROM pragma_table_info(?)", (table,))]
            for row in con.execute("SELECT * FROM " + table):  # noqa: S608
                hashed = row[1:-1] if table == "promise_event" else row[:-1]
                assert cols[-1] == "row_sha256"
                assert row_hash(table, hashed) == row[-1]
    finally:
        con.close()


def test_a_corrupted_row_fails_closed_on_read(populated: Path) -> None:  # [NEG]
    con = sqlite3.connect(populated, isolation_level=None)
    con.execute("DROP TRIGGER customer_no_update")  # what a same-user process could do (O1)
    con.execute("UPDATE customer SET display_name = 'Tampered'")
    con.close()

    async def scenario() -> None:
        repo = await open_repo(populated)
        with pytest.raises(LedgerStoreError):
            await repo.list_customers()
        await repo.close()

    asyncio.run(scenario())


# ----- schema version and the file ----------------------------------------------------------


def test_a_schema_version_mismatch_fails_closed_and_leaves_the_file_alone(  # [NEG]
    populated: Path,
) -> None:
    con = sqlite3.connect(populated, isolation_level=None)
    con.execute("DROP TRIGGER schema_info_no_update")
    con.execute("UPDATE schema_info SET version = 2")
    con.close()
    before = populated.read_bytes()
    repo = SqliteLedgerRepository(populated, FixedClock(TODAY))
    with pytest.raises(LedgerSchemaMismatchError):
        run(repo.open_or_create())
    run(repo.close())
    assert populated.read_bytes() == before


def test_a_file_that_is_not_a_ledger_fails_closed_and_is_not_touched(  # [NEG]
    tmp_path: Path,
) -> None:
    path = tmp_path / "ledger" / LEDGER_FILE_NAME
    path.parent.mkdir()
    path.write_bytes(b"this is not a database at all" * 10)
    before = path.read_bytes()
    repo = SqliteLedgerRepository(path, FixedClock(TODAY))
    with pytest.raises(LedgerSchemaMismatchError):
        run(repo.open_or_create())
    run(repo.close())
    assert path.read_bytes() == before


def test_another_sqlite_database_is_not_taken_for_a_ledger(tmp_path: Path) -> None:  # [NEG]
    path = tmp_path / "ledger" / LEDGER_FILE_NAME
    path.parent.mkdir()
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE something_else (x)")
    con.commit()
    con.close()
    repo = SqliteLedgerRepository(path, FixedClock(TODAY))
    with pytest.raises(LedgerSchemaMismatchError):
        run(repo.open_or_create())
    run(repo.close())


def test_reopening_a_ledger_keeps_its_rows(populated: Path) -> None:
    async def scenario() -> None:
        repo = await open_repo(populated)
        assert len(await repo.list_promises()) == 1
        assert (await repo.get_run_record(RUN)) is not None
        await repo.close()

    asyncio.run(scenario())


def test_the_database_file_is_created_only_inside_the_ledger_folder(tmp_path: Path) -> None:
    folder = tmp_path / "ledger"
    other = tmp_path / "elsewhere"
    other.mkdir()
    repo = run(open_repo(folder / LEDGER_FILE_NAME))
    run(repo.add_customer("Acme"))
    run(repo.close())
    created = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    assert created == ["elsewhere", "ledger", f"ledger/{LEDGER_FILE_NAME}"]


def test_using_a_closed_ledger_is_a_typed_error(ledger_file: Path) -> None:  # [NEG]
    repo = SqliteLedgerRepository(ledger_file, FixedClock(TODAY))
    with pytest.raises(LedgerStoreError):
        run(repo.list_customers())


# ----- no content in logs -------------------------------------------------------------------

CANARY = "CANARY-7f3a91c2"


def test_a_canary_in_a_name_a_question_and_an_answer_never_reaches_a_log(  # [NEG]
    ledger_file: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    async def scenario() -> None:
        repo = await open_repo(ledger_file)
        await repo.add_run_record(new_run(), [answer(2)])
        customer = await repo.add_customer(f"Acme {CANARY}")
        promise = new_promise(customer.customer_id, 2).model_copy(
            update={"question_text": f"Q {CANARY}", "answer_text": f"A {CANARY}"}
        )
        await repo.add_promise(promise)
        with pytest.raises(LedgerRefusedError):
            await repo.add_promise(promise)  # a refusal must not echo the content either
        with pytest.raises(LedgerRefusedError):
            await repo.add_customer(f"ACME  {CANARY}")
        with pytest.raises(LedgerRefusedError):
            await repo.add_customer(f"bad\x00{CANARY}")
        await repo.close()

    asyncio.run(scenario())
    assert CANARY not in caplog.text
    assert all(record.levelno >= logging.DEBUG for record in caplog.records)  # sanity: captured


def test_error_texts_never_hold_caller_values() -> None:  # [NEG]
    refusal = LedgerRefusedError("customer_exists", customer_id="abc")
    assert str(refusal) == "The ledger write was refused: customer_exists"


# ----- the typed 503 and 422 ----------------------------------------------------------------


def test_the_ledger_error_words_map_to_fixed_http_bodies() -> None:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/unset")
    def unset() -> None:
        raise LedgerRootNotConfiguredError

    @app.get("/exists")
    def exists() -> None:
        raise LedgerRefusedError("customer_exists", customer_id="abc123")

    @app.get("/mismatch")
    def mismatch() -> None:
        raise LedgerSchemaMismatchError

    async def scenario() -> None:
        async with create_async_client(
            "http://127.0.0.1:8000", transport=httpx.ASGITransport(app=app)
        ) as client:
            response = await client.get("/unset")
            assert (response.status_code, response.json()) == (
                503,
                {"error": "ledger_root_not_configured"},
            )
            response = await client.get("/exists")
            assert response.status_code == 422
            assert response.json() == {
                "error": "ledger_refused",
                "code": "customer_exists",
                "customer_id": "abc123",
            }
            response = await client.get("/mismatch")
            assert (response.status_code, response.json()) == (503, {"error": "ledger_unusable"})

    asyncio.run(scenario())
