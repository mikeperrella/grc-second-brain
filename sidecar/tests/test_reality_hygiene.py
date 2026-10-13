"""Source-level guards for the reality check (ADR-016 decision 12): it can never become a live
connection, and `csv` is imported only under `reality/`. The Semgrep rule is the first layer; these
AST tests are the second (they also see `__import__` and `importlib`, which a regex cannot)."""

import ast
from pathlib import Path

from .vaultkit import REPO_ROOT

PACKAGE = REPO_ROOT / "sidecar" / "src" / "grc_sidecar"
REALITY = PACKAGE / "reality"
FORBIDDEN_IN_REALITY = {
    "socket",
    "ssl",
    "http",
    "urllib",
    "httpx",
    "requests",
    "aiohttp",
    "ftplib",
    "smtplib",
    "telnetlib",
    "subprocess",
    "multiprocessing",
    "os",
    "pty",
    "ctypes",
}
FORBIDDEN_INTERNAL = ("grc_sidecar.security.net", "grc_sidecar.llm")
CODE_ROOTS = [
    PACKAGE,
    REPO_ROOT / "sidecar" / "ci",
    REPO_ROOT / "tools",
    REPO_ROOT / ".github" / "scripts",
]


def imported_modules(tree: ast.AST) -> set[str]:
    """Every module named by an import statement, in full (`a.b.c`)."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
    return found


def dynamic_import_strings(tree: ast.AST) -> set[str]:
    """String arguments of `__import__(...)` and `importlib.import_module(...)`."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        first = node.args[0]
        is_import_call = name in {"__import__", "import_module"}
        if is_import_call and isinstance(first, ast.Constant) and isinstance(first.value, str):
            found.add(first.value)
    return found


def python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if ".venv" not in p.parts)


def test_nothing_under_reality_imports_a_network_or_process_module() -> None:  # [NEG]
    files = python_files(REALITY)
    assert {f.name for f in files} >= {
        "export_reader.py",
        "inspect.py",
        "runtime.py",
        "claims.py",
        "check.py",
    }
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules = imported_modules(tree) | dynamic_import_strings(tree)
        tops = {m.split(".")[0] for m in modules}
        assert tops.isdisjoint(FORBIDDEN_IN_REALITY), path.name
        assert not any(m.startswith(FORBIDDEN_INTERNAL) for m in modules), path.name


def test_no_request_model_has_a_url_token_credential_or_host_field() -> None:  # [NEG]
    tree = ast.parse((PACKAGE / "api" / "reality.py").read_text(encoding="utf-8"))
    names = {
        node.target.id.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    assert names == {"file"}  # the only request field; nothing that could name a live system


def test_csv_is_imported_only_under_reality() -> None:  # [NEG]
    importers: set[Path] = set()
    for root in CODE_ROOTS:
        for path in python_files(root):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            modules = imported_modules(tree) | dynamic_import_strings(tree)
            if any(m.split(".")[0] == "csv" for m in modules):
                importers.add(path)
    assert importers, "nothing imports csv, so this test would prove nothing"
    assert all(REALITY in path.parents for path in importers), sorted(importers)
