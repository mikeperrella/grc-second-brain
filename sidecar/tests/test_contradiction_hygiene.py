"""Source-level guards for the contradiction detector (ADR-019, F33-6, CLAUDE.md 3.1 and 3.2).

In chunk 4a-2 there is no model, so the package `grc_sidecar/contradictions/` may not import a
network or process module, `grc_sidecar.security.net` (the HTTP client factory), or
`grc_sidecar.llm` (the generation client). Chunk 4b adds the model half and must allow the `llm`
package on purpose, with its own Semgrep rule and fixtures; this test is then updated in the same
commit (ADR-019, "Chunk 4a-2: details"). Modelled on `test_ledger_hygiene.py`; the AST also sees
`__import__` and `importlib`, which a regex cannot.
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
FORBIDDEN_WHILE_THERE_IS_NO_MODEL = ("grc_sidecar.security.net", "grc_sidecar.llm")
REQUIRED_FILES = {
    "claims.py",
    "models.py",
    "numbers.py",
    "pairs.py",
    "service.py",
    "status.py",
    "verify.py",
}


def test_nothing_under_contradictions_imports_a_network_or_process_module() -> None:  # [NEG]
    files = python_files(CONTRADICTIONS)
    assert {f.name for f in files} >= REQUIRED_FILES
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules = imported_modules(tree) | dynamic_import_strings(tree)
        tops = {m.split(".")[0] for m in modules}
        assert tops.isdisjoint(FORBIDDEN_NETWORK_AND_PROCESS), path.name


def imported_names(tree: ast.AST) -> set[str]:
    """`from a.b import c` counts as `a.b.c` too, so `from pkg import module` is seen."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def test_nothing_under_contradictions_imports_the_client_factory_or_the_llm_package() -> None:
    # [NEG] 4a-2 has no model: no HTTP client factory and no generation client.
    for path in python_files(CONTRADICTIONS):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules = imported_modules(tree) | imported_names(tree) | dynamic_import_strings(tree)
        assert not any(m.startswith(FORBIDDEN_WHILE_THERE_IS_NO_MODEL) for m in modules), path.name
