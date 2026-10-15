"""G30 (chunk 6b): the 11 hostile cell texts as REAL question cells in a generated workbook.

`test_answer_service.py` already shows that a hostile question changes nothing the code
computes. This file goes through the file path: each text in
`fixtures/adversarial-m5/questionnaire-cells.json` is written as a question cell of a generated
workbook, which then goes through the real loader, a scripted answerer (no model, no Ollama) and
the real writer, offline. Then the output file is read back.

For every cell: the status in the output is the status the code computed (a hostile question
cannot raise it), no cell the writer added is a live formula, no hyperlink exists anywhere in the
package, every original cell is unchanged, no row was added, and the input file is byte-identical.
All data is synthetic; the hostile targets (example.invalid, the Windows path) are never contacted
or opened.
"""

import asyncio
import hashlib
import json
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from openpyxl.worksheet.worksheet import Worksheet

from grc_sidecar.questionnaire.loader import load_questionnaire
from grc_sidecar.questionnaire.status import blank_result

from .ledgerkit import STATUS_COL, answered, cited
from .runskit import FILE_NAME, MAPPING, ScriptedAnswerer, outcome, setup, supported
from .vaultkit import ADVERSARIAL_M5_CELLS
from .xlsxkit import simple_workbook, to_bytes

CELLS: list[dict[str, Any]] = json.loads(ADVERSARIAL_M5_CELLS.read_text(encoding="utf-8"))["cells"]
IDS = [c["id"] for c in CELLS]
NEUTRAL_BEFORE = "Do you encrypt data at rest, neutral question one?"
NEUTRAL_AFTER = "Do you review access quarterly, neutral question three?"
HOSTILE_ROW = 3  # sheet row of the hostile question (row 1 is the header)
FORMULA_ANSWER = '=HYPERLINK("http://example.invalid/evidence","click")'
FORMULA_QUOTE = "+cmd|' /C calc'!A0 is the sentence, verbatim."


def cell_text(cell: dict[str, Any]) -> str:
    return str(cell["text"] or cell["repeat_unit"] * cell["repeat_count"])


def workbook_with(text: str, *, as_typed_string: bool = True) -> bytes:
    """Three questions; the middle one is the hostile text, stored as a string cell by default.

    openpyxl turns any string starting with `=` into a formula unless the type is set. By default
    the type is forced to a string, so the text reaches the loader as a question; the formula
    variant (the default openpyxl behaviour) is tested on its own below.
    """
    workbook = simple_workbook(
        [("Q-1", NEUTRAL_BEFORE), ("Q-2", "placeholder"), ("Q-3", NEUTRAL_AFTER)]
    )
    sheet = workbook["Questions"]
    sheet.cell(row=HOSTILE_ROW, column=2).value = text
    if as_typed_string:
        sheet.cell(row=HOSTILE_ROW, column=2).data_type = "s"
    return to_bytes(workbook)


def run_through_the_engine(
    tmp_path: Path, data: bytes, hostile_result_status: str
) -> tuple[bytes, bytes, dict[str, int], int]:
    """Input bytes, output bytes, the run's status counts and how many questions were asked."""
    hostile = (
        outcome(blank_result("no_evidence"))
        if hostile_result_status == "NEEDS_REVIEW"
        else answered(
            FORMULA_ANSWER,
            status="PARTIAL",
            reasons=("partial_coverage",),
            cite=cited(7, 8, quote=FORMULA_QUOTE),
        )
    )
    answerer = ScriptedAnswerer(script=[outcome(supported("Yes, neutral.")), hostile])
    s = setup(tmp_path, data=data, answerer=answerer)
    input_path = s.questionnaire_root / FILE_NAME

    async def scenario() -> dict[str, int]:
        state = await s.runs.start(FILE_NAME, MAPPING)
        _ = [event async for event in s.runs.events(state.run_id)]
        assert state.summary is not None and state.status == "complete"
        return dict(state.summary.counts)

    counts = asyncio.run(asyncio.wait_for(scenario(), 30))
    outputs = list(s.export_root.iterdir())
    assert len(outputs) == 1 and "UNREVIEWED-DRAFT" in outputs[0].name  # exactly one new file
    return input_path.read_bytes(), outputs[0].read_bytes(), counts, len(answerer.questions)


def grid(data: bytes) -> list[list[object]]:
    loaded = load_questionnaire(data)
    try:
        sheet = loaded.workbook["Questions"]
        assert isinstance(sheet, Worksheet)
        return [[c.value for c in row] for row in sheet.iter_rows(min_col=1, max_col=2)]
    finally:
        loaded.close()


def formula_cells(data: bytes) -> set[tuple[int, int]]:
    loaded = load_questionnaire(data)
    try:
        sheet = loaded.workbook["Questions"]
        assert isinstance(sheet, Worksheet)
        return {(c.row, c.column) for row in sheet.iter_rows() for c in row if c.data_type == "f"}
    finally:
        loaded.close()


def sheet_xml(data: bytes) -> str:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        return "".join(
            archive.read(name).decode("utf-8", "replace")
            for name in archive.namelist()
            if name.startswith("xl/worksheets/") and name.endswith(".xml")
        )


def package_names(data: bytes) -> list[str]:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        return archive.namelist()


@pytest.mark.parametrize("hostile_status", ["NEEDS_REVIEW", "PARTIAL"])
@pytest.mark.parametrize("cell", CELLS, ids=IDS)
def test_a_hostile_cell_goes_through_the_file_path_and_changes_nothing(  # [NEG]
    tmp_path: Path, cell: dict[str, Any], hostile_status: str
) -> None:
    text = cell_text(cell)
    before, after, counts, asked = run_through_the_engine(
        tmp_path, workbook_with(text), hostile_status
    )

    assert asked == 3  # the hostile cell was one question like any other
    # No status raised: the output says what the code computed, whatever the question asked for.
    assert counts == {
        "SUPPORTED": 2,  # the two neutral rows (the third is the script running out: SUPPORTED)
        "PARTIAL": int(hostile_status == "PARTIAL"),
        "NEEDS_REVIEW": int(hostile_status == "NEEDS_REVIEW"),
    }
    output = grid_with_columns(after)
    assert output[HOSTILE_ROW - 1][STATUS_COL - 1] == hostile_status
    assert output[0][STATUS_COL - 1] == "Status [UNREVIEWED DRAFT]"

    # Question cells unchanged, no extra row, no new sheet.
    assert grid(after) == grid(before)
    assert len(grid(after)) == len(grid(before)) == 4
    assert grid(after)[HOSTILE_ROW - 1][1] == text
    assert [n for n in package_names(after) if n.startswith("xl/worksheets/sheet")] == [
        n for n in package_names(before) if n.startswith("xl/worksheets/sheet")
    ]

    # No live formula and no hyperlink anywhere in what was written.
    assert formula_cells(after) == formula_cells(before) == set()
    xml = sheet_xml(after)
    assert "<f>" not in xml and "<f " not in xml and "<hyperlink" not in xml
    assert not any("hyperlink" in name.lower() for name in package_names(after))
    # A written answer that starts like a formula is stored as text.
    if hostile_status == "PARTIAL":
        assert output[HOSTILE_ROW - 1][STATUS_COL - 3] is not None  # the answer cell is text
        assert str(output[HOSTILE_ROW - 1][STATUS_COL - 3]).lstrip("'") == FORMULA_ANSWER


def grid_with_columns(data: bytes) -> list[list[object]]:
    """Every cell of the used range, including ALIBI's six new columns (3 to 8)."""
    loaded = load_questionnaire(data)
    try:
        sheet = loaded.workbook["Questions"]
        assert isinstance(sheet, Worksheet)
        return [[c.value for c in row] for row in sheet.iter_rows(min_col=1, max_col=8)]
    finally:
        loaded.close()


def test_the_input_file_is_not_touched_by_a_hostile_run(tmp_path: Path) -> None:  # [NEG]
    data = workbook_with(cell_text(CELLS[0]))
    s = setup(tmp_path, data=data, answerer=ScriptedAnswerer())
    path = s.questionnaire_root / FILE_NAME
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    async def scenario() -> None:
        state = await s.runs.start(FILE_NAME, MAPPING)
        _ = [event async for event in s.runs.events(state.run_id)]

    asyncio.run(asyncio.wait_for(scenario(), 30))
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_a_question_cell_that_is_a_formula_is_skipped_and_never_answered(  # [NEG]
    tmp_path: Path,
) -> None:
    """CELL-ADV-04 stored the default way, as a live formula: the loader skips it as a question."""
    adv_04 = next(c for c in CELLS if c["attack"] == "formula-in-question-cell")
    data = workbook_with(cell_text(adv_04), as_typed_string=False)
    assert formula_cells(data) == {(HOSTILE_ROW, 2)}  # it really is a formula in the input
    answerer = ScriptedAnswerer()
    s = setup(tmp_path, data=data, answerer=answerer)

    async def scenario() -> dict[str, int]:
        state = await s.runs.start(FILE_NAME, MAPPING)
        _ = [event async for event in s.runs.events(state.run_id)]
        assert state.summary is not None
        return dict(state.summary.counts)

    counts = asyncio.run(asyncio.wait_for(scenario(), 30))
    assert sum(counts.values()) == 2 and len(answerer.questions) == 2  # the formula row is skipped
    assert all("=1+1" not in q for q in answerer.questions)
    after = next(s.export_root.iterdir()).read_bytes()
    # The original formula cell is kept exactly as it was, and nothing else became a formula.
    assert formula_cells(after) == {(HOSTILE_ROW, 2)}
    assert len(grid(after)) == 4
    assert grid_with_columns(after)[HOSTILE_ROW - 1][2:] == [None] * 6  # no answer written there


def test_all_eleven_cells_are_covered() -> None:
    assert len(CELLS) == 11 and len(set(IDS)) == 11
