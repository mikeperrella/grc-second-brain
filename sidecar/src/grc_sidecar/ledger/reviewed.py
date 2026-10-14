"""Reading a reviewed draft workbook against its run record (ADR-017 decisions 2 and 5).

The workbook supplies only what a person typed: the Review mark and the final Answer text. The
Status and Evidence cells are read so the import can compare them with what ALIBI wrote. No
citation is taken from the workbook. The workbook is parsed from the bytes that were hashed and is
never saved (the one place that saves a workbook is `questionnaire/writer.py`).

Where things are comes from the run record, not from the file: the sheet, the header row, the
question column and the first of ALIBI's six columns. Those six headers must still be there.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from openpyxl.worksheet.worksheet import Worksheet

from grc_sidecar.errors import ImportRefusedError
from grc_sidecar.questionnaire.grounding import normalize
from grc_sidecar.questionnaire.loader import LoadedWorkbook
from grc_sidecar.questionnaire.mapping import QuestionMapping, extract_questions
from grc_sidecar.questionnaire.writer import ANSWER, EVIDENCE, EXPORT_HEADERS, REVIEW, STATUS
from grc_sidecar.repositories.base import RunAnswer, RunRecord
from grc_sidecar.vault.normalize import sha256_hex

# openpyxl cell types that hold what a person typed as text: "s" (a string) and "f" (a typed
# formula, kept as its text and never evaluated). A number, a date, a boolean or an error value is
# never an answer.
TEXT_TYPES = frozenset({"s", "f"})


@dataclass(frozen=True)
class ReviewedRow:
    """The cells of one question row, as read. Nothing here has been interpreted yet."""

    row: int
    question_text: str
    review: object
    answer: object
    answer_is_text: bool
    status: object
    evidence: object


@dataclass(frozen=True)
class ReviewedWorkbook:
    rows: dict[int, ReviewedRow]  # by row number, one per row the run record knows


def read_reviewed(
    loaded: LoadedWorkbook, record: RunRecord, answers: Sequence[RunAnswer]
) -> ReviewedWorkbook:
    """The recorded rows of a reviewed workbook, or a whole-import refusal.

    Raises:
        ImportRefusedError: `headers_changed` (ALIBI's six headers are not where the record says),
            or `question_mismatch` (a recorded row's question differs, or the workbook has more or
            fewer question rows than the record; counts only).
        QuestionMappingError: the sheet cannot be read as a question sheet (too large, bad
            position).
    """
    workbook = loaded.workbook
    if record.sheet not in workbook.sheetnames:
        raise ImportRefusedError("headers_changed")
    sheet = workbook[record.sheet]
    if not isinstance(sheet, Worksheet):
        raise ImportRefusedError("headers_changed")
    for offset, title in enumerate(EXPORT_HEADERS):
        cell = sheet.cell(row=record.header_row, column=record.first_new_column + offset)
        if cell.value != title:
            raise ImportRefusedError("headers_changed")

    mapping = QuestionMapping(
        sheet=record.sheet, header_row=record.header_row, question_col=record.question_col
    )
    questions = {
        q.row: q for q in extract_questions(workbook, mapping, reading_a_draft=True).questions
    }
    recorded = {answer.row_index: answer for answer in answers}
    mismatched = sum(
        1
        for row, answer in recorded.items()
        if row not in questions
        or sha256_hex(normalize(questions[row].text)) != answer.question_sha256
    ) + sum(1 for row in questions if row not in recorded)
    if mismatched:
        raise ImportRefusedError(
            "question_mismatch",
            {
                "mismatched_rows": mismatched,
                "workbook_rows": len(questions),
                "recorded_rows": len(recorded),
            },
        )

    first = record.first_new_column
    rows: dict[int, ReviewedRow] = {}
    for row, question in questions.items():
        answer_cell = sheet.cell(row=row, column=first + ANSWER)
        rows[row] = ReviewedRow(
            row=row,
            question_text=question.text,
            review=sheet.cell(row=row, column=first + REVIEW).value,
            answer=answer_cell.value,
            answer_is_text=answer_cell.data_type in TEXT_TYPES,
            status=sheet.cell(row=row, column=first + STATUS).value,
            evidence=sheet.cell(row=row, column=first + EVIDENCE).value,
        )
    return ReviewedWorkbook(rows=rows)
