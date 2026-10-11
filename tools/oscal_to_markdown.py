"""Convert a NIST OSCAL catalog (JSON) into a framework pack: Markdown files plus pack.json.

Development-only tool (ADR-008). It is never packaged with the app. Standard library only, it
reads exactly one local file and has no network code (a test scans this directory for network
imports). Output is deterministic: sorted, LF line endings, no timestamps.

Safety properties:
- It refuses to convert unless the source file's sha256 equals the hash given with
  --expected-sha256, and writes nothing in that case.
- It counts the controls in the source (active and withdrawn, separately) and again in what it
  emitted, and fails if the two differ.
- Withdrawn controls are never rendered like requirements. They go in a final "Withdrawn controls"
  section of the file as one-line stubs, with the "incorporated into / moved to" note if any.

Usage (from the repo root):
    python -I tools/oscal_to_markdown.py --source <catalog.json> --expected-sha256 <hex> \\
        --profile csf|sp800-171|sp800-53 --pack-id <id> --title <title> --version <v> \\
        --source-url <https url> --retrieved-on YYYY-MM-DD --out packs/<id>
"""

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

CONVERTER_VERSION: Final = "1"

JsonObject = dict[str, Any]

LICENSE_BUNDLED: Final = "US-Gov-PD (17 USC 105); CC0-1.0 (usnistgov/oscal-content LICENSE.md)"
LICENSE_UPSTREAM: Final = (
    "US Government work (NIST), public domain in the United States; "
    "worldwide CC0 waiver stated in usnistgov/oscal-content LICENSE.md"
)
VERIFIED_AGAINST: Final = (
    "source file sha256 matched the pinned hash; control counts (active and withdrawn) "
    "compared with the OSCAL source at conversion time"
)

_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_PARAM = re.compile(r"\{\{\s*insert:\s*param,\s*([^\s}]+)\s*\}\}")
_LINK_LABELS: Final = {
    "incorporated-into": "Incorporated into",
    "incorporated_into": "Incorporated into",
    "moved-to": "Moved to",
    "moved_to": "Moved to",
    "addressed-by": "Addressed by",
    "addressed_by": "Addressed by",
}
_PART_TITLES: Final = {"guidance": "Guidance", "example": "Implementation example"}
_SKIPPED_PARTS: Final = {"assessment-objective", "assessment-method"}


class ConversionError(Exception):
    """A refusal or a failed self-check. The message never contains source text."""


def _prop(item: JsonObject, name: str) -> str | None:
    """The value of the first property with this name that has no class (the plain one)."""
    for prop in item.get("props", []):
        if prop.get("name") == name and "class" not in prop:
            return str(prop["value"])
    return None


def _id_csf(control: JsonObject) -> str:
    return str(control["id"])


def _id_sp800_171(control: JsonObject) -> str:
    return _prop(control, "sort-id") or str(control["id"])


def _id_sp800_53(control: JsonObject) -> str:
    return _prop(control, "label") or str(control["id"]).upper()


PROFILES: Final[dict[str, Callable[[JsonObject], str]]] = {
    "csf": _id_csf,
    "sp800-171": _id_sp800_171,
    "sp800-53": _id_sp800_53,
}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9.-]+", "-", text.lower()).strip("-")


def _one_line(text: str) -> str:
    """Collapse all whitespace, so source text can never start a heading or a fence."""
    return " ".join(text.split())


def _is_withdrawn(control: JsonObject) -> bool:
    return any(
        prop.get("name") == "status" and prop.get("value") == "withdrawn"
        for prop in control.get("props", [])
    )


def _walk_controls(controls: list[JsonObject]) -> list[JsonObject]:
    found: list[JsonObject] = []
    for control in controls:
        found.append(control)
        found.extend(_walk_controls(control.get("controls", [])))
    return found


def _count(groups: list[JsonObject]) -> tuple[int, int]:
    active = withdrawn = 0
    for group in groups:
        for control in _walk_controls(group.get("controls", [])):
            if _is_withdrawn(control):
                withdrawn += 1
            else:
                active += 1
    return active, withdrawn


def _param_text(param: JsonObject) -> str:
    select = param.get("select")
    if isinstance(select, dict) and select.get("choice"):
        return "[Selection: " + "; ".join(_one_line(str(c)) for c in select["choice"]) + "]"
    label = param.get("label") or param.get("usage") or param["id"]
    return f"[Assignment: {_one_line(str(label))}]"


class _Renderer:
    def __init__(self, groups: list[JsonObject], id_of: Callable[[JsonObject], str]) -> None:
        self._id_of = id_of
        self._params: dict[str, JsonObject] = {}
        self._display: dict[str, str] = {}
        for group in groups:
            for control in _walk_controls(group.get("controls", [])):
                self._display[str(control["id"])] = id_of(control)
                for param in control.get("params", []):
                    self._params[str(param["id"])] = param

    def prose(self, text: str) -> str:
        def replace(match: "re.Match[str]") -> str:
            param = self._params.get(match.group(1))
            return _param_text(param) if param is not None else f"[Assignment: {match.group(1)}]"

        # Markdown links keep their text and lose the URL (the sources carry app-internal paths).
        return _one_line(_LINK.sub(r"\1", _PARAM.sub(replace, text)))

    def part_lines(self, part: JsonObject, depth: int) -> list[str]:
        if part.get("name") in _SKIPPED_PARTS:
            return []
        text = self.prose(str(part["prose"])) if "prose" in part else ""
        label = _prop(part, "label")
        name = str(part.get("name"))
        lines: list[str] = []
        if name in ("item", "example"):
            prefix = "  " * depth + "- "
            marker = f"{label} " if label else ""
            title = f"{_PART_TITLES[name]}: " if name in _PART_TITLES else ""
            lines.append(f"{prefix}{marker}{title}{text}".rstrip())
        elif text:
            heading = _PART_TITLES.get(name)
            lines.extend(["", f"**{heading}.** {text}" if heading else text])
        for sub in part.get("parts", []):
            lines.extend(self.part_lines(sub, depth + 1 if name == "item" else depth))
        return lines

    def _target(self, href: str) -> str:
        # Control references become display IDs; part references (#ac-2_smt.k) stay as written.
        return self._display.get(href) or self._display.get(href.removeprefix("#")) or href

    def stub(self, control: JsonObject) -> str:
        identifier = self._id_of(control)
        notes: dict[str, list[str]] = {}
        for link in control.get("links", []):
            label = _LINK_LABELS.get(str(link.get("rel")))
            if label is not None:
                notes.setdefault(label, []).append(self._target(str(link["href"])))
        text = f"- **{identifier}**: Withdrawn."
        for label in sorted(notes):
            text += f" {label}: {', '.join(sorted(notes[label]))}."
        return text

    def control(self, control: JsonObject, level: int) -> list[str]:
        identifier = self._id_of(control)
        title = _one_line(str(control["title"]))
        heading = identifier if title == identifier else f"{identifier} {title}"
        lines = ["", f"{'#' * level} {heading}"]
        block: list[str] = []
        for part in control.get("parts", []):
            block.extend(self.part_lines(part, 0))
        lines.extend(block if not block or block[0] == "" else ["", *block])
        return lines


def _render_group(
    group: JsonObject, renderer: _Renderer, pack_id: str, group_id: str
) -> tuple[str, int, int]:
    withdrawn: list[JsonObject] = []
    active = 0

    def emit(controls: list[JsonObject], level: int) -> list[str]:
        nonlocal active
        lines: list[str] = []
        for control in controls:
            if _is_withdrawn(control):
                withdrawn.append(control)
            else:
                active += 1
                lines.extend(renderer.control(control, level))
            lines.extend(emit(control.get("controls", []), min(level + 1, 6)))
        return lines

    title = _one_line(str(group["title"]))
    out = [
        "---",
        f"doc_id: {pack_id}-{group_id}",
        "type: framework",
        'owner: "NIST"',
        "---",
        f"# {title} ({str(group['id']).removeprefix('SP_800_171_')})",
    ]
    for part in group.get("parts", []):
        out.extend(renderer.part_lines(part, 0))
    out.extend(emit(group.get("controls", []), 2))
    if withdrawn:
        out.extend(["", "## Withdrawn controls", ""])
        out.append("These controls are withdrawn. They are listed so old references still resolve.")
        out.append("")
        out.extend(renderer.stub(control) for control in withdrawn)
    return "\n".join(out) + "\n", active, len(withdrawn)


def content_sha256(files: dict[str, bytes]) -> str:
    """sha256 over the sorted (relative path, file sha256) pairs. Must equal the loader's."""
    pairs = [[path, hashlib.sha256(files[path]).hexdigest()] for path in sorted(files)]
    return hashlib.sha256(
        json.dumps(pairs, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def convert(
    source_bytes: bytes,
    *,
    expected_sha256: str,
    profile: str,
    pack_id: str,
    title: str,
    version: str,
    source_url: str,
    retrieved_on: str,
) -> tuple[dict[str, bytes], dict[str, int]]:
    """Return ({relative path: bytes} including pack.json, counts). Pure: writes nothing."""
    actual = hashlib.sha256(source_bytes).hexdigest()
    if actual != expected_sha256:
        raise ConversionError("source sha256 does not match the expected hash; nothing converted")
    id_of = PROFILES.get(profile)
    if id_of is None:
        raise ConversionError("unknown profile")
    catalog = json.loads(source_bytes.decode("utf-8"))["catalog"]
    groups: list[JsonObject] = catalog["groups"]
    if any("groups" in group for group in groups):
        raise ConversionError("nested groups are not supported")

    source_active, source_withdrawn = _count(groups)
    renderer = _Renderer(groups, id_of)
    files: dict[str, bytes] = {}
    emitted_active = emitted_withdrawn = 0
    for group in groups:
        group_id = _slug(str(group["id"]).removeprefix("SP_800_171_"))
        text, active, withdrawn = _render_group(group, renderer, pack_id, group_id)
        name = f"{group_id}.md"
        if name in files:
            raise ConversionError("two groups map to the same file name")
        files[name] = text.encode("utf-8")
        emitted_active += active
        emitted_withdrawn += withdrawn
    if (emitted_active, emitted_withdrawn) != (source_active, source_withdrawn):
        raise ConversionError("emitted control counts differ from the source counts")

    metadata = {
        "pack_id": pack_id,
        "title": title,
        "version": version,
        "license": LICENSE_BUNDLED,
        "upstream_license": LICENSE_UPSTREAM,
        "source_url": source_url,
        "retrieved_on": retrieved_on,
        "source_sha256": actual,
        "content_sha256": content_sha256(files),
        "origin": "converted_public_domain",
        "content_kind": "full_text",
        "redistributable": True,
        "converter_version": CONVERTER_VERSION,
        "id_list_verified_against": VERIFIED_AGAINST,
    }
    files["pack.json"] = (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8")
    counts = {
        "active": emitted_active,
        "withdrawn": emitted_withdrawn,
        "files": len(files) - 1,
        "bytes": sum(len(data) for data in files.values()),
    }
    return files, counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--profile", choices=sorted(PROFILES), required=True)
    parser.add_argument("--pack-id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--retrieved-on", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.out.exists() and any(args.out.iterdir()):
        print("output directory must be empty or absent", file=sys.stderr)
        return 2
    try:
        files, counts = convert(
            args.source.read_bytes(),
            expected_sha256=args.expected_sha256,
            profile=args.profile,
            pack_id=args.pack_id,
            title=args.title,
            version=args.version,
            source_url=args.source_url,
            retrieved_on=args.retrieved_on,
        )
    except ConversionError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (args.out / name).write_bytes(data)
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
