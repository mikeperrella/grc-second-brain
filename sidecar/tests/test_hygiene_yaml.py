"""Only the strict, safe YAML paths may be used in src/ (backs the Semgrep yaml rule)."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "grc_sidecar"
BANNED_FUNCTIONS = {
    "load",
    "load_all",
    "unsafe_load",
    "unsafe_load_all",
    "full_load",
    "full_load_all",
}
BANNED_CLASSES = {
    "Loader",
    "FullLoader",
    "UnsafeLoader",
    "CLoader",
    "CFullLoader",
    "CUnsafeLoader",
}
BANNED = BANNED_FUNCTIONS | BANNED_CLASSES


def yaml_violations(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "yaml"
            and node.attr in BANNED
        ):
            found.append(f"yaml.{node.attr}")
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "yaml":
            found += [f"from yaml import {a.name}" for a in node.names if a.name in BANNED]
    return found


def test_the_scanner_detects_unsafe_yaml_use() -> None:  # [NEG]
    assert yaml_violations("import yaml\nyaml.load(x)") == ["yaml.load"]
    assert yaml_violations("import yaml\nyaml.unsafe_load(x)") == ["yaml.unsafe_load"]
    assert yaml_violations("import yaml\nyaml.full_load(x)") == ["yaml.full_load"]
    assert yaml_violations("import yaml\nyaml.load_all(x, Loader=yaml.UnsafeLoader)") == [
        "yaml.load_all",
        "yaml.UnsafeLoader",
    ]
    assert yaml_violations("from yaml import Loader") == ["from yaml import Loader"]


def test_the_scanner_allows_the_safe_paths_we_use() -> None:
    safe = "import yaml\nyaml.SafeLoader\nyaml.parse(x, Loader=yaml.SafeLoader)\nyaml.YAMLError"
    assert yaml_violations(safe) == []


def test_src_uses_no_unsafe_yaml_loader() -> None:  # [NEG]
    offenders = {
        path.relative_to(SRC).as_posix(): found
        for path in sorted(SRC.rglob("*.py"))
        if (found := yaml_violations(path.read_text(encoding="utf-8")))
    }
    assert offenders == {}
