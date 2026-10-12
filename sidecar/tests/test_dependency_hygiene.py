import ast
import importlib.util
import os
import re
import urllib.parse
from importlib.metadata import distributions
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
NET_MODULE = SRC / "grc_sidecar" / "security" / "net.py"
# Built by concatenation so this file never contains the forbidden literal itself.
FORBIDDEN_PULL = "/api/" + "pull"
FORBIDDEN_BIND_HOSTS = {"0.0.0.0", "::"}


def _httpx_violations(source: str) -> list[str]:
    """Find httpx imports or client construction in a module's source."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [f"import {a.name}" for a in node.names if a.name.split(".")[0] == "httpx"]
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "httpx":
            found.append(f"from {node.module} import ...")
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "httpx"
            and node.attr in {"Client", "AsyncClient"}
        ):
            found.append(f"httpx.{node.attr}")
    return found


def _source_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_only_opentelemetry_api_is_installed() -> None:  # [NEG]
    names = {
        (d.metadata["Name"] or "").lower().replace("_", "-")
        for d in distributions()
        if (d.metadata["Name"] or "").lower().startswith("opentelemetry")
    }
    assert names == {"opentelemetry-api"}
    assert importlib.util.find_spec("opentelemetry.sdk") is None


def test_telemetry_env_vars_are_set_for_the_test_process() -> None:
    assert os.environ["OTEL_SDK_DISABLED"] == "true"
    assert os.environ["OTEL_TRACES_EXPORTER"] == "none"


def test_scanner_detects_httpx_use() -> None:
    # Proves the scan below is not vacuous.
    assert _httpx_violations("import httpx\nc = httpx.AsyncClient()") == [
        "import httpx",
        "httpx.AsyncClient",
    ]
    assert _httpx_violations("from httpx import Client") == ["from httpx import ..."]
    assert _httpx_violations("import json") == []


def test_httpx_only_used_in_the_client_factory() -> None:  # [NEG]
    assert _httpx_violations(NET_MODULE.read_text(encoding="utf-8"))  # the factory does use it
    offenders = {
        str(path.relative_to(SRC)): _httpx_violations(path.read_text(encoding="utf-8"))
        for path in _source_files()
        if path != NET_MODULE
    }
    assert {k: v for k, v in offenders.items() if v} == {}


def test_no_wildcard_bind_host_in_source() -> None:  # [NEG]
    offenders: list[str] = []
    for path in _source_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and node.value in FORBIDDEN_BIND_HOSTS:
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert offenders == []


def test_no_model_pull_endpoint_in_source() -> None:  # [NEG]
    offenders = [
        str(p.relative_to(SRC))
        for p in _source_files()
        if FORBIDDEN_PULL in p.read_text(encoding="utf-8")
    ]
    assert offenders == []


# ----- model-management endpoints, however the path is built -------------------------------
#
# The literal search above (and the Semgrep regex) only sees a path written out in one piece. This
# folds string pieces joined with `+`, f-strings, `.format`/`%` templates and `join`-style calls
# back into one path, normalizes it, and refuses a `pull`, `push`, `create`, `copy` or `delete`
# segment after `api`, or a computed segment there. It is a second layer: the real control is the
# runtime endpoint allowlist in llm/ollama_embeddings.py, which no spelling can get past.

BLOCKED_API_SEGMENTS = {"pull", "push", "create", "copy", "delete"}
PLACEHOLDER = "\x00"
JOIN_CALLS = {"join", "urljoin", "joinpath"}


def _fold(node: ast.AST) -> str:
    """The string a node builds, with a placeholder wherever a piece is not a literal."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add | ast.Mod):
        return _fold(node.left) + _fold(node.right)
    if isinstance(node, ast.JoinedStr):
        return "".join(_fold(part) for part in node.values)
    if isinstance(node, ast.FormattedValue):
        return PLACEHOLDER
    return PLACEHOLDER


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _path_violation(text: str) -> bool:
    decoded = urllib.parse.unquote(text).casefold().replace("\\", "/")
    segments = [s for s in decoded.split("/") if s not in ("", ".")]
    for index, segment in enumerate(segments[:-1]):
        if segment == "api":
            following = segments[index + 1]
            if (
                following in BLOCKED_API_SEGMENTS
                or PLACEHOLDER in following
                or "{" in following
                or "%s" in following
            ):
                return True
    return False


def _endpoint_violations(source: str) -> list[int]:
    """Line numbers of strings, templates and calls that build a model-management path."""
    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        builds_path = isinstance(
            node, ast.Constant | ast.BinOp | ast.JoinedStr
        ) and _path_violation(_fold(node))
        if builds_path or (isinstance(node, ast.Call) and _call_is_model_management(node)):
            lines.append(node.lineno)
    return sorted(set(lines))


def _call_is_model_management(node: ast.Call) -> bool:
    name = _call_name(node)
    if name == "pull":
        return True
    if name in JOIN_CALLS:
        return _path_violation("/".join(_fold(arg) for arg in node.args))
    if name == "format" and isinstance(node.func, ast.Attribute):
        return _path_violation(_fold(node.func.value))
    return False


@pytest.mark.parametrize(
    "source",
    [
        'url = "/api/pull"',
        'url = "/api/" + "pull"',
        'url = "/api" "/pull"',
        'url = "api/pull"',
        'url = "/API/Pull"',
        'url = "/api//pull"',
        'url = "/api/./pull"',
        'url = "/api/%70ull"',
        'url = "\\\\api\\\\pull"',
        'url = "/api/push"',
        'url = f"/api/{verb}"',
        'url = "/api/{}".format(verb)',
        'url = "/api/%s" % verb',
        'url = "/api/" + verb',
        'url = urljoin(base, "api", "pull")',
        'url = os.path.join("api", "pull")',
        'url = base.joinpath("api", "create")',
        "client.pull(model)",
    ],
)
def test_the_endpoint_scanner_sees_every_spelling_of_a_model_management_path(  # [NEG]
    source: str,
) -> None:
    assert _endpoint_violations(source) != []


@pytest.mark.parametrize(
    "source",
    [
        'url = "/api/tags"',
        'url = "/api/embed"',
        'url = "/api" + "/embed"',
        'url = urljoin(base, "api", "embed")',
        'note = "pull requests are reviewed"',
        'path = "/apiary/pull"',
        "queue.pop()",
    ],
)
def test_the_endpoint_scanner_leaves_ordinary_code_alone(source: str) -> None:
    assert _endpoint_violations(source) == []


def test_no_model_management_endpoint_is_built_anywhere_in_source() -> None:  # [NEG]
    offenders = {
        str(path.relative_to(SRC)): _endpoint_violations(path.read_text(encoding="utf-8"))
        for path in _source_files()
    }
    assert {name: lines for name, lines in offenders.items() if lines} == {}


# ----- the spreadsheet packages (M5i, ADR-014) ---------------------------------------------
#
# openpyxl and et-xmlfile are pure Python and are believed to make no network calls and send no
# telemetry. That is a belief, so this reads the installed source and checks it: none imports a
# network, process or serialization module or calls an eval/exec/system-style function, and
# their text names no telemetry. It cannot prove a negative about code it did not read (a
# dependency added later is covered by the closure test below), but it fails loudly if a version
# bump brings any of this in.

SPREADSHEET_PACKAGES = ("openpyxl", "et_xmlfile")
FORBIDDEN_IMPORT_ROOTS = {
    "socket", "ssl", "http", "urllib", "ftplib", "smtplib", "telnetlib", "imaplib", "poplib",
    "nntplib", "xmlrpc", "requests", "httpx", "aiohttp", "urllib3", "webbrowser", "subprocess",
    "multiprocessing", "ctypes", "asyncio", "socketserver", "selectors", "pickle", "marshal",
    "shelve",
}  # fmt: skip
FORBIDDEN_CALLS = {"eval", "exec", "__import__", "system", "popen", "startfile", "urlopen"}
TELEMETRY_WORDS = re.compile(r"(?i)telemetry|analytics|sentry|posthog|phone.?home")


def _package_root(package: str) -> Path:
    spec = importlib.util.find_spec(package)
    assert spec is not None and spec.origin is not None
    return Path(spec.origin).parent


def _risky_constructs(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [
                f"import {a.name}"
                for a in node.names
                if a.name.split(".")[0] in FORBIDDEN_IMPORT_ROOTS
            ]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            if (node.module or "").split(".")[0] in FORBIDDEN_IMPORT_ROOTS:
                found.append(f"from {node.module} import ...")
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name in FORBIDDEN_CALLS:
                found.append(f"call {name}")
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and TELEMETRY_WORDS.search(node.value)
        ):
            found.append("telemetry text")
    return found


@pytest.mark.parametrize(
    "source",
    [
        "import socket",
        "from urllib.request import urlopen",
        "import subprocess as sp",
        "eval(text)",
        "os.system(cmd)",
        "mod = __import__(name)",
        'url = "https://telemetry.example.invalid"',
    ],
)
def test_the_package_scanner_sees_network_process_and_telemetry_constructs(  # [NEG]
    source: str,
) -> None:
    assert _risky_constructs(source) != []


def test_the_package_scanner_leaves_ordinary_code_alone() -> None:
    assert _risky_constructs("import re\nre.compile('x')\nvalue = 1 + 2") == []


@pytest.mark.parametrize("package", SPREADSHEET_PACKAGES)
def test_the_spreadsheet_packages_have_no_network_process_or_telemetry_code(  # [NEG]
    package: str,
) -> None:
    root = _package_root(package)
    files = sorted(root.rglob("*.py"))
    assert files  # the scan read something
    offenders = {
        str(path.relative_to(root.parent)): hits
        for path in files
        if (hits := _risky_constructs(path.read_text(encoding="utf-8")))
    }
    assert offenders == {}


def test_the_spreadsheet_dependency_closure_is_exactly_two_pure_python_packages() -> None:  # [NEG]
    from importlib.metadata import requires, version

    assert (version("openpyxl"), version("et-xmlfile")) == ("3.1.5", "2.0.0")
    assert [r.split(";")[0].strip() for r in requires("openpyxl") or []] == ["et-xmlfile"]
    assert requires("et-xmlfile") is None
    # Optional accelerators that would change the parser or add code are not installed.
    for module in ("lxml", "defusedxml", "PIL", "numpy", "pandas"):
        assert importlib.util.find_spec(module) is None, module
    import openpyxl.xml

    assert openpyxl.xml.LXML is False  # openpyxl uses the standard library's XML parser


def test_the_spreadsheet_pins_are_exact_in_pyproject_and_constraints() -> None:
    root = Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    constraints = (root / "constraints.txt").read_text(encoding="utf-8")
    for pin in ("openpyxl==3.1.5", "et-xmlfile==2.0.0", "types-openpyxl==3.1.5.20260827"):
        assert f'"{pin}"' in pyproject
    for pin in ("openpyxl==3.1.5", "et_xmlfile==2.0.0", "types-openpyxl==3.1.5.20260827"):
        assert pin in constraints.splitlines()
    runtime = pyproject.split("[project.optional-dependencies]")[0]
    assert "types-openpyxl" not in runtime  # the stubs are a development dependency only
