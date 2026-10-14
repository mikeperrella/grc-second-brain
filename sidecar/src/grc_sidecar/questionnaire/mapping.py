"""Finding and reading the questions in a loaded workbook (CLAUDE.md 8.4, ADR-014).

`suggest_mapping` only suggests: it guesses a sheet, a header row and the question and id columns
from the header words, changes nothing, and may return None. A run uses a mapping the caller names
explicitly (`QuestionMapping`), so a wrong guess can never silently pick the questions.

`extract_questions` reads the rows under the header row. A row is skipped, and the skip is reported,
when its question cell is empty, holds a formula, or is a section header. A row is treated as a
section header only when the question cell is the single non-empty cell in the row and is bold or
sits in a horizontally merged range: a plain one-cell row is read as a question, because skipping a
real question silently is worse than asking a model about a heading.

A workbook that already carries ALIBI's answer columns is refused, so an exported draft is never fed
back in and answered again.
"""

from dataclasses import dataclass
from typing import Final, Literal

from openpyxl.workbook.workbook import Workbook
from openpyxl.worksheet.worksheet import Worksheet
from pydantic import BaseModel, ConfigDict, Field

from grc_sidecar.errors import QuestionMappingError

MAX_QUESTIONS: Final = 500
# A worksheet bigger than this (rows x columns) is refused before any cell is visited: reading a
# cell creates it, so a sparse sheet with one far-away cell could otherwise cost gigabytes.
MAX_SHEET_CELLS: Final = 250_000
HEADER_SEARCH_ROWS: Final = 30
HEADER_SEARCH_COLUMNS: Final = 100
MAX_COLUMN_INDEX: Final = 16_384  # Excel's last column, XFD

QUESTION_HEADERS: Final = frozenset(
    {
        "question",
        "questions",
        "question text",
        "control question",
        "security question",
        "question/request",
        "question / request",
        "questions/requests",
        "requirement",
        "requirements",
        "control description",
        "control specification",
    }
)
ID_HEADERS: Final = frozenset(
    {
        "id",
        "question id",
        "question number",
        "question no",
        "question #",
        "ref",
        "ref #",
        "reference",
        "control id",
        "number",
        "no",
        "no.",
        "#",
    }
)

# What the writer's own headers start with (casefolded). A header row holding any of them is an
# exported draft.
OWN_HEADER_PREFIXES: Final = (
    "grc answer",
    "confidence (model self-rating)",
    "status [unreviewed draft]",
)

SkipKind = Literal["blank", "section_header", "formula"]


class QuestionMapping(BaseModel):
    """Where the questions are: a sheet, its header row, the question column and optional id column.

    Rows and columns count from 1, as in Excel.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sheet: str = Field(min_length=1, max_length=64)
    header_row: int = Field(ge=1, le=1_048_576)
    question_col: int = Field(ge=1, le=MAX_COLUMN_INDEX)
    id_col: int | None = Field(default=None, ge=1, le=MAX_COLUMN_INDEX)


@dataclass(frozen=True)
class QuestionRow:
    """One question: where it is, its id cell if the mapping names one, and its text as written."""

    sheet: str
    row: int
    ref_id: str | None
    text: str


@dataclass(frozen=True)
class SkippedRow:
    row: int
    kind: SkipKind


@dataclass(frozen=True)
class QuestionSet:
    mapping: QuestionMapping
    questions: tuple[QuestionRow, ...]
    skipped: tuple[SkippedRow, ...]


def _norm(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.casefold().split()).rstrip(":*").strip()


def _check_sheet_size(sheet: Worksheet) -> None:
    if sheet.max_row * sheet.max_column > MAX_SHEET_CELLS:
        raise QuestionMappingError("sheet_too_large")


def suggest_mapping(workbook: Workbook) -> QuestionMapping | None:
    """Guess the mapping from header words. Reads only the first rows; changes nothing.

    The first visible sheet that has a header row with a recognised question header wins. Within
    that row the leftmost question header and the leftmost id header are used. Returns None when
    nothing matches.
    """
    for sheet in workbook.worksheets:
        if sheet.sheet_state != "visible":
            continue
        if sheet.max_row * sheet.max_column > MAX_SHEET_CELLS:
            continue
        last_row = min(sheet.max_row, HEADER_SEARCH_ROWS)
        last_col = min(sheet.max_column, HEADER_SEARCH_COLUMNS)
        for row in range(1, last_row + 1):
            headers = [
                _norm(sheet.cell(row=row, column=col).value) for col in range(1, last_col + 1)
            ]
            question = next((c for c, h in enumerate(headers, 1) if h in QUESTION_HEADERS), None)
            if question is None:
                continue
            ident = next(
                (c for c, h in enumerate(headers, 1) if h in ID_HEADERS and c != question), None
            )
            return QuestionMapping(
                sheet=sheet.title, header_row=row, question_col=question, id_col=ident
            )
    return None


def _horizontal_merges(sheet: Worksheet) -> list[tuple[int, int, int, int]]:
    return [
        (r.min_row, r.max_row, r.min_col, r.max_col)
        for r in sheet.merged_cells.ranges
        if r.max_col > r.min_col
    ]


def _is_empty(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _refuse_if_already_answered(sheet: Worksheet, header_row: int) -> None:
    for col in range(1, min(sheet.max_column, MAX_COLUMN_INDEX) + 1):
        text = _norm(sheet.cell(row=header_row, column=col).value)
        if text.startswith(OWN_HEADER_PREFIXES):
            raise QuestionMappingError("already_has_answer_columns")


def _validated_sheet(workbook: Workbook, mapping: QuestionMapping) -> Worksheet:
    if mapping.sheet not in workbook.sheetnames:
        raise QuestionMappingError("sheet_not_found")
    sheet = workbook[mapping.sheet]
    if not isinstance(sheet, Worksheet):
        raise QuestionMappingError("sheet_not_found")  # a chart sheet has no cells
    _check_sheet_size(sheet)
    if mapping.id_col == mapping.question_col:
        raise QuestionMappingError("bad_position")
    if mapping.header_row > sheet.max_row or mapping.question_col > sheet.max_column:
        raise QuestionMappingError("bad_position")
    if mapping.id_col is not None and mapping.id_col > sheet.max_column:
        raise QuestionMappingError("bad_position")
    return sheet


def extract_questions(
    workbook: Workbook, mapping: QuestionMapping, *, reading_a_draft: bool = False
) -> QuestionSet:
    """Read the questions under the header row, reporting every row that was skipped.

    `reading_a_draft` is for the reviewed-workbook import (ADR-017): a draft has ALIBI's answer
    columns by design, so only that one refusal is skipped. Everything else is unchanged.

    Raises:
        QuestionMappingError: the mapping does not fit the sheet, the sheet is too large, the sheet
            already has ALIBI's columns (unless `reading_a_draft`), or it holds more than
            MAX_QUESTIONS questions.
    """
    sheet = _validated_sheet(workbook, mapping)
    if not reading_a_draft:
        _refuse_if_already_answered(sheet, mapping.header_row)
    merges = _horizontal_merges(sheet)
    questions: list[QuestionRow] = []
    skipped: list[SkippedRow] = []
    for row in range(mapping.header_row + 1, sheet.max_row + 1):
        cell = sheet.cell(row=row, column=mapping.question_col)
        value = cell.value
        if _is_empty(value):
            skipped.append(SkippedRow(row, "blank"))
            continue
        if cell.data_type == "f":
            skipped.append(SkippedRow(row, "formula"))
            continue
        others_empty = all(
            _is_empty(sheet.cell(row=row, column=col).value)
            for col in range(1, sheet.max_column + 1)
            if col != mapping.question_col
        )
        merged = any(
            lo_r <= row <= hi_r and lo_c <= mapping.question_col <= hi_c
            for lo_r, hi_r, lo_c, hi_c in merges
        )
        if others_empty and (bool(cell.font.b) or merged):
            skipped.append(SkippedRow(row, "section_header"))
            continue
        ref_id: str | None = None
        if mapping.id_col is not None:
            raw = sheet.cell(row=row, column=mapping.id_col).value
            ref_id = None if _is_empty(raw) else str(raw).strip()
        questions.append(QuestionRow(sheet.title, row, ref_id, str(value)))
        if len(questions) > MAX_QUESTIONS:
            raise QuestionMappingError("too_many_questions")
    return QuestionSet(mapping, tuple(questions), tuple(skipped))
