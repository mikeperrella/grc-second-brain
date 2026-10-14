"""The promise ledger on SQLite (ADR-017 decisions 6, 7, 8, 10, 14 and 20).

The ledger is append-only, enforced twice: this module has insert and read methods and no way to
change or remove a row, and a SQLite trigger on EVERY table aborts any attempt to do it anyway.
It is its own file, `<ledger_dir>/ledger.sqlite3`, never part of the index: the index is derived
state that can be rebuilt, the ledger cannot be rebuilt from anything.

Honest limits: a same-user process can open the file and drop the triggers, and `row_sha256` only
catches accidental corruption. "Immutable" here means immutable to the application.
"""

import contextlib
import hashlib
import json
import re
import sqlite3
import unicodedata
import uuid
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Final, Self, TypeVar, get_args

from pydantic import ValidationError

from grc_sidecar.config import Settings
from grc_sidecar.errors import (
    GrcError,
    LedgerRefusedError,
    LedgerRootNotConfiguredError,
    LedgerSchemaMismatchError,
    LedgerStoreError,
)
from grc_sidecar.repositories.base import (
    Customer,
    NewPromise,
    NewPromiseEvent,
    NewRunRecord,
    Promise,
    PromiseEvent,
    RunAnswer,
    RunRecord,
    StoredRunAnswer,
    VoidReason,
)
from grc_sidecar.repositories.worker import SqliteWorker
from grc_sidecar.vault.clock import Clock

T = TypeVar("T")

SCHEMA_VERSION: Final = 1
LEDGER_FILE_NAME: Final = "ledger.sqlite3"
MAX_DISPLAY_NAME_CHARS: Final = 200
_BLOCKED_NAME_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cs"})  # control, format, surrogate

# Column order is part of the schema: the row hash is taken over these values in this order.
_RUN_RECORD_COLS: Final = (
    "run_id",
    "created_on",
    "model",
    "digest_before",
    "digest_after",
    "template_sha256",
    "input_sha256",
    "file_name",
    "sheet",
    "header_row",
    "question_col",
    "first_new_column",
    "question_count",
    "complete",
    "row_sha256",
)
_RUN_ANSWER_COLS: Final = (
    "run_id",
    "row_index",
    "question_sha256",
    "status",
    "reasons",
    "source_path",
    "doc_id",
    "start_line",
    "end_line",
    "quote",
    "cited_lines_sha256",
    "cited_file_sha256",
    "answer_cell_sha256",
    "status_cell_sha256",
    "evidence_cell_sha256",
    "row_sha256",
)
_CUSTOMER_COLS: Final = ("customer_id", "display_name", "normalized_name", "row_sha256")
_PROMISE_COLS: Final = (
    "promise_id",
    "customer_id",
    "run_id",
    "run_row_index",
    "question_sha256",
    "question_text",
    "answer_text",
    "status",
    "source_path",
    "doc_id",
    "start_line",
    "end_line",
    "quote",
    "cited_lines_sha256",
    "cited_file_sha256",
    "edited_after_draft",
    "import_workbook_sha256",
    "sent_on",
    "created_on",
    "row_sha256",
)
# `event_id` is assigned by SQLite at insert, so it is not part of the row hash.
_EVENT_COLS: Final = (
    "event_id",
    "promise_id",
    "kind",
    "state",
    "detail_code",
    "on_date",
    "row_sha256",
)

_TABLE_COLS: Final = {
    "run_record": _RUN_RECORD_COLS,
    "run_answer": _RUN_ANSWER_COLS,
    "customer": _CUSTOMER_COLS,
    "promise": _PROMISE_COLS,
    "promise_event": _EVENT_COLS,
}
# Every table, including schema_info. A trigger is created for each, from this one tuple.
_TABLES: Final = ("schema_info", *_TABLE_COLS)

_SCHEMA: Final = (
    "CREATE TABLE schema_info (version INTEGER NOT NULL)",
    """CREATE TABLE run_record (
        run_id TEXT PRIMARY KEY,
        created_on TEXT NOT NULL,
        model TEXT NOT NULL,
        digest_before TEXT NOT NULL,
        digest_after TEXT NOT NULL,
        template_sha256 TEXT NOT NULL,
        input_sha256 TEXT NOT NULL,
        file_name TEXT NOT NULL,
        sheet TEXT NOT NULL,
        header_row INTEGER NOT NULL,
        question_col INTEGER NOT NULL,
        first_new_column INTEGER NOT NULL,
        question_count INTEGER NOT NULL,
        complete INTEGER NOT NULL CHECK (complete IN (0, 1)),
        row_sha256 TEXT NOT NULL
    )""",
    """CREATE TABLE run_answer (
        run_id TEXT NOT NULL REFERENCES run_record (run_id),
        row_index INTEGER NOT NULL,
        question_sha256 TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('SUPPORTED', 'PARTIAL', 'NEEDS_REVIEW')),
        reasons TEXT NOT NULL,
        source_path TEXT,
        doc_id TEXT,
        start_line INTEGER,
        end_line INTEGER,
        quote TEXT,
        cited_lines_sha256 TEXT,
        cited_file_sha256 TEXT,
        answer_cell_sha256 TEXT,
        status_cell_sha256 TEXT NOT NULL,
        evidence_cell_sha256 TEXT,
        row_sha256 TEXT NOT NULL,
        PRIMARY KEY (run_id, row_index)
    )""",
    """CREATE TABLE customer (
        customer_id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        normalized_name TEXT NOT NULL UNIQUE,
        row_sha256 TEXT NOT NULL
    )""",
    """CREATE TABLE promise (
        promise_id TEXT PRIMARY KEY,
        customer_id TEXT NOT NULL REFERENCES customer (customer_id),
        run_id TEXT NOT NULL REFERENCES run_record (run_id),
        run_row_index INTEGER NOT NULL,
        question_sha256 TEXT NOT NULL,
        question_text TEXT NOT NULL,
        answer_text TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('SUPPORTED', 'PARTIAL')),
        source_path TEXT NOT NULL,
        doc_id TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        quote TEXT NOT NULL,
        cited_lines_sha256 TEXT NOT NULL,
        cited_file_sha256 TEXT NOT NULL,
        edited_after_draft INTEGER NOT NULL CHECK (edited_after_draft IN (0, 1)),
        import_workbook_sha256 TEXT NOT NULL,
        sent_on TEXT NOT NULL,
        created_on TEXT NOT NULL,
        row_sha256 TEXT NOT NULL,
        UNIQUE (customer_id, run_id, run_row_index)
    )""",
    """CREATE TABLE promise_event (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        promise_id TEXT NOT NULL REFERENCES promise (promise_id),
        kind TEXT NOT NULL
            CHECK (kind IN ('created', 'recall_state_changed', 'acknowledged', 'voided')),
        state TEXT,
        detail_code TEXT,
        on_date TEXT NOT NULL,
        row_sha256 TEXT NOT NULL
    )""",
)
# One trigger of each kind per table. The table name comes from `_TABLES` above, never from input.
_TRIGGER: Final = (
    "CREATE TRIGGER __TABLE___no_update BEFORE UPDATE ON __TABLE__ "
    "BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END",
    "CREATE TRIGGER __TABLE___no_delete BEFORE DELETE ON __TABLE__ "
    "BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END",
)


# The three builders below run once, at import, over the constant table and column names above:
# no value and no caller input ever reaches them, so S608 does not apply. Every VALUE is bound.
def _insert_sql(table: str, columns: Sequence[str]) -> str:
    return (
        "INSERT INTO "  # noqa: S608
        + table
        + " ("
        + ", ".join(columns)
        + ") VALUES ("
        + ", ".join("?" for _ in columns)
        + ")"
    )


def _select_sql(table: str, columns: Sequence[str]) -> str:
    return "SELECT " + ", ".join(columns) + " FROM " + table  # noqa: S608


_INSERT: Final = {
    "run_record": _insert_sql("run_record", _RUN_RECORD_COLS),
    "run_answer": _insert_sql("run_answer", _RUN_ANSWER_COLS),
    "customer": _insert_sql("customer", _CUSTOMER_COLS),
    "promise": _insert_sql("promise", _PROMISE_COLS),
    "promise_event": _insert_sql("promise_event", _EVENT_COLS[1:]),
}
_SELECT: Final = {table: _select_sql(table, cols) for table, cols in _TABLE_COLS.items()}

_WHITESPACE_RUN = re.compile(r"\s+")
_VOID_REASONS: Final = frozenset(get_args(VoidReason))


# ----- customer names ----------------------------------------------------------------------


def normalize_customer_name(name: str) -> str:
    """The customer table's unique key: NFC, trimmed, whitespace collapsed, case-folded.

    Case folding can leave text un-normalized, so NFC is applied once more at the end.
    """
    text = unicodedata.normalize("NFC", name).strip()
    text = _WHITESPACE_RUN.sub(" ", text)
    return unicodedata.normalize("NFC", text.casefold())


def _checked_display_name(name: str) -> str:
    """The normalized form of a name that may be stored, else `customer_name_invalid`.

    The display name is kept exactly as typed, so what is checked is what is stored: at most 200
    characters, no control, format (zero-width, direction marks) or surrogate characters, and
    something left once normalized.
    """
    bad = (
        len(name) > MAX_DISPLAY_NAME_CHARS
        or any(unicodedata.category(char) in _BLOCKED_NAME_CATEGORIES for char in name)
        or not normalize_customer_name(name)
    )
    if bad:
        raise LedgerRefusedError("customer_name_invalid")
    return normalize_customer_name(name)


# ----- row hashes --------------------------------------------------------------------------


def row_hash(table: str, values: Sequence[object]) -> str:
    """sha256 over a row's own fields, in column order, each tagged and length-prefixed.

    Detects accidental corruption only. It is not a signature, and nothing here claims tamper
    evidence (ADR-017 decision 9).
    """
    digest = hashlib.sha256(table.encode("utf-8") + b"\0")
    for value in values:
        if value is None:
            digest.update(b"N;")
        elif isinstance(value, int):
            digest.update(b"I" + str(int(value)).encode("ascii") + b";")
        else:
            data = str(value).encode("utf-8")
            digest.update(b"S" + str(len(data)).encode("ascii") + b":" + data + b";")
    return digest.hexdigest()


def _with_hash(table: str, values: tuple[object, ...]) -> tuple[object, ...]:
    return (*values, row_hash(table, values))


def _verified(table: str, columns: Sequence[str], row: tuple[object, ...]) -> dict[str, object]:
    """A stored row as a dict, after checking its hash. A mismatch is corruption: fail closed."""
    first = 1 if table == "promise_event" else 0  # event_id is not hashed
    if row_hash(table, row[first:-1]) != row[-1]:
        raise LedgerStoreError("A ledger row failed its integrity check")
    return dict(zip(columns, row, strict=True))


def ledger_path(settings: Settings) -> Path:
    """`<ledger_dir>/ledger.sqlite3`. With no ledger folder set there is no ledger (ADR-017 #7)."""
    if settings.ledger_dir is None:
        raise LedgerRootNotConfiguredError
    return settings.ledger_dir / LEDGER_FILE_NAME


# ----- opening -----------------------------------------------------------------------------


def _inspect(path: Path) -> tuple[set[str], int | None]:
    """(table names, schema version or None) of an existing file, opened READ-ONLY.

    The file is never written to, so a refusal leaves it byte-identical. A file that is not SQLite
    at all, or whose `schema_info` does not hold exactly one version row, is a mismatch.
    """
    con: sqlite3.Connection | None = None
    names: set[str] = set()
    version: int | None = None
    failed = False
    try:
        con = sqlite3.connect(f"{path.resolve(strict=True).as_uri()}?mode=ro", uri=True)
        names = {
            str(row[0])
            for row in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if "schema_info" in names:
            rows = con.execute("SELECT version FROM schema_info").fetchall()
            if len(rows) == 1 and isinstance(rows[0][0], int):
                version = rows[0][0]
    except (sqlite3.Error, OSError):
        failed = True
    finally:
        if con is not None:
            con.close()
    if failed:
        raise LedgerSchemaMismatchError
    return names, version


def _connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path, isolation_level=None)  # explicit BEGIN/COMMIT; check_same_thread on
    con.execute("PRAGMA foreign_keys = ON")
    return con


class _LedgerStore:
    """The synchronous core. Never call it from outside the worker thread."""

    def __init__(self, con: sqlite3.Connection, clock: Clock) -> None:
        self._con = con
        self._clock = clock

    @classmethod
    def open(cls, path: Path, clock: Clock) -> Self:
        if path.is_dir():
            raise LedgerStoreError
        if path.exists():
            names, version = _inspect(path)
            if names:  # an empty file is not a ledger yet; a file with tables must be exactly one
                if version != SCHEMA_VERSION or not set(_TABLES) <= names:
                    raise LedgerSchemaMismatchError
                return cls(_connect(path), clock)
        path.parent.mkdir(parents=True, exist_ok=True)
        store = cls(_connect(path), clock)
        try:
            with store._transaction():
                for statement in _SCHEMA:
                    store._con.execute(statement)
                for table in _TABLES:
                    for template in _TRIGGER:
                        store._con.execute(template.replace("__TABLE__", table))
                store._con.execute(
                    "INSERT INTO schema_info (version) VALUES (?)", (SCHEMA_VERSION,)
                )
        except BaseException:
            store.close()
            raise
        return store

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

    def _exists(self, table: str, column: str, value: object) -> bool:
        # `table` and `column` are constants at every call site, never input.
        sql = "SELECT 1 FROM " + table + " WHERE " + column + " = ?"  # noqa: S608
        return self._con.execute(sql, (value,)).fetchone() is not None

    # ----- run record ---------------------------------------------------------------------

    def add_run_record(self, record: NewRunRecord, answers: Sequence[RunAnswer]) -> None:
        if any(answer.run_id != record.run_id for answer in answers):
            raise LedgerRefusedError("run_unknown")
        with self._transaction():
            if self._exists("run_record", "run_id", record.run_id):
                raise LedgerRefusedError("run_exists")
            self._con.execute(
                _INSERT["run_record"],
                _with_hash(
                    "run_record",
                    (
                        record.run_id,
                        self._clock.today().isoformat(),
                        record.model,
                        record.digest_before,
                        record.digest_after,
                        record.template_sha256,
                        record.input_sha256,
                        record.file_name,
                        record.sheet,
                        record.header_row,
                        record.question_col,
                        record.first_new_column,
                        record.question_count,
                        int(record.complete),
                    ),
                ),
            )
            for answer in answers:
                self._con.execute(
                    _INSERT["run_answer"],
                    _with_hash(
                        "run_answer",
                        (
                            answer.run_id,
                            answer.row_index,
                            answer.question_sha256,
                            answer.status,
                            json.dumps(list(answer.reasons)),
                            answer.source_path,
                            answer.doc_id,
                            answer.start_line,
                            answer.end_line,
                            answer.quote,
                            answer.cited_lines_sha256,
                            answer.cited_file_sha256,
                            answer.answer_cell_sha256,
                            answer.status_cell_sha256,
                            answer.evidence_cell_sha256,
                        ),
                    ),
                )

    def get_run_record(self, run_id: str) -> RunRecord | None:
        row = self._con.execute(_SELECT["run_record"] + " WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return RunRecord.model_validate(_verified("run_record", _RUN_RECORD_COLS, row))

    def list_run_answers(self, run_id: str) -> list[StoredRunAnswer]:
        rows = self._con.execute(
            _SELECT["run_answer"] + " WHERE run_id = ? ORDER BY row_index", (run_id,)
        ).fetchall()
        found: list[StoredRunAnswer] = []
        for row in rows:
            values = _verified("run_answer", _RUN_ANSWER_COLS, row)
            raw_reasons = values["reasons"]
            values["reasons"] = json.loads(raw_reasons) if isinstance(raw_reasons, str) else []
            found.append(StoredRunAnswer.model_validate(values))
        return found

    # ----- customers ----------------------------------------------------------------------

    def add_customer(self, display_name: str) -> Customer:
        normalized = _checked_display_name(display_name)
        customer_id = uuid.uuid4().hex
        with self._transaction():
            existing = self._con.execute(
                "SELECT customer_id FROM customer WHERE normalized_name = ?", (normalized,)
            ).fetchone()
            if existing is not None:
                raise LedgerRefusedError("customer_exists", customer_id=str(existing[0]))
            self._con.execute(
                _INSERT["customer"],
                _with_hash("customer", (customer_id, display_name, normalized)),
            )
        stored = self.get_customer(customer_id)
        if stored is None:  # pragma: no cover  (the row was just committed)
            raise LedgerStoreError
        return stored

    def get_customer(self, customer_id: str) -> Customer | None:
        row = self._con.execute(
            _SELECT["customer"] + " WHERE customer_id = ?", (customer_id,)
        ).fetchone()
        return (
            None
            if row is None
            else Customer.model_validate(_verified("customer", _CUSTOMER_COLS, row))
        )

    def find_customer_by_name(self, name: str) -> Customer | None:
        row = self._con.execute(
            _SELECT["customer"] + " WHERE normalized_name = ?", (normalize_customer_name(name),)
        ).fetchone()
        return (
            None
            if row is None
            else Customer.model_validate(_verified("customer", _CUSTOMER_COLS, row))
        )

    def list_customers(self) -> list[Customer]:
        rows = self._con.execute(_SELECT["customer"] + " ORDER BY rowid").fetchall()
        return [Customer.model_validate(_verified("customer", _CUSTOMER_COLS, r)) for r in rows]

    # ----- promises and events ------------------------------------------------------------

    def add_promise(self, promise: NewPromise) -> Promise:
        promise_id = str(uuid.uuid4())
        today = self._clock.today().isoformat()
        unique_violation = False
        with self._transaction():
            if not self._exists("customer", "customer_id", promise.customer_id):
                raise LedgerRefusedError("customer_unknown")
            if not self._exists("run_record", "run_id", promise.run_id):
                raise LedgerRefusedError("run_unknown")
            try:
                self._con.execute(
                    _INSERT["promise"],
                    _with_hash(
                        "promise",
                        (
                            promise_id,
                            promise.customer_id,
                            promise.run_id,
                            promise.run_row_index,
                            promise.question_sha256,
                            promise.question_text,
                            promise.answer_text,
                            promise.status,
                            promise.source_path,
                            promise.doc_id,
                            promise.start_line,
                            promise.end_line,
                            promise.quote,
                            promise.cited_lines_sha256,
                            promise.cited_file_sha256,
                            int(promise.edited_after_draft),
                            promise.import_workbook_sha256,
                            promise.sent_on.isoformat(),
                            today,
                        ),
                    ),
                )
            except sqlite3.IntegrityError:
                unique_violation = True  # the UNIQUE(customer, run, row) key is the only way here
            if unique_violation:  # raised after the `except` block, so nothing is chained
                raise LedgerRefusedError("promise_exists")
            self._insert_event(promise_id, "created", None, None, today)
        stored = self.get_promise(promise_id)
        if stored is None:  # pragma: no cover  (the row was just committed)
            raise LedgerStoreError
        return stored

    def _insert_event(
        self, promise_id: str, kind: str, state: str | None, detail: str | None, on_date: str
    ) -> int:
        cursor = self._con.execute(
            _INSERT["promise_event"],
            _with_hash("promise_event", (promise_id, kind, state, detail, on_date)),
        )
        if cursor.lastrowid is None:  # pragma: no cover  (an INSERT always sets it)
            raise LedgerStoreError
        return cursor.lastrowid

    def get_promise(self, promise_id: str) -> Promise | None:
        row = self._con.execute(
            _SELECT["promise"] + " WHERE promise_id = ?", (promise_id,)
        ).fetchone()
        return (
            None
            if row is None
            else Promise.model_validate(_verified("promise", _PROMISE_COLS, row))
        )

    def list_promises(self, customer_id: str | None) -> list[Promise]:
        if customer_id is None:
            rows = self._con.execute(_SELECT["promise"] + " ORDER BY rowid").fetchall()
        else:
            rows = self._con.execute(
                _SELECT["promise"] + " WHERE customer_id = ? ORDER BY rowid", (customer_id,)
            ).fetchall()
        return [Promise.model_validate(_verified("promise", _PROMISE_COLS, r)) for r in rows]

    def add_event(self, event: NewPromiseEvent) -> PromiseEvent:
        valid = {
            "recall_state_changed": event.state is not None,
            "acknowledged": event.state is None,
            "voided": event.state is None and event.detail_code in _VOID_REASONS,
        }[event.kind]
        if not valid:
            raise LedgerRefusedError("event_invalid")
        with self._transaction():
            if not self._exists("promise", "promise_id", event.promise_id):
                raise LedgerRefusedError("promise_unknown")
            event_id = self._insert_event(
                event.promise_id,
                event.kind,
                event.state,
                event.detail_code,
                self._clock.today().isoformat(),
            )
        for stored in self.list_events(event.promise_id):
            if stored.event_id == event_id:
                return stored
        raise LedgerStoreError  # pragma: no cover  (the row was just committed)

    def list_events(self, promise_id: str) -> list[PromiseEvent]:
        rows = self._con.execute(
            _SELECT["promise_event"] + " WHERE promise_id = ? ORDER BY event_id", (promise_id,)
        ).fetchall()
        return [
            PromiseEvent.model_validate(_verified("promise_event", _EVENT_COLS, row))
            for row in rows
        ]


class SqliteLedgerRepository:
    """The async facade. Every call runs on the single DB thread; sqlite3 errors never escape."""

    def __init__(self, path: Path, clock: Clock) -> None:
        self._path = path
        self._clock = clock
        self._worker = SqliteWorker()
        self._core: _LedgerStore | None = None

    async def _run(self, call: Callable[[], T]) -> T:
        failed = False
        try:
            return await self._worker.run(call)
        except GrcError:
            raise
        except (sqlite3.Error, OSError, ValidationError):
            # sqlite3 errors can quote SQL, OS errors a path, a ValidationError the bad value
            # (a customer, a question or an answer). None of it may reach a log or a response.
            failed = True
        if failed:  # raised after the `except` block, so nothing is chained
            raise LedgerStoreError
        raise AssertionError  # pragma: no cover  (the try either returned or raised)

    def _store(self) -> _LedgerStore:
        if self._core is None:
            raise LedgerStoreError("The ledger is not open")
        return self._core

    async def open_or_create(self) -> None:
        if self._core is not None:
            return
        path, clock = self._path, self._clock
        self._core = await self._run(lambda: _LedgerStore.open(path, clock))

    async def add_run_record(self, record: NewRunRecord, answers: Sequence[RunAnswer]) -> None:
        store = self._store()
        await self._run(lambda: store.add_run_record(record, answers))

    async def get_run_record(self, run_id: str) -> RunRecord | None:
        store = self._store()
        return await self._run(lambda: store.get_run_record(run_id))

    async def list_run_answers(self, run_id: str) -> list[StoredRunAnswer]:
        store = self._store()
        return await self._run(lambda: store.list_run_answers(run_id))

    async def add_customer(self, display_name: str) -> Customer:
        store = self._store()
        return await self._run(lambda: store.add_customer(display_name))

    async def get_customer(self, customer_id: str) -> Customer | None:
        store = self._store()
        return await self._run(lambda: store.get_customer(customer_id))

    async def find_customer_by_name(self, name: str) -> Customer | None:
        store = self._store()
        return await self._run(lambda: store.find_customer_by_name(name))

    async def list_customers(self) -> list[Customer]:
        return await self._run(self._store().list_customers)

    async def add_promise(self, promise: NewPromise) -> Promise:
        store = self._store()
        return await self._run(lambda: store.add_promise(promise))

    async def get_promise(self, promise_id: str) -> Promise | None:
        store = self._store()
        return await self._run(lambda: store.get_promise(promise_id))

    async def list_promises(self, customer_id: str | None = None) -> list[Promise]:
        store = self._store()
        return await self._run(lambda: store.list_promises(customer_id))

    async def add_event(self, event: NewPromiseEvent) -> PromiseEvent:
        store = self._store()
        return await self._run(lambda: store.add_event(event))

    async def list_events(self, promise_id: str) -> list[PromiseEvent]:
        store = self._store()
        return await self._run(lambda: store.list_events(promise_id))

    async def close(self) -> None:
        store, self._core = self._core, None
        try:
            if store is not None:
                await self._run(store.close)
        finally:
            self._worker.shutdown()
