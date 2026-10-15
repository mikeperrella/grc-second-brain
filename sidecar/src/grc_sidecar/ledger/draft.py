"""The rows of a finished draft, for the review queue (ADR-021). Read-only.

Two sources are joined and neither is trusted blindly:

- the **run record** (the ledger) supplies every verdict: `status`, `reasons` and the citation.
  They are ALIBI's own conclusions, written when the run finished;
- the **workbook** supplies only text a person can read: the question and the Answer cell.

The workbook is also used to say "this cell differs from what ALIBI wrote": the Status, Answer and
Evidence cells are hashed and compared with the hashes in the run record (`cell_unchanged`, the same
check the import uses). A cell's own text is never taken as a verdict or a citation; the Status,
Evidence, Reason, Confidence and Review cells are never returned. The question cells are compared by
hash too (inside `read_reviewed`), and a single mismatch refuses the whole draft.

A draft is found by the full `file_name` recorded in the run record, never by the 8 hex digits in
the name, so a renamed copy has no record and is refused (`run_record_missing`). Nothing is
written: not a workbook, not an event, not a ledger file.
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path

from grc_sidecar.errors import (
    DraftRefusedError,
    ImportRefusedError,
    QuestionMappingError,
    WorkbookRefusedError,
)
from grc_sidecar.ledger.reviewed import ReviewedRow, cell_unchanged, read_reviewed
from grc_sidecar.questionnaire.loader import load_questionnaire, read_input_bytes
from grc_sidecar.repositories.base import LedgerRepository, StoredRunAnswer
from grc_sidecar.security.paths import resolve_within

RUN_RECORD_MISSING = "run_record_missing"


@dataclass(frozen=True)
class DraftCitation:
    """The run record's citation. Never parsed from the Evidence cell."""

    source_path: str
    start_line: int
    end_line: int
    quote: str


@dataclass(frozen=True)
class DraftRow:
    row: int
    question_text: str  # the workbook's question cell, whose hash matched the record
    status: str  # the run record's final status, never the Status cell
    reasons: tuple[str, ...]  # the run record's reason codes, never the Reason cell
    answer_text: str | None  # the Answer cell, only if the record says an answer was written
    citation: DraftCitation | None
    status_edited: bool
    answer_edited: bool
    evidence_edited: bool


@dataclass(frozen=True)
class DraftRows:
    model: str
    file_name: str
    complete: bool
    question_count: int
    rows: tuple[DraftRow, ...]


def _answer_text(answer: StoredRunAnswer, row: ReviewedRow) -> str | None:
    """The Answer cell's text, only when the record says an answer was written and it is text."""
    if answer.answer_cell_sha256 is None:
        return None
    if not row.answer_is_text or not isinstance(row.answer, str) or not row.answer.strip():
        return None
    return row.answer


def _citation(answer: StoredRunAnswer) -> DraftCitation | None:
    if (
        answer.source_path is None
        or answer.start_line is None
        or answer.end_line is None
        or answer.quote is None
    ):
        return None
    return DraftCitation(answer.source_path, answer.start_line, answer.end_line, answer.quote)


def _draft_row(answer: StoredRunAnswer, row: ReviewedRow) -> DraftRow:
    return DraftRow(
        row=answer.row_index,
        question_text=row.question_text,
        status=answer.status,
        reasons=tuple(answer.reasons),
        answer_text=_answer_text(answer, row),
        citation=_citation(answer),
        status_edited=not cell_unchanged(row.status, answer.status_cell_sha256),
        answer_edited=not cell_unchanged(row.answer, answer.answer_cell_sha256),
        evidence_edited=not cell_unchanged(row.evidence, answer.evidence_cell_sha256),
    )


async def read_draft_rows(repo: LedgerRepository, export_root: Path, file: str) -> DraftRows:
    """Every question row of a draft in the export folder, joined with its run record.

    Raises:
        PathNotAllowedError, PathNotFoundError, ConfigurationError: from the path guard.
        DraftRefusedError: `run_record_missing`, `headers_changed`, `question_mismatch` (integer
            counts only), or the loader's own refusal code. No rows are returned with a refusal.
    """
    path = resolve_within(file, export_root)
    # Drafts are written straight into the export folder, so a file anywhere deeper is a copy.
    if path.parent != export_root.resolve():
        raise DraftRefusedError(RUN_RECORD_MISSING)
    if not await repo.open_existing():  # never creates the ledger file
        raise DraftRefusedError(RUN_RECORD_MISSING)
    record = await repo.get_run_record_by_file_name(path.name)
    if record is None:
        raise DraftRefusedError(RUN_RECORD_MISSING)
    answers = await repo.list_run_answers(record.run_id)

    failure: str | None = None
    counts: dict[str, int] = {}
    reviewed = None
    try:
        # `path` is directly in the export folder (checked above), so its name is the way in.
        data = await asyncio.to_thread(read_input_bytes, path.name, export_root)
        loaded = await asyncio.to_thread(load_questionnaire, data)
        try:
            reviewed = await asyncio.to_thread(read_reviewed, loaded, record, answers)
        finally:
            loaded.close()
    except ImportRefusedError as error:
        failure, counts = error.code, error.counts
    except (WorkbookRefusedError, QuestionMappingError) as error:
        failure = error.code
    if reviewed is None:  # raised outside the except blocks, so no cause is chained
        raise DraftRefusedError(failure or "unreadable", counts)

    rows = tuple(_draft_row(answer, reviewed.rows[answer.row_index]) for answer in answers)
    return DraftRows(
        model=record.model,
        file_name=record.file_name,
        complete=record.complete,
        question_count=record.question_count,
        rows=rows,
    )
