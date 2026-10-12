"""The demo spreadsheet generator (M5l): original synthetic questions, a workbook the engine reads,
and a refusal to overwrite."""

import ast
from pathlib import Path

import pytest

from grc_sidecar.questionnaire.loader import load_questionnaire
from grc_sidecar.questionnaire.mapping import extract_questions, suggest_mapping

from .e2ekit import load_demo_tool
from .vaultkit import REPO_ROOT

TOOL_PATH = REPO_ROOT / "tools" / "make_demo_xlsx.py"


def test_the_workbook_maps_to_15_questions_and_skips_the_three_section_banners(
    tmp_path: Path,
) -> None:
    tool = load_demo_tool()
    path = tool.write_demo(tmp_path)
    loaded = load_questionnaire(path.read_bytes())
    try:
        mapping = suggest_mapping(loaded.workbook)
        assert mapping is not None
        assert (mapping.sheet, mapping.header_row, mapping.question_col, mapping.id_col) == (
            "Questions",
            1,
            2,
            1,
        )
        found = extract_questions(loaded.workbook, mapping)
        assert [q.text for q in found.questions] == [q.question for q in tool.DEMO_QUESTIONS]
        assert [s.kind for s in found.skipped] == ["section_header"] * 3  # the banners
    finally:
        loaded.close()


def test_an_existing_demo_file_is_never_overwritten(tmp_path: Path) -> None:  # [NEG]
    tool = load_demo_tool()
    target = tmp_path / tool.FILE_NAME
    target.write_bytes(b"precious")
    with pytest.raises(FileExistsError):
        tool.write_demo(tmp_path)
    assert target.read_bytes() == b"precious"
    assert tool.main(["--out", str(tmp_path)]) == 1


def test_the_response_column_is_empty_and_has_a_dropdown() -> None:
    tool = load_demo_tool()
    sheet = tool.build_workbook()["Questions"]
    assert len(sheet.data_validations.dataValidation) == 1
    assert all(sheet.cell(row=r, column=3).value is None for r in range(2, sheet.max_row + 1))


def test_the_tool_has_no_network_or_process_imports() -> None:  # [NEG]
    tree = ast.parse(TOOL_PATH.read_text(encoding="utf-8"))
    imported = {
        (node.module or "").split(".")[0]
        if isinstance(node, ast.ImportFrom)
        else a.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for a in (node.names if isinstance(node, ast.Import) else [None])
    }
    assert imported.isdisjoint(
        {"socket", "http", "urllib", "httpx", "requests", "subprocess", "os", "ssl"}
    )


def test_the_gitignore_covers_the_output_folder() -> None:
    lines = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "fixtures-out/" in lines
