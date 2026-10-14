"""Writing the answers file (CLAUDE.md 8.4, ADR-014). The only module that saves a workbook.

The output is a new file with new columns to the right of the used range: the question cells are
never changed, no row is added, and the input file is never written to. The order is the point:

1. Work out every cell to write, from results that can only have been lowered (a cited file that
   changed since retrieval blanks its answer: `lower_for_changed_source`).
2. Write them into the parsed workbook, refusing any target cell that is not empty.
3. Save to memory, read that back through the loader and compare it with what was intended: every
   original cell, the sheet order, merged ranges, data validations, row counts, and each written
   cell (a string, typed as one, with the quote prefix where the text starts like a formula, and
   never a hyperlink). Parts openpyxl dropped are reported, not fatal.
4. Re-read the input file and compare its hash with the one taken when it was read. A difference
   means the answers belong to a file that no longer exists: nothing is written.
5. Create the output with exclusive create (`"xb"`), so an existing file of any kind is never
   overwritten. Read it back and compare the hash with the bytes saved; on a mismatch remove the
   file just created, and only that file, and fail.

A same-user process could swap a file between steps 4 and 5; the threat model records that as out
of scope (O1). Both sides of the comparison in step 3 are openpyxl's view, so live check B opens
the result in Excel.
"""

import contextlib
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Final

from openpyxl.cell.cell import MergedCell
from openpyxl.workbook.workbook import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from grc_sidecar.errors import WorkbookWriteError
from grc_sidecar.questionnaire.grounding import normalize
from grc_sidecar.questionnaire.loader import LoadedWorkbook, load_questionnaire
from grc_sidecar.questionnaire.mapping import QuestionMapping, QuestionRow
from grc_sidecar.questionnaire.models import AnswerResult, Citation, Status
from grc_sidecar.questionnaire.package import InventoryChange, compare_inventories, sha256_hex
from grc_sidecar.questionnaire.sanitize import (
    DIRECTION_MARKER,
    clean_cell_text,
    contains_direction_controls,
    needs_quote_prefix,
    remove_direction_controls,
)
from grc_sidecar.questionnaire.status import lower_for_changed_source
from grc_sidecar.security.paths import resolve_within
from grc_sidecar.vault.normalize import sha256_hex as sha256_text

EXPORT_HEADERS: Final = (
    "GRC Answer [UNREVIEWED DRAFT]",
    "Confidence (model self-rating) [UNREVIEWED DRAFT]",
    "Status [UNREVIEWED DRAFT]",
    "Reason",
    "Evidence",
    "Review",
)
ANSWER, CONFIDENCE, STATUS, REASON, EVIDENCE, REVIEW = range(len(EXPORT_HEADERS))

MARKER: Final = "UNREVIEWED-DRAFT"
_UNSAFE_NAME_CHARS: Final = re.compile(r"[^A-Za-z0-9._ -]")
_RUN_ID: Final = re.compile(r"[0-9a-f]{8,64}")
MAX_STEM_CHARS: Final = 80


@dataclass(frozen=True)
class ExportedRow:
    """What the run record keeps of one question row (ADR-017 decision 3): hashes, not cell text.

    `status`, `reasons` and `citation` are those of the FINAL result, after the changed-source
    lowering. Each cell hash is sha256 (UTF-8) of the string exactly as stored in the saved cell,
    or None when no cell was written. The question hash is of the whitespace-normalized text.
    """

    row: int
    question_sha256: str
    status: Status
    reasons: tuple[str, ...]
    citation: Citation | None
    answer_cell_sha256: str | None
    status_cell_sha256: str
    evidence_cell_sha256: str | None


@dataclass(frozen=True)
class ExportResult:
    """What was written. `path` is internal; callers show `file_name`, never the folder.

    `rows` is for the run record only: it never goes into a summary, an event or a log.
    """

    path: Path
    file_name: str
    output_sha256: str
    rows_written: int
    first_new_column: int
    dropped: tuple[InventoryChange, ...]
    warnings: tuple[str, ...]
    rows: tuple[ExportedRow, ...] = ()


@dataclass(frozen=True)
class _Cell:
    """One cell to write: text, or a number (the confidence), or nothing (a blank)."""

    row: int
    column: int
    text: str | None = None
    number: float | None = None


def output_name(source_name: str, run_id: str, *, macro_enabled: bool) -> str:
    """`<stem>-UNREVIEWED-DRAFT-<run8>.<ext>`, built from characters that are safe in a file name.

    The source name is user input: only its last path component is used, its extension is dropped,
    and every character outside letters, digits, dot, space, hyphen and underscore becomes `_`.
    """
    if _RUN_ID.fullmatch(run_id) is None:
        raise WorkbookWriteError("bad_output_name")
    last = re.split(r"[\\/]", source_name)[-1]
    stem = last.rpartition(".")[0] or last
    stem = _UNSAFE_NAME_CHARS.sub("_", stem).strip(" .")[:MAX_STEM_CHARS].strip(" .")
    stem = stem or "questionnaire"
    extension = "xlsm" if macro_enabled else "xlsx"
    return f"{stem}-{MARKER}-{run_id[:8]}.{extension}"


def _evidence_text(result: AnswerResult) -> str | None:
    citation = result.citation
    if citation is None:
        return None
    lines = (
        f"L{citation.start_line}"
        if citation.start_line == citation.end_line
        else f"L{citation.start_line}-L{citation.end_line}"
    )
    evidence = f'{citation.source_path}:{lines} "{citation.quote}"'
    if contains_direction_controls(evidence):
        return DIRECTION_MARKER + evidence  # the quote stays verbatim; the reviewer is warned
    return evidence


def _cells_for(question: QuestionRow, result: AnswerResult, first_column: int) -> list[_Cell]:
    """The cells for one question. A blank answer is left empty: no empty string is written."""
    row = question.row
    cells = [
        _Cell(row, first_column + STATUS, text=result.status),
        _Cell(row, first_column + REVIEW, text=result.review_state),
    ]
    if result.answer is not None:
        answer = remove_direction_controls(result.answer)  # model output, not a quote
        cells.append(_Cell(row, first_column + ANSWER, text=answer))
    if result.model_confidence is not None:
        cells.append(
            _Cell(row, first_column + CONFIDENCE, number=round(result.model_confidence, 2))
        )
    if result.reasons:
        cells.append(_Cell(row, first_column + REASON, text="; ".join(result.reasons)))
    evidence = _evidence_text(result)
    if evidence is not None:
        cells.append(_Cell(row, first_column + EVIDENCE, text=evidence))
    return cells


def _put_text(sheet: Worksheet, row: int, column: int, text: str) -> str:
    """Store text as a typed string, with the quote prefix if it starts like a formula.

    Setting `value` on a string that starts with `=` would make a formula, so the type is set
    explicitly afterwards. Returns the text as stored (cleaned).
    """
    cleaned = clean_cell_text(text)
    cell = sheet.cell(row=row, column=column)
    cell.value = cleaned
    cell.data_type = "s"
    if needs_quote_prefix(cleaned):
        cell.quotePrefix = True
    return cleaned


def _target_must_be_empty(sheet: Worksheet, row: int, column: int) -> None:
    cell = sheet.cell(row=row, column=column)
    if isinstance(cell, MergedCell) or cell.value is not None:
        raise WorkbookWriteError("target_not_empty")


def _snapshot(workbook: Workbook) -> dict[tuple[str, str], tuple[object, str]]:
    """Every non-empty cell as (sheet, coordinate) -> (value, data type)."""
    found: dict[tuple[str, str], tuple[object, str]] = {}
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if cell.value is not None:
                    found[(sheet.title, cell.coordinate)] = (cell.value, cell.data_type)
    return found


def _sheet_facts(workbook: Workbook) -> list[tuple[str, int, tuple[str, ...], tuple[str, ...]]]:
    """Per sheet, in order: title, row count, merged ranges, data-validation ranges."""
    return [
        (
            sheet.title,
            sheet.max_row,
            tuple(sorted(str(r) for r in sheet.merged_cells.ranges)),
            tuple(sorted(str(v.sqref) for v in sheet.data_validations.dataValidation)),
        )
        for sheet in workbook.worksheets
    ]


def _verify(
    saved: bytes,
    original_values: dict[tuple[str, str], tuple[object, str]],
    original_facts: list[tuple[str, int, tuple[str, ...], tuple[str, ...]]],
    sheet_name: str,
    written: dict[tuple[int, int], tuple[str | float, bool]],
    original_columns: int,
) -> LoadedWorkbook:
    """Read the saved bytes back and compare them with what was meant. Raises on any difference.

    The caller closes the returned workbook. If the comparison fails it is closed here, so a macro
    project that openpyxl keeps open inside the workbook is never left to the garbage collector.
    """
    reopened = load_questionnaire(saved)
    try:
        _compare(reopened, original_values, original_facts, sheet_name, written, original_columns)
    except WorkbookWriteError:
        reopened.close()
        raise
    return reopened


def _compare(
    reopened: LoadedWorkbook,
    original_values: dict[tuple[str, str], tuple[object, str]],
    original_facts: list[tuple[str, int, tuple[str, ...], tuple[str, ...]]],
    sheet_name: str,
    written: dict[tuple[int, int], tuple[str | float, bool]],
    original_columns: int,
) -> None:
    workbook = reopened.workbook
    sheet = workbook[sheet_name]
    if _sheet_facts(workbook) != original_facts:
        raise WorkbookWriteError("verify_failed")
    if sheet.max_column != original_columns + len(EXPORT_HEADERS):
        raise WorkbookWriteError("verify_failed")
    after = _snapshot(workbook)
    for key, expected in original_values.items():
        if after.get(key) != expected:
            raise WorkbookWriteError("verify_failed")
    for (row, column), (value, prefixed) in written.items():
        cell = sheet.cell(row=row, column=column)
        stored_as_intended = _stored_as_intended(cell.value, value)
        string_ok = isinstance(value, float) or (
            cell.data_type == "s" and cell.quotePrefix == prefixed
        )
        if not stored_as_intended or not string_ok or cell.hyperlink is not None:
            raise WorkbookWriteError("verify_failed")


def _stored_as_intended(stored: object, intended: str | float) -> bool:
    """Is the cell read back the same as what was written, in value and in kind?

    A number is compared as a number, not by Python type: the file stores 0.0 and 1.0 as "0" and
    "1", so openpyxl reads them back as ints (found by the M5l end-to-end run, where a model that
    said confidence 0.0 made every run fail). A bool is not a number here. Text must come back as
    exactly the same `str`.
    """
    if isinstance(intended, float):
        return (
            isinstance(stored, int | float) and not isinstance(stored, bool) and stored == intended
        )
    return type(stored) is str and stored == intended


def _read_back(path: Path) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def _create_exclusive(path: Path, data: bytes) -> None:
    """Create the file, failing if anything of that name exists. Never overwrites."""
    try:
        with open(path, "xb") as handle:
            handle.write(data)
    except FileExistsError:
        raise WorkbookWriteError("output_exists") from None
    except OSError:
        _remove_if_present(path)
        raise WorkbookWriteError("write_failed") from None


def _remove_if_present(path: Path) -> None:
    """Remove the one file this call created. The path is the one just created, never a pattern."""
    with contextlib.suppress(OSError):
        os.remove(path)


def _lowered(result: AnswerResult, current_sha256: Callable[[str], str | None]) -> AnswerResult:
    """The result after the changed-source check: a cited file that changed blanks the answer."""
    citation = result.citation
    if citation is None:
        return result
    return lower_for_changed_source(result, current_sha256(citation.source_path))


def write_answers(
    loaded: LoadedWorkbook,
    mapping: QuestionMapping,
    questions: Sequence[QuestionRow],
    results: Mapping[int, AnswerResult],
    *,
    export_root: Path,
    source_name: str,
    run_id: str,
    read_input: Callable[[], bytes],
    current_sha256: Callable[[str], str | None],
) -> ExportResult:
    """Write the answers file for a run. See the module docstring for the order of the steps.

    `results` maps a question's row number to its result and must hold one for every question
    (a run that stopped early supplies blank results with `model_unavailable` for the rest).
    `read_input` re-reads the input file; `current_sha256` gives the hash of a vault file as it
    is now (None if it cannot be read), for the changed-source check.

    Raises:
        WorkbookWriteError: nothing was written (or a file written wrongly was removed).
    """
    name = output_name(source_name, run_id, macro_enabled=loaded.scan.macro_enabled)
    if any(question.row not in results for question in questions):
        raise WorkbookWriteError("answers_incomplete")
    root = resolve_within(".", export_root)  # strict: exists, is a directory, links followed

    workbook = loaded.workbook
    sheet = workbook[mapping.sheet]
    original_values = _snapshot(workbook)
    original_facts = _sheet_facts(workbook)
    original_columns = sheet.max_column
    first_column = original_columns + 1

    written: dict[tuple[int, int], tuple[str | float, bool]] = {}
    for offset, title in enumerate(EXPORT_HEADERS):
        _target_must_be_empty(sheet, mapping.header_row, first_column + offset)
        stored = _put_text(sheet, mapping.header_row, first_column + offset, title)
        written[(mapping.header_row, first_column + offset)] = (stored, needs_quote_prefix(stored))
    exported: list[ExportedRow] = []
    for question in questions:
        final = _lowered(results[question.row], current_sha256)
        stored_hashes: dict[int, str] = {}  # column offset -> sha256 of the string as stored
        for cell in _cells_for(question, final, first_column):
            _target_must_be_empty(sheet, cell.row, cell.column)
            if cell.number is not None:
                sheet.cell(row=cell.row, column=cell.column).value = cell.number
                written[(cell.row, cell.column)] = (cell.number, False)
            elif cell.text is not None:
                stored = _put_text(sheet, cell.row, cell.column, cell.text)
                written[(cell.row, cell.column)] = (stored, needs_quote_prefix(stored))
                stored_hashes[cell.column - first_column] = sha256_text(stored)
        exported.append(
            ExportedRow(
                row=question.row,
                question_sha256=sha256_text(normalize(question.text)),
                status=final.status,
                reasons=final.reasons,
                citation=final.citation,
                answer_cell_sha256=stored_hashes.get(ANSWER),
                status_cell_sha256=stored_hashes[STATUS],
                evidence_cell_sha256=stored_hashes.get(EVIDENCE),
            )
        )

    buffer = BytesIO()
    workbook.save(buffer)
    saved = buffer.getvalue()
    reopened = _verify(
        saved, original_values, original_facts, mapping.sheet, written, original_columns
    )
    dropped = compare_inventories(loaded.scan.inventory, reopened.scan.inventory)
    reopen_warnings = reopened.warnings
    reopened.close()

    if sha256_hex(read_input()) != loaded.scan.sha256:
        raise WorkbookWriteError("input_changed")

    path = root / name
    _create_exclusive(path, saved)
    output_hash = sha256_hex(saved)
    if sha256_hex(_read_back(path)) != output_hash:
        _remove_if_present(path)
        raise WorkbookWriteError("readback_mismatch")
    return ExportResult(
        path=path,
        file_name=name,
        output_sha256=output_hash,
        rows_written=len(questions),
        first_new_column=first_column,
        dropped=dropped,
        warnings=reopen_warnings,
        rows=tuple(exported),
    )
