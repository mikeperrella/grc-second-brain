"""The loader reads a file once, scans the bytes, then parses those same bytes (ADR-014)."""

from pathlib import Path

import pytest

from grc_sidecar.errors import PathNotAllowedError, PathNotFoundError, WorkbookRefusedError
from grc_sidecar.questionnaire import loader
from grc_sidecar.questionnaire.loader import load_questionnaire, read_input_bytes
from grc_sidecar.questionnaire.package import Caps, sha256_hex

from .xlsxkit import (
    MACRO_TYPE,
    read_part,
    rebuild,
    simple_bytes,
    with_content_type,
)

SHEET = "xl/worksheets/sheet1.xml"
UNKNOWN_EXTENSION = (
    '<extLst><ext uri="{CCE6A557-97BC-4b89-ADB6-D9C93CAAB3DF}" '
    'xmlns:x14="http://schemas.microsoft.com/office/spreadsheetml/2009/9/main" '
    'xmlns:xm="http://schemas.microsoft.com/office/excel/2006/main">'
    '<x14:dataValidations count="1"><x14:dataValidation type="list" allowBlank="1">'
    '<x14:formula1><xm:f>"yes,no"</xm:f></x14:formula1><xm:sqref>C2:C9</xm:sqref>'
    "</x14:dataValidation></x14:dataValidations></ext></extLst>"
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    folder = tmp_path / "questionnaires"
    folder.mkdir()
    return folder


# ----- reading the file ----------------------------------------------------------------------


def test_a_file_inside_the_root_is_read_whole(root: Path) -> None:
    data = simple_bytes()
    (root / "sig.xlsx").write_bytes(data)
    assert read_input_bytes("sig.xlsx", root) == data


@pytest.mark.parametrize("candidate", ["../outside.xlsx", "..\\outside.xlsx"])
def test_a_path_that_leaves_the_root_is_refused(root: Path, candidate: str) -> None:  # [NEG]
    (root.parent / "outside.xlsx").write_bytes(simple_bytes())
    with pytest.raises(PathNotAllowedError):
        read_input_bytes(candidate, root)


def test_an_absolute_path_outside_the_root_is_refused(root: Path, tmp_path: Path) -> None:  # [NEG]
    other = tmp_path / "elsewhere.xlsx"
    other.write_bytes(simple_bytes())
    with pytest.raises(PathNotAllowedError):
        read_input_bytes(str(other), root)


def test_a_missing_file_is_a_typed_error(root: Path) -> None:
    with pytest.raises(PathNotFoundError):
        read_input_bytes("nothing.xlsx", root)


def test_a_directory_is_not_a_workbook(root: Path) -> None:  # [NEG]
    (root / "folder.xlsx").mkdir()
    with pytest.raises(WorkbookRefusedError) as caught:
        read_input_bytes("folder.xlsx", root)
    assert caught.value.code == "unreadable"


def test_an_oversize_file_is_refused_after_reading_at_most_one_byte_over(
    root: Path,
) -> None:  # [NEG]
    (root / "big.xlsx").write_bytes(b"x" * 10_000)
    with pytest.raises(WorkbookRefusedError) as caught:
        read_input_bytes("big.xlsx", root, Caps(max_file_bytes=100))
    assert caught.value.code == "file_too_large"


def test_an_oversize_file_is_never_read_past_one_byte_over_the_cap(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # [NEG]
    produced: list[int] = []
    real_open = open

    class Counting:
        def __init__(self, path: Path, mode: str) -> None:
            self._file = real_open(path, mode)

        def __enter__(self) -> "Counting":
            return self

        def __exit__(self, *exc: object) -> None:
            self._file.close()

        def read(self, size: int = -1) -> bytes:
            data: bytes = self._file.read(size)
            produced.append(len(data))
            return data

    monkeypatch.setattr(loader, "open", Counting, raising=False)
    (root / "huge.xlsx").write_bytes(b"x" * 50_000)
    with pytest.raises(WorkbookRefusedError):
        read_input_bytes("huge.xlsx", root, Caps(max_file_bytes=100))
    assert sum(produced) == 101  # the cap plus the one byte that proves it was exceeded


def test_a_file_exactly_at_the_cap_is_read(root: Path) -> None:
    (root / "edge.xlsx").write_bytes(b"x" * 100)
    assert len(read_input_bytes("edge.xlsx", root, Caps(max_file_bytes=100))) == 100


# ----- scanning, then parsing the same bytes -------------------------------------------------


def test_the_loaded_workbook_comes_with_the_hash_of_the_bytes_it_was_parsed_from() -> None:
    data = simple_bytes(4)
    loaded = load_questionnaire(data)
    assert loaded.scan.sha256 == sha256_hex(data)
    sheet = loaded.workbook["Questions"]
    assert sheet.max_row == 5
    assert sheet["A2"].value == "Q-1"


def test_the_bytes_that_were_scanned_are_the_bytes_that_are_parsed(root: Path) -> None:
    # The file changes on disk after it was read. The loader never goes back to the disk.
    (root / "q.xlsx").write_bytes(simple_bytes(2))
    data = read_input_bytes("q.xlsx", root)
    (root / "q.xlsx").write_bytes(simple_bytes(9))
    loaded = load_questionnaire(data)
    assert loaded.scan.sha256 == sha256_hex(data)
    assert loaded.workbook["Questions"].max_row == 3


def test_nothing_refused_by_the_scan_reaches_the_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # [NEG]
    def parser_must_not_run(*args: object, **kwargs: object) -> None:
        raise AssertionError("the parser was called for a refused package")

    monkeypatch.setattr(loader, "load_workbook", parser_must_not_run)
    hostile = rebuild(
        simple_bytes(), replace={SHEET: b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]><a/>'}
    )
    with pytest.raises(WorkbookRefusedError) as caught:
        load_questionnaire(hostile)
    assert caught.value.code == "doctype"


def test_a_macro_package_is_loaded_with_its_project_kept() -> None:
    project = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
    data = rebuild(simple_bytes(), add={"xl/vbaProject.bin": project})
    plain = load_questionnaire(data)
    macro = load_questionnaire(with_content_type(data, MACRO_TYPE))
    assert plain.workbook.vba_archive is None
    assert macro.workbook.vba_archive is not None
    assert macro.scan.macro_enabled is True


def test_the_parsers_own_warnings_are_collected_not_raised_or_printed() -> None:
    # The test run turns every warning into an error; the loader must catch this one itself.
    sheet = read_part(simple_bytes(), SHEET).decode("utf-8")
    with_dropdown = sheet.replace("</worksheet>", UNKNOWN_EXTENSION + "</worksheet>")
    loaded = load_questionnaire(rebuild(simple_bytes(), replace={SHEET: with_dropdown.encode()}))
    assert any("extension" in notice.casefold() for notice in loaded.warnings)
    assert loaded.scan.inventory.count("extension_data_validations") == 1


def test_a_clean_workbook_has_no_warnings() -> None:
    assert load_questionnaire(simple_bytes()).warnings == ()


def test_a_parser_failure_is_a_typed_refusal_with_nothing_chained() -> None:  # [NEG]
    broken = rebuild(simple_bytes(), replace={SHEET: b"<worksheet><sheetData><row></worksheet>"})
    with pytest.raises(WorkbookRefusedError) as caught:
        load_questionnaire(broken)
    assert caught.value.code == "unreadable"
    # The parser's message can quote the file, so it must not travel with the error.
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "sheetData" not in str(caught.value)


def test_a_workbook_with_a_corrupt_part_is_refused_not_crashed() -> None:  # [NEG]
    data = bytearray(simple_bytes())
    marker = data.find(b"PK\x03\x04")
    data[marker + 40 : marker + 60] = b"\xff" * 20  # damage the first entry's data
    with pytest.raises(WorkbookRefusedError):
        load_questionnaire(bytes(data))


# ----- the loader is the only caller of load_workbook ----------------------------------------

SRC = Path(loader.__file__).resolve().parents[1]
ALLOWED = Path("questionnaire") / "loader.py"


def files_naming_load_workbook(root: Path) -> set[Path]:
    return {
        path.relative_to(root)
        for path in root.rglob("*.py")
        if "load_workbook" in path.read_text(encoding="utf-8")
    }


def test_only_the_loader_names_load_workbook() -> None:
    assert files_naming_load_workbook(SRC) == {ALLOWED}


def test_the_scan_for_load_workbook_can_find_an_offender(tmp_path: Path) -> None:  # [NEG]
    (tmp_path / "a.py").write_text("from openpyxl import load_workbook\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("import openpyxl\nopenpyxl.load_workbook(p)\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("x = 1\n", encoding="utf-8")
    assert {p.name for p in files_naming_load_workbook(tmp_path)} == {"a.py", "b.py"}
