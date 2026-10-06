import ast
import importlib.util
import os
from importlib.metadata import distributions
from pathlib import Path

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
