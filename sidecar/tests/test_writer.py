"""Writing the answers file (ADR-014): new columns only, verified, never overwriting anything."""

import hashlib
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from openpyxl import Workbook
from openpyxl.worksheet.datavalidation import DataValidation

from grc_sidecar.errors import ConfigurationError, WorkbookWriteError
from grc_sidecar.questionnaire import writer
from grc_sidecar.questionnaire.loader import LoadedWorkbook, load_questionnaire
from grc_sidecar.questionnaire.mapping import QuestionMapping, QuestionRow, extract_questions
from grc_sidecar.questionnaire.models import AnswerResult, Citation
from grc_sidecar.questionnaire.sanitize import DIRECTION_MARKER
from grc_sidecar.questionnaire.writer import (
    EXPORT_HEADERS,
    ExportResult,
    output_name,
    write_answers,
)

from .xlsxkit import MACRO_TYPE, rebuild, simple_bytes, simple_workbook, to_bytes, with_content_type

MAPPING = QuestionMapping(sheet="Questions", header_row=1, question_col=2, id_col=1)
RUN_ID = "0123456789abcdef0123456789abcdef"
FILE_HASH = "a" * 64
FIRST = 3  # the first new column: the sheet has two columns (ID, Question)
ANSWER_COL, CONFIDENCE_COL, STATUS_COL, REASON_COL, EVIDENCE_COL, REVIEW_COL = range(3, 9)


def citation(sha: str = FILE_HASH, start: int = 12, end: int = 13) -> Citation:
    return Citation(
        doc_id="ENC-001",
        source_path="policies/encryption-policy.md",
        start_line=start,
        end_line=end,
        quote="Data at rest is encrypted with AES-256.",
        file_sha256=sha,
    )


def supported(answer: str = "Yes. Data at rest is encrypted.", **kwargs: object) -> AnswerResult:
    base: dict[str, object] = {
        "status": "SUPPORTED",
        "reasons": (),
        "answer": answer,
        "model_confidence": 0.95,
        "citation": citation(),
    }
    base.update(kwargs)
    return AnswerResult(**base)  # type: ignore[arg-type]


def blank(*reasons: str) -> AnswerResult:
    return AnswerResult(
        status="NEEDS_REVIEW",
        reasons=reasons,  # type: ignore[arg-type]
        answer=None,
        model_confidence=None,
        citation=None,
    )


_OPEN: list[LoadedWorkbook] = []


@pytest.fixture(autouse=True)
def close_every_loaded_workbook() -> Iterator[None]:
    """Close what each test loaded, so a macro project is never left to the garbage collector."""
    yield
    for loaded in _OPEN:
        loaded.close()
    _OPEN.clear()


class Run:
    """One export of a small questionnaire, with every seam a test may need to change."""

    def __init__(self, tmp_path: Path, data: bytes | None = None, count: int = 3) -> None:
        self.data = data if data is not None else simple_bytes(count)
        self.loaded: LoadedWorkbook = load_questionnaire(self.data)
        _OPEN.append(self.loaded)
        self.questions: tuple[QuestionRow, ...] = extract_questions(
            self.loaded.workbook, MAPPING
        ).questions
        self.export_root = tmp_path / "exports"
        self.export_root.mkdir(exist_ok=True)
        self.results: dict[int, AnswerResult] = {q.row: supported() for q in self.questions}
        self.read_input: Callable[[], bytes] = lambda: self.data
        self.current_sha256: Callable[[str], str | None] = lambda _path: FILE_HASH

    def write(self) -> ExportResult:
        return write_answers(
            self.loaded,
            MAPPING,
            self.questions,
            self.results,
            export_root=self.export_root,
            source_name="Vendor Questionnaire.xlsx",
            run_id=RUN_ID,
            read_input=self.read_input,
            current_sha256=self.current_sha256,
        )

    def files(self) -> list[str]:
        return sorted(p.name for p in self.export_root.iterdir())


def reopen(result: ExportResult) -> Workbook:
    loaded = load_questionnaire(result.path.read_bytes())
    _OPEN.append(loaded)
    return loaded.workbook


# ----- what is written, and where ------------------------------------------------------------


def test_headers_and_answers_go_to_new_columns_right_of_the_used_range(tmp_path: Path) -> None:
    run = Run(tmp_path)
    result = run.write()
    sheet = reopen(result)["Questions"]
    assert result.first_new_column == FIRST
    assert [sheet.cell(row=1, column=c).value for c in range(FIRST, FIRST + 6)] == list(
        EXPORT_HEADERS
    )
    assert sheet.cell(row=2, column=ANSWER_COL).value == "Yes. Data at rest is encrypted."
    assert sheet.cell(row=2, column=CONFIDENCE_COL).value == 0.95
    assert sheet.cell(row=2, column=STATUS_COL).value == "SUPPORTED"
    assert sheet.cell(row=2, column=REVIEW_COL).value == "unreviewed"
    assert sheet.cell(row=2, column=EVIDENCE_COL).value == (
        'policies/encryption-policy.md:L12-L13 "Data at rest is encrypted with AES-256."'
    )


def test_the_original_cells_and_the_row_count_are_unchanged(tmp_path: Path) -> None:
    run = Run(tmp_path)
    before = run.loaded.workbook["Questions"]
    original = {c.coordinate: c.value for row in before.iter_rows() for c in row if c.value}
    rows = before.max_row
    sheet = reopen(run.write())["Questions"]
    assert sheet.max_row == rows  # no row was added or inserted
    for coordinate, value in original.items():
        assert sheet[coordinate].value == value


def test_the_titles_carry_the_draft_marker_and_the_file_name_does_too(tmp_path: Path) -> None:
    result = Run(tmp_path).write()
    assert all("UNREVIEWED DRAFT" in t for t in EXPORT_HEADERS[:3])
    assert result.file_name == "Vendor Questionnaire-UNREVIEWED-DRAFT-01234567.xlsx"
    assert result.path == tmp_path / "exports" / result.file_name


def test_a_needs_review_answer_cell_stays_blank(tmp_path: Path) -> None:  # [NEG]
    run = Run(tmp_path)
    run.results[2] = blank("no_evidence")
    sheet = reopen(run.write())["Questions"]
    assert sheet.cell(row=2, column=ANSWER_COL).value is None  # blank, not an empty string
    assert sheet.cell(row=2, column=STATUS_COL).value == "NEEDS_REVIEW"
    assert sheet.cell(row=2, column=REASON_COL).value == "no_evidence"
    assert sheet.cell(row=2, column=CONFIDENCE_COL).value is None
    assert sheet.cell(row=2, column=EVIDENCE_COL).value is None


def test_every_reason_is_listed_in_order(tmp_path: Path) -> None:
    run = Run(tmp_path)
    run.results[2] = supported(status="PARTIAL", reasons=("review_overdue", "lexical_only"))
    assert reopen(run.write())["Questions"].cell(row=2, column=REASON_COL).value == (
        "review_overdue; lexical_only"
    )


def test_a_single_line_citation_prints_one_line_number(tmp_path: Path) -> None:
    run = Run(tmp_path)
    run.results[2] = supported(citation=citation(start=7, end=7))
    evidence = reopen(run.write())["Questions"].cell(row=2, column=EVIDENCE_COL).value
    assert evidence.startswith("policies/encryption-policy.md:L7 ")


def test_blank_and_section_rows_get_nothing(tmp_path: Path) -> None:
    workbook = simple_workbook(
        [("Q-1", "Is data encrypted?"), (None, None), ("Q-3", "Is MFA required?")]
    )
    run = Run(tmp_path, data=to_bytes(workbook))
    assert [q.row for q in run.questions] == [2, 4]
    sheet = reopen(run.write())["Questions"]
    assert [sheet.cell(row=3, column=c).value for c in range(FIRST, FIRST + 6)] == [None] * 6


def test_only_one_file_is_created(tmp_path: Path) -> None:
    run = Run(tmp_path)
    result = run.write()
    assert run.files() == [result.file_name]
    assert result.output_sha256 == hashlib.sha256(result.path.read_bytes()).hexdigest()


def test_a_macro_package_is_written_with_its_project_and_an_xlsm_name(tmp_path: Path) -> None:
    project = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
    data = with_content_type(
        rebuild(simple_bytes(), add={"xl/vbaProject.bin": project}), MACRO_TYPE
    )
    result = Run(tmp_path, data=data).write()
    assert result.file_name.endswith(".xlsm")
    assert "xl/vbaProject.bin" in zipfile.ZipFile(result.path).namelist()


def test_merged_ranges_and_dropdowns_survive(tmp_path: Path) -> None:
    workbook = simple_workbook([("Q-1", "Is data encrypted?"), (None, "Access control")])
    sheet = workbook.active
    assert sheet is not None
    sheet.merge_cells("A4:B4")
    validation = DataValidation(type="list", formula1='"yes,no"', allow_blank=True)
    sheet.add_data_validation(validation)
    validation.add("C2:C9")
    run = Run(tmp_path, data=to_bytes(workbook))
    reopened = reopen(run.write())["Questions"]
    assert [str(r) for r in reopened.merged_cells.ranges] == ["A4:B4"]
    assert [str(v.sqref) for v in reopened.data_validations.dataValidation] == ["C2:C9"]


def test_a_dropped_part_is_reported_not_fatal(tmp_path: Path) -> None:
    # openpyxl does not keep printer settings, so a package that had them loses them on save.
    data = rebuild(simple_bytes(), add={"xl/printerSettings/printerSettings1.bin": b"\x00\x01"})
    result = Run(tmp_path, data=data).write()
    assert [(c.category, c.before, c.after) for c in result.dropped] == [("printer_settings", 1, 0)]
    assert result.path.exists()


def test_nothing_is_reported_dropped_for_a_plain_workbook(tmp_path: Path) -> None:
    assert Run(tmp_path).write().dropped == ()


# ----- text from the model and the vault is untrusted ----------------------------------------


@pytest.mark.parametrize("trigger", ["=", "+", "-", "@", "\t", "\r"])
def test_formula_trigger(trigger: str, tmp_path: Path) -> None:  # [NEG]
    run = Run(tmp_path)
    hostile = f'{trigger}HYPERLINK("http://example.invalid","x")'
    run.results[2] = supported(answer=hostile)
    cell = reopen(run.write())["Questions"].cell(row=2, column=ANSWER_COL)
    assert cell.data_type == "s"  # a string, never a formula
    assert cell.value == hostile.replace("\r", "\n")  # the text, unchanged but for the line ending
    assert cell.quotePrefix is True


def test_the_quote_prefix_is_not_added_where_it_is_not_needed(tmp_path: Path) -> None:
    run = Run(tmp_path)
    run.results[2] = supported(answer="Encryption = AES-256 (see -note)")
    cell = reopen(run.write())["Questions"].cell(row=2, column=ANSWER_COL)
    assert (cell.data_type, cell.quotePrefix) == ("s", False)


def test_a_formula_in_the_evidence_quote_is_neutralized_too(tmp_path: Path) -> None:  # [NEG]
    run = Run(tmp_path)
    hostile = Citation("D", "=cmd.md", 1, 1, "x", FILE_HASH)
    run.results[2] = supported(citation=hostile)
    # the Evidence text starts with the path, which here is "=cmd.md"
    cell = reopen(run.write())["Questions"].cell(row=2, column=EVIDENCE_COL)
    assert (cell.data_type, cell.quotePrefix) == ("s", True)


@pytest.mark.parametrize("control", ["‮", "⁦", "‏", "؜"])
def test_direction_controls_are_removed_from_the_answer(
    control: str, tmp_path: Path
) -> None:  # [NEG]
    run = Run(tmp_path)
    run.results[2] = supported(answer=f"Yes{control}, data at rest is encrypted")
    cell = reopen(run.write())["Questions"].cell(row=2, column=ANSWER_COL)
    assert cell.value == "Yes, data at rest is encrypted"


def test_a_direction_control_cannot_hide_a_formula_trigger(tmp_path: Path) -> None:  # [NEG]
    # Removed first, so the text that is stored is the text that is checked for a trigger.
    run = Run(tmp_path)
    run.results[2] = supported(answer="‮=1+1")
    cell = reopen(run.write())["Questions"].cell(row=2, column=ANSWER_COL)
    assert (cell.value, cell.data_type, cell.quotePrefix) == ("=1+1", "s", True)


def test_a_quote_with_a_direction_control_stays_verbatim_and_is_marked(
    tmp_path: Path,
) -> None:  # [NEG]
    run = Run(tmp_path)
    quote = "Data at rest is ‮encrypted‬ with AES-256."
    run.results[2] = supported(citation=Citation("D", "policies/p.md", 4, 4, quote, FILE_HASH))
    evidence = reopen(run.write())["Questions"].cell(row=2, column=EVIDENCE_COL).value
    assert evidence == f'{DIRECTION_MARKER}policies/p.md:L4 "{quote}"'
    assert quote in evidence  # verbatim, controls included


def test_a_path_with_a_direction_control_is_marked_too(tmp_path: Path) -> None:  # [NEG]
    run = Run(tmp_path)
    run.results[2] = supported(citation=Citation("D", "policies/‮p.md", 4, 4, "q", FILE_HASH))
    evidence = reopen(run.write())["Questions"].cell(row=2, column=EVIDENCE_COL).value
    assert evidence.startswith(DIRECTION_MARKER)


def test_a_quote_without_direction_controls_has_no_marker(tmp_path: Path) -> None:
    evidence = reopen(Run(tmp_path).write())["Questions"].cell(row=2, column=EVIDENCE_COL).value
    assert DIRECTION_MARKER not in evidence
    assert evidence.startswith("policies/encryption-policy.md:L12-L13")


def test_no_hyperlink_is_ever_set(tmp_path: Path) -> None:  # [NEG]
    run = Run(tmp_path)
    run.results[2] = supported(answer="https://example.invalid/page and www.example.invalid")
    result = run.write()
    sheet = reopen(result)["Questions"]
    assert all(cell.hyperlink is None for row in sheet.iter_rows() for cell in row)
    with zipfile.ZipFile(result.path) as archive:
        sheet_xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert "hyperlink" not in sheet_xml.lower()


def test_unsafe_characters_in_an_answer_do_not_break_the_file(tmp_path: Path) -> None:  # [NEG]
    run = Run(tmp_path)
    run.results[2] = supported(answer="Yes\x00\x0b\ud800 encrypted")
    assert reopen(run.write())["Questions"].cell(row=2, column=ANSWER_COL).value == "Yes encrypted"


# ----- what is checked before and after the file is created ----------------------------------


def test_a_changed_source_blanks_the_answer(tmp_path: Path) -> None:  # [NEG]
    run = Run(tmp_path)
    run.current_sha256 = lambda _path: "b" * 64
    sheet = reopen(run.write())["Questions"]
    assert sheet.cell(row=2, column=ANSWER_COL).value is None
    assert sheet.cell(row=2, column=STATUS_COL).value == "NEEDS_REVIEW"
    assert sheet.cell(row=2, column=REASON_COL).value == "source_changed"


def test_an_unchanged_source_keeps_the_answer(tmp_path: Path) -> None:
    sheet = reopen(Run(tmp_path).write())["Questions"]
    assert sheet.cell(row=2, column=STATUS_COL).value == "SUPPORTED"


def test_an_unreadable_source_blanks_the_answer(tmp_path: Path) -> None:  # [NEG]
    run = Run(tmp_path)
    run.current_sha256 = lambda _path: None
    sheet = reopen(run.write())["Questions"]
    assert sheet.cell(row=2, column=STATUS_COL).value == "NEEDS_REVIEW"


def test_a_missing_result_writes_nothing(tmp_path: Path) -> None:  # [NEG]
    run = Run(tmp_path)
    del run.results[3]
    with pytest.raises(WorkbookWriteError) as caught:
        run.write()
    assert caught.value.code == "answers_incomplete"
    assert run.files() == []


def test_input_changed_during_the_run_writes_nothing(tmp_path: Path) -> None:  # [NEG]
    run = Run(tmp_path)
    run.read_input = lambda: simple_bytes(9)  # the file on disk is no longer the one answered
    with pytest.raises(WorkbookWriteError) as caught:
        run.write()
    assert caught.value.code == "input_changed"
    assert run.files() == []


def test_an_existing_output_is_never_overwritten(tmp_path: Path) -> None:  # [NEG]
    run = Run(tmp_path)
    existing = run.export_root / output_name(
        "Vendor Questionnaire.xlsx", RUN_ID, macro_enabled=False
    )
    existing.write_bytes(b"precious")
    with pytest.raises(WorkbookWriteError) as caught:
        run.write()
    assert caught.value.code == "output_exists"
    assert existing.read_bytes() == b"precious"


def test_the_input_file_is_never_written_to(tmp_path: Path) -> None:  # [NEG]
    folder = tmp_path / "questionnaires"
    folder.mkdir()
    source = folder / "Vendor Questionnaire.xlsx"
    source.write_bytes(simple_bytes())
    before = source.read_bytes()
    run = Run(tmp_path, data=before)
    run.export_root = folder  # an export folder that is also the input folder
    result = run.write()
    assert source.read_bytes() == before
    assert result.path != source


def test_the_reopen_check_catches_an_altered_original_cell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # [NEG]
    original_put = writer._put_text

    def tampering_put(sheet: object, row: int, column: int, text: str) -> str:
        stored = original_put(sheet, row, column, text)  # type: ignore[arg-type]
        sheet["B2"].value = "tampered"  # type: ignore[index]
        return stored

    monkeypatch.setattr(writer, "_put_text", tampering_put)
    run = Run(tmp_path)
    with pytest.raises(WorkbookWriteError) as caught:
        run.write()
    assert caught.value.code == "verify_failed"
    assert run.files() == []


def test_a_readback_mismatch_removes_only_the_file_just_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # [NEG]
    run = Run(tmp_path)
    bystander = run.export_root / "someone-elses-file.xlsx"
    bystander.write_bytes(b"keep me")
    monkeypatch.setattr(writer, "_read_back", lambda _path: b"not what was written")
    with pytest.raises(WorkbookWriteError) as caught:
        run.write()
    assert caught.value.code == "readback_mismatch"
    assert run.files() == ["someone-elses-file.xlsx"]  # the new file is gone, the other is not
    assert bystander.read_bytes() == b"keep me"


def test_a_target_cell_that_is_not_empty_or_is_merged_is_refused(tmp_path: Path) -> None:  # [NEG]
    workbook = simple_workbook([("Q-1", "Is data encrypted?")])
    sheet = workbook.active
    assert sheet is not None
    sheet["D2"] = "already here"
    sheet.merge_cells("E3:F3")
    loaded = load_questionnaire(to_bytes(workbook))
    target = loaded.workbook["Questions"]
    with pytest.raises(WorkbookWriteError) as filled:
        writer._target_must_be_empty(target, 2, 4)
    with pytest.raises(WorkbookWriteError) as merged:
        writer._target_must_be_empty(target, 3, 6)
    assert (filled.value.code, merged.value.code) == ("target_not_empty", "target_not_empty")
    writer._target_must_be_empty(target, 2, 9)  # an empty cell is fine


def test_a_missing_export_folder_writes_nothing(tmp_path: Path) -> None:  # [NEG]
    run = Run(tmp_path)
    run.export_root = tmp_path / "no-such-folder"
    with pytest.raises(ConfigurationError):
        run.write()
    assert not (tmp_path / "no-such-folder").exists()


# ----- the output name -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "../../evil.xlsx",
        "..\\..\\evil.xlsx",
        "C:\\Users\\x\\Q 1.xlsx",
        "a/b/c.xlsx",
        'bad<>:"|?*name.xlsx',
        "name with\nnewline.xlsx",
        "",
        "...",
        "CON",
        "日本語.xlsx",
        "x" * 500 + ".xlsx",
    ],
)
def test_the_output_name_is_always_a_plain_file_name(source: str) -> None:  # [NEG]
    name = output_name(source, RUN_ID, macro_enabled=False)
    assert "/" not in name and "\\" not in name and ":" not in name
    assert name.endswith("-UNREVIEWED-DRAFT-01234567.xlsx")
    assert len(name) <= writer.MAX_STEM_CHARS + len("-UNREVIEWED-DRAFT-01234567.xlsx")
    assert Path(name).name == name


def test_a_bad_run_id_cannot_make_a_name() -> None:  # [NEG]
    for run_id in ("", "short", "../../x" + "0" * 8, "ZZZZZZZZ", "0123456789abcdef" * 8):
        with pytest.raises(WorkbookWriteError) as caught:
            output_name("q.xlsx", run_id, macro_enabled=False)
        assert caught.value.code == "bad_output_name"


def test_a_macro_package_gets_an_xlsm_name() -> None:
    assert output_name("q.xlsm", RUN_ID, macro_enabled=True).endswith(".xlsm")
