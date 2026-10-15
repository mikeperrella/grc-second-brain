"""Source-level guards for the contradiction detector (ADR-019, F33-6, CLAUDE.md 3.1 and 3.2).

The package `grc_sidecar/contradictions/` may not import a network or process module, and may not
import `grc_sidecar.security.net` (the HTTP client factory) or `httpx`, in any file. Since chunk 4b
the `grc_sidecar.llm` package is allowed in exactly ONE file, `verdict.py`, the model module (the
Semgrep rules `grc-py-contradictions-network-process-import` and
`grc-py-contradictions-llm-outside-model-module` say the same). Modelled on
`test_ledger_hygiene.py`; the AST also sees `__import__` and `importlib`, which a regex cannot.
"""

import ast
from pathlib import Path

import grc_sidecar

from .test_ledger_hygiene import (
    FORBIDDEN_IN_LEDGER as FORBIDDEN_NETWORK_AND_PROCESS,
)
from .test_ledger_hygiene import (
    dynamic_import_strings,
    imported_modules,
    python_files,
)

# Found from the import, not from the repository layout (the air-gap image keeps code at /app/src).
PACKAGE = Path(grc_sidecar.__file__).resolve().parent
CONTRADICTIONS = PACKAGE / "contradictions"
MODEL_MODULE = "verdict.py"  # the one file that may import the llm package
NEVER_IN_THE_PACKAGE = ("grc_sidecar.security.net", "httpx")
LLM_PACKAGE = "grc_sidecar.llm"
REQUIRED_FILES = {
    "claims.py",
    "models.py",
    "numbers.py",
    "pairs.py",
    "runs.py",
    "service.py",
    "status.py",
    "verdict.py",
    "verify.py",
}


def imported_names(tree: ast.AST) -> set[str]:
    """`from a.b import c` counts as `a.b.c` too, so `from pkg import module` is seen."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def all_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return imported_modules(tree) | imported_names(tree) | dynamic_import_strings(tree)


def test_nothing_under_contradictions_imports_a_network_or_process_module() -> None:  # [NEG]
    files = python_files(CONTRADICTIONS)
    assert {f.name for f in files} >= REQUIRED_FILES
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules = imported_modules(tree) | dynamic_import_strings(tree)
        tops = {m.split(".")[0] for m in modules}
        assert tops.isdisjoint(FORBIDDEN_NETWORK_AND_PROCESS), path.name


def test_nothing_under_contradictions_imports_the_client_factory_or_httpx() -> None:  # [NEG]
    # Not even the model module: the generation client is passed in already built.
    for path in python_files(CONTRADICTIONS):
        modules = all_imports(path)
        assert not any(
            m == n or m.startswith(n + ".") for m in modules for n in NEVER_IN_THE_PACKAGE
        ), path.name


def test_the_llm_package_is_imported_only_by_the_model_module() -> None:  # [NEG]
    files = python_files(CONTRADICTIONS)
    assert MODEL_MODULE in {f.name for f in files}
    for path in files:
        uses_llm = any(m.startswith(LLM_PACKAGE) for m in all_imports(path))
        if path.name == MODEL_MODULE:
            assert uses_llm, (
                "the model module must import the llm package (else this test is vacuous)"
            )
        else:
            assert not uses_llm, path.name
