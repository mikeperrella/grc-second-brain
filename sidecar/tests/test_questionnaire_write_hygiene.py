"""The questionnaire package writes one way only (ADR-014): the second line of defence.

The first is the set of Semgrep rules `grc-py-workbook-save-outside-writer`,
`grc-py-questionnaire-file-write-call`, `grc-py-questionnaire-open-mode` and
`grc-py-questionnaire-delete-outside-writer`, which CI runs. This is the same policy read from the
syntax tree, so it runs here too, and the scanner is itself tested: a check that finds nothing in
the real tree proves little unless it can find something.

Policy: `open()` only with mode "rb" or "xb"; no write_bytes, write_text, os.replace, os.rename,
shutil.copy* or shutil.move; `.save(` and file removal only in writer.py.
"""

import ast
from pathlib import Path

import pytest

from grc_sidecar.questionnaire import writer

PACKAGE = Path(writer.__file__).resolve().parent
WRITER = "writer.py"
ALLOWED_MODES = {"rb", "xb"}
BANNED_ATTRIBUTES = {"write_bytes", "write_text"}
BANNED_OS = {"replace", "rename"}
WRITER_ONLY = {"save", "remove", "unlink"}


def _mode(call: ast.Call) -> str | None:
    """The literal mode of an open call, "" if it has none, None if it is not a literal."""
    for keyword in call.keywords:
        if keyword.arg == "mode":
            value = keyword.value
            return value.value if isinstance(value, ast.Constant) else None
    if len(call.args) >= 2:
        value = call.args[1]
        return value.value if isinstance(value, ast.Constant) else None
    return ""


def violations(source: str, *, is_writer: bool) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "open":
            mode = _mode(node)
            if mode != "" and mode not in ALLOWED_MODES:
                found.append(f"line {node.lineno}: open with mode {mode!r}")
        elif isinstance(func, ast.Attribute):
            owner = func.value.id if isinstance(func.value, ast.Name) else ""
            if func.attr in BANNED_ATTRIBUTES:
                found.append(f"line {node.lineno}: {func.attr}")
            elif owner == "os" and func.attr in BANNED_OS:
                found.append(f"line {node.lineno}: os.{func.attr}")
            elif owner == "shutil" and (func.attr.startswith("copy") or func.attr == "move"):
                found.append(f"line {node.lineno}: shutil.{func.attr}")
            elif func.attr == "open" and node.args:
                if (
                    isinstance(node.args[0], ast.Constant)
                    and node.args[0].value not in ALLOWED_MODES
                ):
                    found.append(f"line {node.lineno}: .open with mode {node.args[0].value!r}")
            elif func.attr in WRITER_ONLY and not is_writer:
                found.append(f"line {node.lineno}: {func.attr} outside the writer")
    return found


def test_the_package_follows_the_write_policy() -> None:
    files = sorted(PACKAGE.glob("*.py"))
    assert WRITER in {f.name for f in files}
    offenders = {
        f.name: hits
        for f in files
        if (hits := violations(f.read_text(encoding="utf-8"), is_writer=f.name == WRITER))
    }
    assert offenders == {}


def test_the_writer_really_does_save_and_create_exclusively() -> None:
    """If the writer stopped doing these, the policy test above would pass for the wrong reason."""
    source = (PACKAGE / WRITER).read_text(encoding="utf-8")
    assert ".save(" in source
    assert 'open(path, "xb")' in source
    assert "os.remove(" in source


@pytest.mark.parametrize(
    "source",
    [
        'open(path, "wb")',
        'open(path, "w", encoding="utf-8")',
        'open(path, mode="a")',
        'open(path, "r+b")',
        'path.open("w")',
        "target.write_bytes(data)",
        "target.write_text(text)",
        "os.replace(a, b)",
        "os.rename(a, b)",
        "shutil.copyfile(a, b)",
        "shutil.copy2(a, b)",
        "shutil.move(a, b)",
        "workbook.save(buffer)",
        "os.remove(path)",
        "path.unlink()",
    ],
)
def test_the_scanner_sees_every_forbidden_construct(source: str) -> None:  # [NEG]
    assert violations(source, is_writer=False) != []


@pytest.mark.parametrize(
    "source",
    [
        'open(path, "rb")',
        'open(path, "xb")',
        'open(path, mode="xb")',
        "open(path)",
        "archive.open(info)",
        "data = target.read_bytes()",
        "os.path.exists(path)",
    ],
)
def test_the_scanner_leaves_reading_and_exclusive_create_alone(source: str) -> None:
    assert violations(source, is_writer=False) == []


def test_the_writer_alone_may_save_and_remove() -> None:
    assert violations("workbook.save(buffer)\nos.remove(path)", is_writer=True) == []
    assert violations('open(path, "wb")', is_writer=True) != []  # but never another open mode
