"""The OSCAL to Markdown converter (tools/oscal_to_markdown.py, ADR-008).

Everything here uses a small synthetic catalog built in the test, so no downloaded NIST file is
needed in CI. The real sources were converted locally and their counts are checked in
test_pack_loader.py against the committed packs.
"""

import ast
import hashlib
import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from grc_sidecar.packs.loader import content_sha256 as loader_content_sha256
from grc_sidecar.packs.models import parse_pack_metadata

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "tools"
NETWORK_MODULES = {
    "urllib",
    "http",
    "socket",
    "ssl",
    "requests",
    "httpx",
    "ftplib",
    "smtplib",
    "telnetlib",
    "aiohttp",
    "websockets",
    "xmlrpc",
}


def _load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "oscal_to_markdown", TOOLS / "oscal_to_markdown.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = _load_tool()


def _label(value: str) -> list[dict[str, str]]:
    return [{"name": "label", "value": value}]


def synthetic_catalog() -> dict[str, Any]:
    hostile = "Keep records.\n# Fake heading\n---\ndoc_id: injected"
    return {
        "catalog": {
            "groups": [
                {
                    "id": "xx",
                    "title": "Test Family",
                    "controls": [
                        {
                            "id": "xx-1",
                            "title": "Alpha",
                            "props": _label("XX-1"),
                            "params": [{"id": "xx-1_prm", "label": "frequency"}],
                            "parts": [
                                {
                                    "id": "xx-1_smt",
                                    "name": "statement",
                                    "prose": "Review {{ insert: param, xx-1_prm }} "
                                    "[docs](http://example.invalid/x) now.",
                                    "parts": [
                                        {
                                            "id": "xx-1_smt.a",
                                            "name": "item",
                                            "props": _label("a."),
                                            "prose": "First item;",
                                        },
                                        {
                                            "id": "xx-1_smt.b",
                                            "name": "item",
                                            "props": _label("b."),
                                            "prose": "Second item.",
                                        },
                                    ],
                                },
                                {"id": "xx-1_gdn", "name": "guidance", "prose": hostile},
                                {"id": "xx-1_obj", "name": "assessment-objective", "prose": "X"},
                            ],
                            "controls": [
                                {
                                    "id": "xx-1.1",
                                    "title": "Alpha Extra",
                                    "props": _label("XX-1(1)"),
                                    "parts": [{"name": "statement", "prose": "Extra text."}],
                                },
                                {
                                    "id": "xx-1.2",
                                    "title": "Old Extra",
                                    "props": [
                                        *_label("XX-1(2)"),
                                        {"name": "status", "value": "withdrawn"},
                                    ],
                                    "links": [{"href": "xx-1.1", "rel": "incorporated-into"}],
                                    "parts": [{"name": "statement", "prose": "SECRET OLD TEXT"}],
                                },
                            ],
                        },
                        {
                            "id": "xx-2",
                            "title": "Gone",
                            "props": [*_label("XX-2"), {"name": "status", "value": "withdrawn"}],
                        },
                        {
                            "id": "xx-3",
                            "title": "Gamma",
                            "props": _label("XX-3"),
                            "parts": [{"name": "statement", "prose": "Gamma text."}],
                        },
                    ],
                }
            ]
        }
    }


def source_bytes() -> bytes:
    return json.dumps(synthetic_catalog()).encode("utf-8")


def run_convert(data: bytes | None = None, *, expected: str | None = None) -> Any:
    data = source_bytes() if data is None else data
    return tool.convert(
        data,
        expected_sha256=expected or hashlib.sha256(data).hexdigest(),
        profile="sp800-53",
        pack_id="synthetic-pack",
        title="Synthetic Pack",
        version="1",
        source_url="https://example.invalid/catalog.json",
        retrieved_on="2026-10-09",
    )


def test_counts_are_split_into_active_and_withdrawn() -> None:
    _, counts = run_convert()
    assert (counts["active"], counts["withdrawn"]) == (3, 2)


def test_output_is_byte_identical_across_runs() -> None:
    first, _ = run_convert()
    second, _ = run_convert()
    assert first == second


def test_wrong_source_hash_is_refused_and_converts_nothing() -> None:
    with pytest.raises(tool.ConversionError):
        run_convert(expected=hashlib.sha256(b"something else").hexdigest())


def test_cli_refuses_a_wrong_hash_and_writes_nothing(tmp_path: Path) -> None:
    source = tmp_path / "catalog.json"
    source.write_bytes(source_bytes())
    out = tmp_path / "out"
    code = tool.main(
        [
            "--source", str(source),
            "--expected-sha256", hashlib.sha256(b"x").hexdigest(),
            "--profile", "sp800-53",
            "--pack-id", "synthetic-pack",
            "--title", "T",
            "--version", "1",
            "--source-url", "https://example.invalid/c.json",
            "--retrieved-on", "2026-10-09",
            "--out", str(out),
        ]
    )  # fmt: skip
    assert code == 1 and not out.exists()


def test_withdrawn_controls_are_stubs_in_a_final_section() -> None:
    files, _ = run_convert()
    text = files["xx.md"].decode("utf-8")
    head, _, tail = text.partition("## Withdrawn controls")
    assert "SECRET OLD TEXT" not in text  # a withdrawn control carries no requirement text
    assert "XX-1(2)" not in head and "XX-2" not in head
    assert "- **XX-1(2)**: Withdrawn. Incorporated into: XX-1(1)." in tail
    assert "- **XX-2**: Withdrawn." in tail


def test_output_control_ids_are_unique_and_match_active_count() -> None:
    files, counts = run_convert()
    text = files["xx.md"].decode("utf-8")
    withdrawn_start = text.index("## Withdrawn controls")
    headings = re.findall(r"^#{2,6} (\S+)", text[:withdrawn_start], flags=re.MULTILINE)
    assert len(headings) == len(set(headings)) == counts["active"]


def test_source_text_cannot_inject_headings_or_frontmatter() -> None:
    files, _ = run_convert()
    lines = files["xx.md"].decode("utf-8").split("\n")
    assert lines[0] == "---" and lines.count("---") == 2  # only our own frontmatter fences
    assert not any(
        line.startswith("# Fake") or line.startswith("doc_id: injected") for line in lines
    )
    assert sum(line.startswith("# ") for line in lines) == 1  # the one title line


def test_parameters_become_assignments_and_link_urls_are_dropped() -> None:
    files, _ = run_convert()
    text = files["xx.md"].decode("utf-8")
    assert "[Assignment: frequency]" in text
    assert "docs now." in text and "example.invalid" not in text
    assert "assessment" not in text.lower() and "- a. First item;" in text


def test_pack_json_validates_and_hash_matches_loader_formula() -> None:
    files, _ = run_convert()
    metadata = parse_pack_metadata(files["pack.json"].decode("utf-8"))
    markdown = {name: data for name, data in files.items() if name.endswith(".md")}
    assert metadata.content_sha256 == tool.content_sha256(markdown)
    assert tool.content_sha256(markdown) == loader_content_sha256(markdown)
    assert metadata.source_sha256 == hashlib.sha256(source_bytes()).hexdigest()


def test_a_source_that_loses_a_control_fails_the_count_check() -> None:
    """The self-check compares source counts with emitted counts; here the renderer is patched."""
    original = tool._render_group

    def lossy(*args: Any) -> tuple[str, int, int]:
        text, active, withdrawn = original(*args)
        return text, active - 1, withdrawn

    tool._render_group = lossy
    try:
        with pytest.raises(tool.ConversionError):
            run_convert()
    finally:
        tool._render_group = original


def test_tools_directory_has_no_network_imports() -> None:
    offenders: list[str] = []
    for path in sorted(TOOLS.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            offenders += [
                f"{path.name}: {name}" for name in names if name.split(".")[0] in NETWORK_MODULES
            ]
    assert offenders == []
