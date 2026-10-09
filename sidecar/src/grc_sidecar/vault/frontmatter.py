"""Parse the YAML frontmatter block of a vault file (CLAUDE.md 8.1).

YAML is a surprisingly loose format, and frontmatter is untrusted input (CLAUDE.md 8.6),
so this module is deliberately strict:

- Only `yaml.SafeLoader` machinery is used, through our own subclass. `yaml.load` is never
  called (a Semgrep rule bans it).
- The subclass turns off YAML's "helpful" type guessing. `2026-01-15` stays the text
  "2026-01-15", `NO` stays "NO" (not False), and `007` stays "007" (not 7). Pydantic then
  checks each field against its real type, so a typo is an error, not a silent coercion.
- Anchors, aliases, explicit tags (`!!python/object`) and duplicate keys are rejected.
- The frontmatter block and the whole file have size caps.

Line numbers: the opening `---` is line 1 of the file, so YAML line N (1-based, inside the
block) is file line N + 1. Frontmatter lines count in every citation.
"""

import re
from collections.abc import Hashable
from typing import Any, Final

import yaml
from pydantic import ValidationError

from grc_sidecar.errors import (
    FrontmatterFieldError,
    FrontmatterSyntaxError,
    MissingFrontmatterError,
    VaultError,
)
from grc_sidecar.vault.models import Frontmatter, IndexIssue, ParsedDocument
from grc_sidecar.vault.normalize import normalize_text, sha256_hex, split_lines

FENCE: Final = "---"
MAX_FRONTMATTER_BYTES: Final = 64 * 1024
UNQUOTED_WIKILINK_CODE: Final = "FRONTMATTER_UNQUOTED_WIKILINK"
# Fields where a nested list means an unquoted `[[wikilink]]` (both hold lists of text).
WIKILINK_FIELDS: Final = ("links", "control_mappings")
_NULL_TAG: Final = "tag:yaml.org,2002:null"
_LINKS_KEY: Final = re.compile(r"^links\s*:")

_REQUIRED_FIELD_ORDER: Final = ("doc_id", "type", "owner", "review_by")


class _Rejected(Exception):
    """Raised inside the loader. `code` says which rule rejected the YAML."""

    def __init__(self, code: str, line: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.line = line


def _null_only_resolvers() -> dict[Any, list[tuple[str, re.Pattern[str]]]]:
    """A brand-new resolver table that keeps only YAML's null rule (`~`, `null`, empty).

    New dict and new lists on purpose: PyYAML keeps this table on the class, so editing the
    inherited one would silently change yaml.safe_load for every other caller in the process.
    """
    table: dict[Any, list[tuple[str, re.Pattern[str]]]] = {}
    for first_char, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items():
        kept = [(tag, pattern) for tag, pattern in resolvers if tag == _NULL_TAG]
        if kept:
            table[first_char] = kept
    return table


class StrictFrontmatterLoader(yaml.SafeLoader):
    """SafeLoader with no implicit typing, no anchors/aliases/tags, and no duplicate keys."""

    # Only null is guessed; every other plain scalar (dates, yes/no, 007, 1e3) is text.
    yaml_implicit_resolvers = _null_only_resolvers()

    def construct_mapping(self, node: Any, deep: bool = False) -> dict[Hashable, Any]:
        seen: set[Hashable] = set()
        for key_node, _value_node in node.value:
            key = self.construct_object(key_node, deep=True)
            if isinstance(key, Hashable):
                if key in seen:
                    raise _Rejected("yaml_duplicate_key", key_node.start_mark.line + 1)
                seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _event_rejection(event: yaml.Event) -> str | None:
    """Why a YAML parse event is refused: aliases, anchors and explicit tags are not allowed."""
    if isinstance(event, yaml.AliasEvent):
        return "yaml_alias"
    if isinstance(event, yaml.NodeEvent) and event.anchor is not None:
        return "yaml_anchor"
    if isinstance(event, yaml.ScalarEvent | yaml.CollectionStartEvent) and event.tag is not None:
        return "yaml_tag"
    return None


def split_frontmatter(lines: tuple[str, ...], source_path: str) -> tuple[str, int]:
    """Return (frontmatter_text, body_start_line). Line 1 must be exactly `---`."""
    if not lines or lines[0] != FENCE:
        raise MissingFrontmatterError(source_path=source_path, line=1)
    for index in range(1, len(lines)):
        if lines[index] == FENCE:
            block = "\n".join(lines[1:index])
            if len(block.encode("utf-8")) > MAX_FRONTMATTER_BYTES:
                raise FrontmatterSyntaxError(source_path=source_path, code="frontmatter_too_large")
            return block, index + 2  # closing fence is line index+1, so the body starts after it
    raise FrontmatterSyntaxError(source_path=source_path, line=1, code="frontmatter_unterminated")


def _load_mapping(block: str) -> tuple[dict[str, Any] | None, str | None, int | None]:
    """Run the strict loader. Returns (mapping, error_code, yaml_line); never raises YAML errors.

    Errors are returned instead of raised so the caller can raise the domain error outside any
    `except` block (keeps the YAML exception, which quotes the input, out of __context__).
    """
    code: str | None = None
    line: int | None = None
    data: Any = None
    try:
        # First pass over the parse events only (no objects built yet): refuse aliases, anchors
        # and tags before anything is constructed.
        for event in yaml.parse(block, Loader=StrictFrontmatterLoader):
            rejection = _event_rejection(event)
            if rejection is not None:
                raise _Rejected(rejection, event.start_mark.line + 1)
        loader = StrictFrontmatterLoader(block)
        try:
            data = loader.get_single_data()
        finally:
            loader.dispose()
    except _Rejected as rejected:
        code, line = rejected.code, rejected.line
    except yaml.YAMLError as exc:
        code = "yaml_syntax"
        mark = getattr(exc, "problem_mark", None)
        line = int(mark.line) + 1 if mark is not None else None
    if code is not None:
        return None, code, line
    if data is None:
        return {}, None, None
    if not isinstance(data, dict) or not all(isinstance(key, str) for key in data):
        return None, "frontmatter_not_a_mapping", None
    return data, None, None


def _first_field_error(error: ValidationError) -> tuple[str, str]:
    """Pick one (field, code) from a pydantic error, preferring required fields in a fixed order."""
    found: dict[str, str] = {}
    for item in error.errors():
        field = str(item["loc"][0]) if item["loc"] else "frontmatter"
        code = (
            "missing_field" if item["type"] in {"missing", "missing_required"} else "invalid_value"
        )
        found.setdefault(field, code)
    for field in _REQUIRED_FIELD_ORDER:
        if field in found:
            return field, found[field]
    field = sorted(found)[0]
    return field, found[field]


def parse_frontmatter(block: str, source_path: str) -> tuple[Frontmatter, tuple[str, ...]]:
    """Parse and validate one frontmatter block.

    Returns the validated model and the sorted names of keys we do not use (Obsidian users
    add `tags`, `aliases` and so on; they are ignored, and reported as notes by the caller).
    Raises typed errors naming file, field and line.
    """
    data, code, yaml_line = _load_mapping(block)
    if code is not None or data is None:
        raise FrontmatterSyntaxError(
            source_path=source_path,
            code=code,
            line=None if yaml_line is None else yaml_line + 1,
        )
    for field in WIKILINK_FIELDS:
        value = data.get(field)
        if isinstance(value, list) and any(isinstance(item, list) for item in value):
            # YAML reads an unquoted `links: [[x]]` as a list inside a list. Say what to do.
            raise FrontmatterFieldError(
                source_path=source_path, field=field, code=UNQUOTED_WIKILINK_CODE
            )
    problem: tuple[str, str] | None = None
    frontmatter: Frontmatter | None = None
    try:
        frontmatter = Frontmatter.model_validate(data)
    except ValidationError as exc:
        problem = _first_field_error(exc)
    if problem is not None or frontmatter is None:
        field, field_code = problem if problem is not None else ("frontmatter", "invalid_value")
        raise FrontmatterFieldError(source_path=source_path, field=field, code=field_code)
    return frontmatter, tuple(sorted(set(data) - set(Frontmatter.model_fields)))


def parse_document(text: str, source_path: str) -> ParsedDocument:
    """Normalize text, split off and validate frontmatter, and fingerprint the file.

    `source_path` must already be vault-relative with forward slashes.
    """
    normalized = normalize_text(text, source_path)
    lines = split_lines(normalized)
    block, body_start = split_frontmatter(lines, source_path)
    frontmatter, unknown_keys = parse_frontmatter(block, source_path)
    notes = tuple(
        IndexIssue(
            severity="info", code="unknown_frontmatter_key", source_path=source_path, field=key
        )
        for key in unknown_keys
    )
    links_line = next(
        (
            number
            for number, line in enumerate(lines[1 : body_start - 2], start=2)
            if _LINKS_KEY.match(line)
        ),
        1,
    )
    return ParsedDocument(
        source_path=source_path,
        frontmatter=frontmatter,
        file_sha256=sha256_hex(normalized),
        lines=lines,
        body_start_line=body_start,
        frontmatter_line_of_links=links_line,
        notes=notes,
    )


def error_to_issue(error: VaultError, fallback_path: str | None = None) -> IndexIssue:
    """Turn a typed vault error into a report entry. Carries no message text and no values."""
    return IndexIssue(
        severity="error",
        code=error.code,
        source_path=error.source_path or fallback_path,
        field=error.field,
        line=error.line,
    )
