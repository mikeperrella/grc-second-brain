"""Fail if the Cursor rule no longer mirrors CLAUDE.md Sections 3, 7 and 11.

CLAUDE.md is the canonical policy (Section 10). `.cursor/rules/grc-security.mdc`
mirrors three of its sections so Cursor sees the same MUST rules. This check compares
the sections line by line (headings included) and fails with a readable diff if they
differ, if a required section is missing from either file, if a numbered heading
appears twice, or if the rule file contains any numbered section other than 3, 7 and 11.

Differences that are not policy are ignored: line endings, trailing whitespace, and the
horizontal rule (`---`) that CLAUDE.md puts between sections.

Standard library only. Usage: python check_policy_drift.py [--claude PATH] [--rule PATH]
Exit codes: 0 in sync, 1 drift found, 2 a file could not be read.
"""

import argparse
import difflib
import re
import sys
from pathlib import Path

REQUIRED_SECTIONS = ("3", "7", "11")
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CLAUDE = REPO_ROOT / "CLAUDE.md"
DEFAULT_RULE = REPO_ROOT / ".cursor" / "rules" / "grc-security.mdc"

_SECTION_BOUNDARY = re.compile(r"(?m)^(?=## )")
_NUMBERED_HEADING = re.compile(r"## (\d+)\.")


def _normalize(chunk: str) -> list[str]:
    lines = [line.rstrip() for line in chunk.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and lines[-1] in ("", "---"):  # blank lines and the section separator
        lines.pop()
    return lines


def extract_sections(text: str) -> tuple[dict[str, list[str]], list[str]]:
    """Return ({section number: normalized lines}, [numbers that appear more than once])."""
    sections: dict[str, list[str]] = {}
    duplicates: list[str] = []
    for chunk in _SECTION_BOUNDARY.split(text):
        match = _NUMBERED_HEADING.match(chunk)
        if match is None:
            continue
        number = match.group(1)
        if number in sections:
            duplicates.append(number)
        else:
            sections[number] = _normalize(chunk)
    return sections, duplicates


def find_drift(
    canonical_text: str, mirror_text: str, required: tuple[str, ...] = REQUIRED_SECTIONS
) -> list[str]:
    """Return human-readable problems; an empty list means the files are in sync."""
    canonical, canonical_dups = extract_sections(canonical_text)
    mirror, mirror_dups = extract_sections(mirror_text)
    problems: list[str] = []

    problems += [f"CLAUDE.md has section {n} more than once" for n in canonical_dups]
    problems += [f"Cursor rule has section {n} more than once" for n in mirror_dups]

    for number in required:
        if number not in canonical:
            problems.append(f"CLAUDE.md is missing section {number}")
        elif number not in mirror:
            problems.append(f"Cursor rule is missing section {number}")
        elif canonical[number] != mirror[number]:
            diff = difflib.unified_diff(
                canonical[number],
                mirror[number],
                fromfile=f"CLAUDE.md section {number}",
                tofile=f"Cursor rule section {number}",
                lineterm="",
            )
            problems.append(f"Section {number} differs:\n" + "\n".join(diff))

    extra = sorted(set(mirror) - set(required), key=int)
    if extra:
        problems.append(
            "Cursor rule has sections it must not mirror: "
            + ", ".join(extra)
            + f" (allowed: {', '.join(required)})"
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claude", type=Path, default=DEFAULT_CLAUDE)
    parser.add_argument("--rule", type=Path, default=DEFAULT_RULE)
    args = parser.parse_args(argv)

    try:
        canonical_text = args.claude.read_text(encoding="utf-8")
        mirror_text = args.rule.read_text(encoding="utf-8")
    except OSError as error:
        print(f"cannot read policy file: {error.filename}", file=sys.stderr)
        return 2

    problems = find_drift(canonical_text, mirror_text)
    if problems:
        print("Policy drift: the Cursor rule no longer matches CLAUDE.md.", file=sys.stderr)
        for problem in problems:
            print(f"\n{problem}", file=sys.stderr)
        print(
            "\nFix: CLAUDE.md is canonical. Update the Cursor rule (and .coderabbit.yaml) to "
            "match it, in the same change.",
            file=sys.stderr,
        )
        return 1

    print(f"Cursor rule matches CLAUDE.md sections {', '.join(REQUIRED_SECTIONS)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
