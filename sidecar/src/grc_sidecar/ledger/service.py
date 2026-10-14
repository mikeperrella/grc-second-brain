"""The ledger service: import a reviewed workbook, list customers and promises (ADR-017).

An import creates promises only from rows a person approved, and takes everything that matters
(status, citation, baseline hashes) from ALIBI's own run record, never from the workbook. The order
of the checks is the contract:

1. no run record, then the workbook (path guard, loader, headers, every question row);
2. the customer, then the sent date;
3. per row, the first rule that applies: not approved, no answer, status edited, evidence edited,
   no baseline, already imported. Every skip has a code and a count; none is silent.

A new customer is made only when at least one promise will be created, after every refusal, because
the ledger is append-only and a refused import must leave nothing behind. Promises are written one
transaction each, so an import that is interrupted can simply be run again: the finished rows are
skipped as `already_imported`.

Nothing here logs. Customer names, questions and answers pass through this module and reach only
the ledger and the authenticated response.
"""

import asyncio
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final

from grc_sidecar.config import Settings
from grc_sidecar.errors import (
    ExportRootNotConfiguredError,
    ImportRefusedError,
    LedgerRefusedError,
)
from grc_sidecar.ledger.review import ReviewMark, classify_review, shorten_other
from grc_sidecar.ledger.reviewed import ReviewedRow, read_reviewed
from grc_sidecar.questionnaire.loader import load_questionnaire, read_input_bytes
from grc_sidecar.repositories.base import (
    Customer,
    LedgerRepository,
    NewPromise,
    Promise,
    PromiseStatus,
    RunAnswer,
)
from grc_sidecar.repositories.sqlite_ledger_repo import (
    SqliteLedgerRepository,
    ledger_path,
    validate_customer_name,
)
from grc_sidecar.vault.clock import Clock
from grc_sidecar.vault.normalize import sha256_hex

SKIP_CODES: Final = (
    "not_approved",
    "no_answer",
    "status_edited",
    "evidence_edited",
    "baseline_unavailable",
    "already_imported",
)
PAGE_SIZE: Final = 100  # a starting value, unmeasured (M5.5 plan, Section 3)
_ISO_DATE: Final = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class CustomerChoice:
    """Either an existing customer, or a new name the owner has confirmed. Exactly one."""

    existing_customer_id: str | None = None
    new_customer_name: str | None = None
    confirm_new: bool = False


@dataclass(frozen=True)
class OtherValue:
    """A Review value that is not one of the three words, shortened for display."""

    text: str
    truncated: bool
    count: int


@dataclass(frozen=True)
class ImportOutcome:
    """What an import did. Counts and ids only, plus the shortened 'other' Review values."""

    created: int
    created_edited: int
    customer_id: str | None  # None when nothing was created for a new name
    customer_created: bool
    skipped: dict[str, int]
    review: dict[str, int]
    other_values: tuple[OtherValue, ...]
    promise_ids: tuple[str, ...]


@dataclass(frozen=True)
class PromiseView:
    promise: Promise
    voided: bool


@dataclass(frozen=True)
class PromisePage:
    page: int
    page_size: int
    total: int
    items: tuple[PromiseView, ...]


@dataclass(frozen=True)
class _Baseline:
    """A recorded citation with every field present (a promise needs all of them)."""

    source_path: str
    doc_id: str
    start_line: int
    end_line: int
    quote: str
    cited_lines_sha256: str
    cited_file_sha256: str


def _baseline(answer: RunAnswer) -> _Baseline | None:
    if (
        answer.source_path is None
        or answer.doc_id is None
        or answer.start_line is None
        or answer.end_line is None
        or answer.quote is None
        or answer.cited_lines_sha256 is None
        or answer.cited_file_sha256 is None
    ):
        return None
    return _Baseline(
        answer.source_path,
        answer.doc_id,
        answer.start_line,
        answer.end_line,
        answer.quote,
        answer.cited_lines_sha256,
        answer.cited_file_sha256,
    )


def _answer_text(row: ReviewedRow) -> str | None:
    """The Answer cell's text, or None if it is blank or not text (never converted to text)."""
    if not row.answer_is_text or not isinstance(row.answer, str) or not row.answer.strip():
        return None
    return row.answer


def _unchanged(cell: object, recorded_sha256: str | None) -> bool:
    """Is the cell exactly what ALIBI wrote? A blank cell matches only 'nothing was written'."""
    if cell is None:
        return recorded_sha256 is None
    return (
        isinstance(cell, str)
        and recorded_sha256 is not None
        and sha256_hex(cell) == recorded_sha256
    )


@dataclass(frozen=True)
class _Ready:
    """A row that passed every check, with the values the promise is made from."""

    answer: RunAnswer
    row: ReviewedRow
    text: str
    baseline: _Baseline


def _plan(
    mark: ReviewMark,
    answer: RunAnswer,
    row: ReviewedRow,
    imported: set[tuple[str, int]],
) -> str | _Ready:
    """The skip code of the first rule that applies (the contract's order), else the row to make."""
    text = _answer_text(row)
    baseline = _baseline(answer)
    if mark != "approved":
        return "not_approved"
    if answer.status == "NEEDS_REVIEW" or text is None:
        return "no_answer"
    if not _unchanged(row.status, answer.status_cell_sha256):
        return "status_edited"
    if not _unchanged(row.evidence, answer.evidence_cell_sha256):
        return "evidence_edited"
    if baseline is None:
        return "baseline_unavailable"
    if (answer.run_id, answer.row_index) in imported:
        return "already_imported"
    return _Ready(answer, row, text, baseline)


class LedgerService:
    """The ledger's operations. `export_root` is None when no export folder is configured."""

    def __init__(self, repo: LedgerRepository, export_root: Path | None, clock: Clock) -> None:
        self._repo = repo
        self._export_root = export_root
        self._clock = clock

    # ----- import ------------------------------------------------------------------------------

    async def import_reviewed(
        self, run_id: str, file: str, customer: CustomerChoice, sent_on: str
    ) -> ImportOutcome:
        """Turn the approved rows of a reviewed draft into promises. See the module docstring."""
        root = self._export_root
        if root is None:
            raise ExportRootNotConfiguredError
        await self._repo.open_or_create()
        record = await self._repo.get_run_record(run_id)
        if record is None:
            raise ImportRefusedError("run_record_missing")
        answers = await self._repo.list_run_answers(run_id)

        data = await asyncio.to_thread(read_input_bytes, file, root)
        loaded = await asyncio.to_thread(load_questionnaire, data)
        try:
            workbook_sha256 = loaded.scan.sha256  # the hash of the very bytes that were parsed
            reviewed = await asyncio.to_thread(read_reviewed, loaded, record, answers)
        finally:
            loaded.close()

        existing, new_name = await self._resolve_customer(customer)
        sent = self._checked_sent_on(sent_on, record.created_on)

        imported: set[tuple[str, int]] = set()
        if existing is not None:
            imported = {
                (p.run_id, p.run_row_index)
                for p in await self._repo.list_promises(existing.customer_id)
            }
        skipped = dict.fromkeys(SKIP_CODES, 0)
        marks: Counter[str] = Counter()
        others: Counter[tuple[str, bool]] = Counter()
        pending: list[_Ready] = []
        for answer in answers:
            row = reviewed.rows[answer.row_index]
            mark = classify_review(row.review)
            marks[mark] += 1
            if mark == "other":
                others[shorten_other(row.review)] += 1
            planned = _plan(mark, answer, row, imported)
            if isinstance(planned, _Ready):
                pending.append(planned)
            else:
                skipped[planned] += 1

        customer_id = None if existing is None else existing.customer_id
        customer_created = False
        if pending and customer_id is None and new_name is not None:
            customer_id = (await self._repo.add_customer(new_name)).customer_id
            customer_created = True

        created_ids: list[str] = []
        edited = 0
        if customer_id is not None:  # always true when anything is pending
            for ready in pending:
                promise = await self._create(customer_id, ready, workbook_sha256, sent)
                if promise is None:
                    skipped["already_imported"] += 1
                    continue
                created_ids.append(promise.promise_id)
                edited += int(promise.edited_after_draft)
        return ImportOutcome(
            created=len(created_ids),
            created_edited=edited,
            customer_id=customer_id,
            customer_created=customer_created,
            skipped=skipped,
            review={
                mark: marks.get(mark, 0) for mark in ("approved", "rejected", "unreviewed", "other")
            },
            other_values=tuple(
                OtherValue(text, truncated, count)
                for (text, truncated), count in sorted(others.items())
            ),
            promise_ids=tuple(created_ids),
        )

    async def _resolve_customer(self, choice: CustomerChoice) -> tuple[Customer | None, str | None]:
        """(the existing customer, None) or (None, the new name). Creates nothing."""
        existing_id, name = choice.existing_customer_id, choice.new_customer_name
        if (existing_id is None) == (name is None):
            raise ImportRefusedError("customer_choice_invalid")
        if existing_id is not None:
            found = await self._repo.get_customer(existing_id)
            if found is None:
                raise LedgerRefusedError("customer_unknown")
            return found, None
        if name is None:  # unreachable after the check above; keeps the types honest
            raise ImportRefusedError("customer_choice_invalid")
        if not choice.confirm_new:
            raise ImportRefusedError("new_customer_not_confirmed")
        validate_customer_name(name)
        clash = await self._repo.find_customer_by_name(name)
        if clash is not None:
            raise LedgerRefusedError("customer_exists", customer_id=clash.customer_id)
        return None, name

    def _checked_sent_on(self, sent_on: str, drafted_on: date) -> date:
        if _ISO_DATE.fullmatch(sent_on) is None:
            raise ImportRefusedError("sent_on_invalid")
        try:
            sent = date.fromisoformat(sent_on)
        except ValueError:
            raise ImportRefusedError("sent_on_invalid") from None
        if sent > self._clock.today():
            raise ImportRefusedError("sent_on_in_future")
        if sent < drafted_on:
            raise ImportRefusedError("sent_before_drafted")
        return sent

    async def _create(
        self, customer_id: str, ready: _Ready, workbook_sha256: str, sent: date
    ) -> Promise | None:
        """One promise, or None if it already exists. The citation is the run record's, only."""
        answer, row, text, baseline = ready.answer, ready.row, ready.text, ready.baseline
        status: PromiseStatus = "SUPPORTED" if answer.status == "SUPPORTED" else "PARTIAL"
        try:
            return await self._repo.add_promise(
                NewPromise(
                    customer_id=customer_id,
                    run_id=answer.run_id,
                    run_row_index=answer.row_index,
                    question_sha256=answer.question_sha256,
                    question_text=row.question_text,
                    answer_text=text,
                    status=status,
                    source_path=baseline.source_path,
                    doc_id=baseline.doc_id,
                    start_line=baseline.start_line,
                    end_line=baseline.end_line,
                    quote=baseline.quote,
                    cited_lines_sha256=baseline.cited_lines_sha256,
                    cited_file_sha256=baseline.cited_file_sha256,
                    edited_after_draft=sha256_hex(text) != answer.answer_cell_sha256,
                    import_workbook_sha256=workbook_sha256,
                    sent_on=sent,
                )
            )
        except LedgerRefusedError as error:
            if error.code == "promise_exists":
                return None
            raise

    # ----- listings ----------------------------------------------------------------------------

    async def customers(self) -> list[Customer]:
        await self._repo.open_or_create()
        return await self._repo.list_customers()

    async def promises(
        self, customer_id: str | None, include_voided: bool, page: int
    ) -> PromisePage:
        """One page of promises, oldest first. Voided ones are left out unless asked for.

        A promise is voided when any of its events is `voided`. That is one query per promise, which
        is fine at a single owner's scale and is recorded as a limit in the ADR.
        """
        await self._repo.open_or_create()
        found = await self._repo.list_promises(customer_id)
        if include_voided:
            window = found[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
            views = [PromiseView(p, await self._is_voided(p)) for p in window]
            return PromisePage(page, PAGE_SIZE, len(found), tuple(views))
        kept = [p for p in found if not await self._is_voided(p)]
        window = kept[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
        return PromisePage(page, PAGE_SIZE, len(kept), tuple(PromiseView(p, False) for p in window))

    async def _is_voided(self, promise: Promise) -> bool:
        events = await self._repo.list_events(promise.promise_id)
        return any(event.kind == "voided" for event in events)


def build_ledger_service(
    settings: Settings, existing: LedgerRepository | None, clock: Clock
) -> tuple[LedgerService | None, Callable[[], Awaitable[None]] | None]:
    """The service for these settings, and what has to be closed (None if nothing was opened here).

    `existing` is the repository the questionnaire runtime already built: there is one repository
    per process, never two connections to one file. With no ledger folder there is no ledger.
    """
    if existing is not None:
        return LedgerService(existing, settings.export_dir, clock), None
    if settings.ledger_dir is None:
        return None, None
    repo = SqliteLedgerRepository(ledger_path(settings), clock)
    return LedgerService(repo, settings.export_dir, clock), repo.close
