"""Source-level guards for the promise ledger (backlog item G38, ADR-017): the `ledger/` package can
never become a network client or a process spawner, and no ledger request model can name a live
system. The Semgrep rules are the first layer; these AST tests are the second (they also see
`__import__` and `importlib`, which a regex cannot). Modelled on `test_reality_hygiene.py`.

Scope, stated plainly: the package `grc_sidecar/ledger/` and the request models in
`api/ledger.py`. The repository module `repositories/sqlite_ledger_repo.py` is not read here."""

import ast
from pathlib import Path

import grc_sidecar

# The package is found from the import, not from the repository layout: in the air-gap image the
# code lives at /app/src, so a path built from the test file's location points at nothing.
PACKAGE = Path(grc_sidecar.__file__).resolve().parent
LEDGER = PACKAGE / "ledger"
LEDGER_ROUTES = PACKAGE / "api" / "ledger.py"
FORBIDDEN_IN_LEDGER = {
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
# Parts of a field name that could point at a live system or carry a secret.
FORBIDDEN_FIELD_PARTS = (
    "url",
    "uri",
    "token",
    "credential",
    "password",
    "secret",
    "host",
    "endpoint",
    "address",
    "proxy",
)
# Every field of every ledger request model, pinned: a new field must be reviewed to be added here.
EXPECTED_REQUEST_FIELDS = {
    "ImportRequest": {
        "run_id",
        "file",
        "existing_customer_id",
        "new_customer_name",
        "confirm_new",
        "sent_on",
        "preview",
    },
    "RecallRequest": {"customer_id"},
    "VoidRequest": {"reason"},
    "AcknowledgeRequest": {"seen_recall_event_id"},
    "BlastRadiusRequest": {"path", "proposed_text", "delete_file", "customer_id"},
}


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


def test_nothing_under_ledger_imports_a_network_or_process_module() -> None:  # [NEG]
    files = python_files(LEDGER)
    assert {f.name for f in files} >= {"recall.py", "review.py", "reviewed.py", "service.py"}
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules = imported_modules(tree) | dynamic_import_strings(tree)
        tops = {m.split(".")[0] for m in modules}
        assert tops.isdisjoint(FORBIDDEN_IN_LEDGER), path.name
        assert not any(m.startswith(FORBIDDEN_INTERNAL) for m in modules), path.name


def request_models() -> dict[str, set[str]]:
    """The annotated field names of every class in `api/ledger.py` whose name ends in `Request`."""
    tree = ast.parse(LEDGER_ROUTES.read_text(encoding="utf-8"))
    found: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name.endswith("Request"):
            found[node.name] = {
                item.target.id
                for item in node.body
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
            }
    return found


def test_no_ledger_request_model_has_a_url_token_credential_or_host_field() -> None:  # [NEG]
    models = request_models()
    assert set(models) == set(EXPECTED_REQUEST_FIELDS), "a request model was added or removed"
    for name, fields in models.items():
        assert fields == EXPECTED_REQUEST_FIELDS[name], name
        for field in fields:
            assert not any(part in field.lower() for part in FORBIDDEN_FIELD_PARTS), (name, field)
