"""Builders for the ledger import tests (ADR-017 decisions 1, 2, 4, 5). Offline; all data synthetic.

A draft is made by a real questionnaire run: the real loader and writer, the real run recorder and
a real `SqliteLedgerRepository` in `tmp_path`. Only the answerer (scripted), the gate, the digest
reader and the cited-file reader are fakes. The owner's edits (typing `approved`, changing a cell)
are simulated by loading the saved draft, changing cells and writing the bytes back in place, which
is what Excel's Save does.
"""

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from openpyxl.worksheet.worksheet import Worksheet

from grc_sidecar.ledger.service import CustomerChoice, ImportOutcome, LedgerService
from grc_sidecar.questionnaire.loader import load_questionnaire
from grc_sidecar.questionnaire.models import AnswerResult, Citation
from grc_sidecar.questionnaire.runs import CitedReader
from grc_sidecar.questionnaire.runtime import LedgerRunRecorder
from grc_sidecar.questionnaire.service import AnswerOutcome
from grc_sidecar.repositories.base import Promise
from grc_sidecar.repositories.sqlite_ledger_repo import SqliteLedgerRepository
from grc_sidecar.vault.scan import CitedFile, ReadReason

from .clockkit import ManualClock
from .runskit import FILE_HASH, FILE_NAME, MAPPING, Setup, citation, outcome, setup, supported
from .xlsxkit import to_bytes

T = TypeVar("T")

SCENARIO_TIMEOUT_SECONDS = 30  # a broken control must fail a test, not hang the suite
# The sample sheet has the question in column 2; ALIBI's six columns start at column 3.
QUESTION_COL = 2
ANSWER_COL, CONFIDENCE_COL, STATUS_COL, REASON_COL, EVIDENCE_COL, REVIEW_COL = range(3, 9)
SENT_ON = "2026-10-10"  # the fake clock's day, and the day the run record is dated


def run(awaitable: Awaitable[T]) -> T:
    return asyncio.run(asyncio.wait_for(awaitable, SCENARIO_TIMEOUT_SECONDS))  # type: ignore[arg-type]


def lines_hash(start: int) -> str:
    """The fake cited-lines hash for a range starting at `start` (so a test can tell rows apart)."""
    return f"{start:064x}"


def good_reader(_path: str, ranges: Sequence[tuple[int, int]]) -> CitedFile | ReadReason:
    """Every cited file reads, and its hash is the one the answers carry: every baseline is kept."""
    return CitedFile(FILE_HASH, tuple(lines_hash(start) for start, _ in ranges))


def cited(start: int, end: int, *, quote: str = "Data at rest is encrypted.") -> Citation:
    return dataclasses.replace(citation(), start_line=start, end_line=end, quote=quote)


def answered(
    answer: str, *, status: str = "SUPPORTED", reasons: tuple[str, ...] = (), cite: Citation
) -> AnswerOutcome:
    result = AnswerResult(
        status=status,  # type: ignore[arg-type]
        reasons=reasons,  # type: ignore[arg-type]
        answer=answer,
        model_confidence=0.95,
        citation=cite,
    )
    return outcome(result)


def supported_outcomes(count: int) -> list[AnswerOutcome]:
    return [outcome(supported(f"Yes, answer {n}.")) for n in range(1, count + 1)]


@dataclass
class Draft:
    """A finished run: its draft file in the export folder, its run record in a real ledger."""

    s: Setup
    repo: SqliteLedgerRepository
    service: LedgerService
    run_id: str
    path: Path
    clock: ManualClock

    @property
    def export_root(self) -> Path:
        return self.s.export_root


@contextmanager
def draft_for(
    tmp_path: Path,
    outcomes: Sequence[AnswerOutcome],
    *,
    data: bytes | None = None,
    read_cited: CitedReader = good_reader,
) -> Iterator[Draft]:
    """Run a scripted questionnaire once and yield the draft. The ledger is closed on exit."""
    clock = ManualClock()
    repo = SqliteLedgerRepository(tmp_path / "ledger" / "ledger.sqlite3", clock)
    s = setup(
        tmp_path,
        count=len(outcomes),
        data=data,
        run_recorder=LedgerRunRecorder(repo),
        read_cited=read_cited,
    )
    s.answerer.script = list(outcomes)

    async def scenario() -> tuple[str, str]:
        state = await s.runs.start(FILE_NAME, MAPPING)
        _ = [event async for event in s.runs.events(state.run_id)]
        assert state.summary is not None and state.summary.file_name is not None
        assert state.summary.warnings == ()  # the run record was kept
        return state.run_id, state.summary.file_name

    run_id, file_name = run(scenario())
    try:
        yield Draft(
            s=s,
            repo=repo,
            service=LedgerService(repo, s.export_root, clock),
            run_id=run_id,
            path=s.export_root / file_name,
            clock=clock,
        )
    finally:
        run(repo.close())


def edit_sheet(
    draft: Draft, change: Callable[[Worksheet], None], *, name: str | None = None
) -> str:
    """The owner edits the draft and presses Save: change the sheet, write the bytes back in place.

    With `name` the result goes to another file in the export folder instead. Returns the file name.
    """
    loaded = load_questionnaire(draft.path.read_bytes())
    try:
        change(loaded.workbook[MAPPING.sheet])
        data = to_bytes(loaded.workbook)
    finally:
        loaded.close()
    target = draft.path if name is None else draft.path.with_name(name)
    target.write_bytes(data)
    return target.name


def edit_cells(
    draft: Draft, cells: Mapping[tuple[int, int], object], *, name: str | None = None
) -> str:
    def change(sheet: Worksheet) -> None:
        for (row, column), value in cells.items():
            sheet.cell(row=row, column=column).value = value

    return edit_sheet(draft, change, name=name)


def approve(draft: Draft, *rows: int, mark: object = "approved") -> None:
    """Type `mark` in the Review column of these rows (row 2 is the first question)."""
    edit_cells(draft, {(row, REVIEW_COL): mark for row in rows})


def import_rows(
    draft: Draft,
    *,
    choice: CustomerChoice | None = None,
    new_name: str = "Acme Test Co",
    sent_on: str = SENT_ON,
    file: str | None = None,
    run_id: str | None = None,
) -> ImportOutcome:
    """Call the same service the route calls, with a new customer unless `choice` says otherwise."""
    chosen = choice or CustomerChoice(new_customer_name=new_name, confirm_new=True)
    return run(
        draft.service.import_reviewed(
            run_id or draft.run_id, file or draft.path.name, chosen, sent_on
        )
    )


def promises_of(draft: Draft) -> list[Promise]:
    return run(draft.repo.list_promises())
