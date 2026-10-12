"""Finding and reading the questions in a workbook (ADR-014)."""

import pytest
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Font
from pydantic import ValidationError

from grc_sidecar.errors import QuestionMappingError
from grc_sidecar.questionnaire import mapping as mapping_module
from grc_sidecar.questionnaire.loader import load_questionnaire
from grc_sidecar.questionnaire.mapping import (
    MAX_QUESTIONS,
    QuestionMapping,
    extract_questions,
    suggest_mapping,
)

from .xlsxkit import to_bytes


def reloaded(workbook: Workbook) -> Workbook:
    """Through bytes and the real loader, as a run sees it."""
    return load_questionnaire(to_bytes(workbook)).workbook


def questionnaire(
    rows: list[tuple[object, ...]], headers: tuple[str, ...] = ("ID", "Question")
) -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Questions"
    sheet.append(list(headers))
    for row in rows:
        sheet.append(list(row))
    return workbook


def refused(workbook: Workbook, mapping: QuestionMapping) -> str:
    with pytest.raises(QuestionMappingError) as caught:
        extract_questions(workbook, mapping)
    return caught.value.code


M = QuestionMapping(sheet="Questions", header_row=1, question_col=2, id_col=1)


# ----- suggest_mapping -----------------------------------------------------------------------


def test_the_header_words_are_recognised() -> None:
    workbook = reloaded(questionnaire([("Q-1", "Is data encrypted?")]))
    assert suggest_mapping(workbook) == M


def test_the_header_row_need_not_be_the_first_row() -> None:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Vendor"
    sheet.append(["Acme vendor security questionnaire"])
    sheet.append([])
    sheet.append(["Section", "Question Number", "Question / Request"])
    sheet.append(["1", "1.1", "Is data encrypted?"])
    suggestion = suggest_mapping(reloaded(workbook))
    assert suggestion == QuestionMapping(sheet="Vendor", header_row=3, question_col=3, id_col=2)


def test_a_workbook_without_a_question_header_gets_no_suggestion() -> None:  # [NEG]
    assert suggest_mapping(reloaded(questionnaire([], headers=("Name", "Notes")))) is None


def test_a_hidden_sheet_is_never_suggested() -> None:  # [NEG]
    workbook = questionnaire([("Q-1", "Is data encrypted?")])
    sheet = workbook.active
    assert sheet is not None
    sheet.sheet_state = "hidden"
    other = workbook.create_sheet("Notes")
    other.append(["Name"])
    assert suggest_mapping(reloaded(workbook)) is None


def test_the_id_column_is_optional() -> None:
    workbook = questionnaire([("Is data encrypted?",)], headers=("Question",))
    assert suggest_mapping(reloaded(workbook)) == QuestionMapping(
        sheet="Questions", header_row=1, question_col=1, id_col=None
    )


def test_suggesting_changes_nothing() -> None:
    workbook = reloaded(questionnaire([("Q-1", "Is data encrypted?"), ("Q-2", "Is MFA required?")]))
    sheet = workbook["Questions"]
    before = (sheet.max_row, sheet.max_column, [[c.value for c in r] for r in sheet.iter_rows()])
    suggest_mapping(workbook)
    extract_questions(workbook, M)
    after = (sheet.max_row, sheet.max_column, [[c.value for c in r] for r in sheet.iter_rows()])
    assert before == after


# ----- extract_questions ---------------------------------------------------------------------


def test_questions_are_read_in_order_with_their_ids() -> None:
    workbook = reloaded(questionnaire([("Q-1", "Is data encrypted?"), ("Q-2", "Is MFA required?")]))
    result = extract_questions(workbook, M)
    assert [(q.sheet, q.row, q.ref_id, q.text) for q in result.questions] == [
        ("Questions", 2, "Q-1", "Is data encrypted?"),
        ("Questions", 3, "Q-2", "Is MFA required?"),
    ]
    assert result.skipped == ()


def test_the_text_is_kept_as_written() -> None:
    workbook = reloaded(questionnaire([("Q-1", "  Is  data encrypted?\n(yes/no)  ")]))
    assert extract_questions(workbook, M).questions[0].text == "  Is  data encrypted?\n(yes/no)  "


def test_a_missing_id_is_none_and_a_number_question_is_text() -> None:
    workbook = reloaded(questionnaire([(None, 42), ("  ", "Is MFA required?")]))
    questions = extract_questions(workbook, M).questions
    assert [(q.ref_id, q.text) for q in questions] == [(None, "42"), (None, "Is MFA required?")]


def test_blank_rows_are_skipped_and_reported() -> None:
    workbook = reloaded(questionnaire([("Q-1", "Is data encrypted?"), (None, None), ("Q-3", "  ")]))
    result = extract_questions(workbook, M)
    assert len(result.questions) == 1
    assert [(s.row, s.kind) for s in result.skipped] == [(3, "blank"), (4, "blank")]


def test_a_formula_in_the_question_cell_is_skipped_not_asked() -> None:  # [NEG]
    workbook = reloaded(questionnaire([("Q-1", '=HYPERLINK("http://example.invalid","Is it?")')]))
    result = extract_questions(workbook, M)
    assert result.questions == ()
    assert [(s.row, s.kind) for s in result.skipped] == [(2, "formula")]


def test_a_bold_single_cell_row_is_a_section_header() -> None:
    workbook = questionnaire([("Q-1", "Is data encrypted?"), (None, "Access control")])
    sheet = workbook.active
    assert sheet is not None
    sheet["B3"].font = Font(bold=True)
    result = extract_questions(reloaded(workbook), M)
    assert [q.row for q in result.questions] == [2]
    assert [(s.row, s.kind) for s in result.skipped] == [(3, "section_header")]


def test_a_merged_single_cell_row_is_a_section_header() -> None:
    workbook = questionnaire(
        [(None, "Access control"), ("Q-2", "Is MFA required?")], ("ID", "Question", "Answer")
    )
    sheet = workbook.active
    assert sheet is not None
    sheet.merge_cells("B2:C2")
    result = extract_questions(reloaded(workbook), M)
    assert [q.row for q in result.questions] == [3]
    assert [(s.row, s.kind) for s in result.skipped] == [(2, "section_header")]


def test_a_plain_one_cell_row_is_still_a_question() -> None:  # [NEG]
    # Skipping a real question silently is worse than asking about a heading, so the rule needs
    # bold or a merge as well as an otherwise empty row.
    workbook = reloaded(questionnaire([(None, "Is the data centre ISO 27001 certified?")]))
    result = extract_questions(workbook, M)
    assert [q.row for q in result.questions] == [2]
    assert result.skipped == ()


def test_a_bold_row_with_other_cells_filled_is_a_question() -> None:  # [NEG]
    workbook = questionnaire([("Q-1", "Is data encrypted?")])
    sheet = workbook.active
    assert sheet is not None
    sheet["B2"].font = Font(bold=True)
    assert len(extract_questions(reloaded(workbook), M).questions) == 1


# ----- the mapping must fit the sheet --------------------------------------------------------


@pytest.mark.parametrize(
    ("mapping", "code"),
    [
        (QuestionMapping(sheet="Nope", header_row=1, question_col=2), "sheet_not_found"),
        (QuestionMapping(sheet="Questions", header_row=50, question_col=2), "bad_position"),
        (QuestionMapping(sheet="Questions", header_row=1, question_col=9), "bad_position"),
        (
            QuestionMapping(sheet="Questions", header_row=1, question_col=2, id_col=9),
            "bad_position",
        ),
        (
            QuestionMapping(sheet="Questions", header_row=1, question_col=2, id_col=2),
            "bad_position",
        ),
    ],
)
def test_a_mapping_that_does_not_fit_is_refused(
    mapping: QuestionMapping, code: str
) -> None:  # [NEG]
    assert refused(reloaded(questionnaire([("Q-1", "Is data encrypted?")])), mapping) == code


def test_a_chart_sheet_cannot_be_mapped() -> None:  # [NEG]
    source = questionnaire([("Q-1", "Is data encrypted?")])
    chart = BarChart()  # an empty chart sheet does not load in openpyxl, so it holds a chart
    chart.add_data(Reference(source.worksheets[0], min_col=1, min_row=1, max_row=2))
    source.create_chartsheet("Chart").add_chart(chart)
    workbook = reloaded(source)
    assert "Chart" in workbook.sheetnames
    assert (
        refused(workbook, QuestionMapping(sheet="Chart", header_row=1, question_col=1))
        == "sheet_not_found"
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"sheet": "", "header_row": 1, "question_col": 1},
        {"sheet": "Q", "header_row": 0, "question_col": 1},
        {"sheet": "Q", "header_row": 1, "question_col": 0},
        {"sheet": "Q", "header_row": 1, "question_col": 1, "id_col": 0},
        {"sheet": "Q", "header_row": 1, "question_col": 99_999},
        {"sheet": "Q", "header_row": 1, "question_col": 1, "extra": 1},
    ],
)
def test_the_mapping_model_rejects_impossible_values(bad: dict[str, object]) -> None:  # [NEG]
    with pytest.raises(ValidationError):
        QuestionMapping.model_validate(bad)


# ----- a workbook that already carries ALIBI's columns ---------------------------------------


@pytest.mark.parametrize(
    "header",
    [
        "GRC Answer [UNREVIEWED DRAFT]",
        "grc answer",
        "Confidence (model self-rating) [UNREVIEWED DRAFT]",
        "Status [UNREVIEWED DRAFT]",
    ],
)
def test_an_exported_draft_is_not_answered_again(header: str) -> None:  # [NEG]
    workbook = questionnaire([("Q-1", "Is data encrypted?")], ("ID", "Question", header))
    assert refused(reloaded(workbook), M) == "already_has_answer_columns"


def test_an_ordinary_status_column_is_not_mistaken_for_ours() -> None:
    workbook = questionnaire([("Q-1", "Is data encrypted?", "Open")], ("ID", "Question", "Status"))
    assert len(extract_questions(reloaded(workbook), M).questions) == 1


# ----- size limits ---------------------------------------------------------------------------


def test_the_question_limit_is_enforced_at_501() -> None:  # [NEG]
    rows = [(f"Q-{n}", f"Question number {n} about controls?") for n in range(1, MAX_QUESTIONS + 2)]
    assert refused(reloaded(questionnaire(rows)), M) == "too_many_questions"


def test_exactly_the_limit_is_accepted() -> None:
    rows = [(f"Q-{n}", f"Question number {n} about controls?") for n in range(1, MAX_QUESTIONS + 1)]
    assert len(extract_questions(reloaded(questionnaire(rows)), M).questions) == MAX_QUESTIONS


def test_a_sparse_sheet_with_a_far_cell_is_refused_before_any_cell_is_visited() -> None:  # [NEG]
    # One cell at row 200000 would make a scan of the rectangle create about a million cells.
    workbook = questionnaire([("Q-1", "Is data encrypted?")])
    sheet = workbook.active
    assert sheet is not None
    sheet["E200000"] = "x"
    loaded = reloaded(workbook)
    assert refused(loaded, M) == "sheet_too_large"
    assert suggest_mapping(loaded) is None  # too big to guess on, so no guess, not a crash


def test_the_sheet_size_limit_is_a_documented_constant() -> None:
    assert mapping_module.MAX_SHEET_CELLS == 250_000
