"""Fail if a custom Semgrep rule has no test annotation, or a test names a rule that is gone.

`semgrep --test` only checks the rules that have `ruleid:` or `ok:` annotations in a
fixture file. A new rule with no fixture would pass it without ever being tested. This
check closes that gap: every `- id:` in the rule files under RULES_DIR must appear in at
least one `ruleid:` annotation in FIXTURES_DIR, and every annotated id must exist in a
rule file (which catches typos and rules that were renamed or removed).

Standard library only. Reads local files only.

Usage: python check_semgrep_rule_coverage.py RULES_DIR FIXTURES_DIR
Exit codes: 0 covered, 1 a rule is untested or an annotation names an unknown rule,
2 a directory is missing or holds no rules or no annotations.
"""

import argparse
import re
import sys
from pathlib import Path

RULE_ID = re.compile(r"^\s*-\s*id:\s*(\S+)\s*$", re.MULTILINE)
ANNOTATION = re.compile(r"(?:#|//)\s*ruleid:\s*(.+)$", re.MULTILINE)
FIXTURE_SUFFIXES = {".py", ".rs", ".ts", ".tsx"}


def rule_ids(rules_dir: Path) -> set[str]:
    ids: set[str] = set()
    for path in sorted(rules_dir.glob("*.yml")):
        ids.update(RULE_ID.findall(path.read_text(encoding="utf-8")))
    return ids


def annotated_ids(fixtures_dir: Path) -> set[str]:
    ids: set[str] = set()
    for path in sorted(fixtures_dir.iterdir()):
        if path.suffix not in FIXTURE_SUFFIXES:
            continue
        for match in ANNOTATION.findall(path.read_text(encoding="utf-8")):
            ids.update(part.strip() for part in match.split(",") if part.strip())
    return ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rules_dir", type=Path, help="directory holding the rule .yml files")
    parser.add_argument("fixtures_dir", type=Path, help="directory holding the fixture files")
    args = parser.parse_args(argv)

    for directory in (args.rules_dir, args.fixtures_dir):
        if not directory.is_dir():
            print(f"not a directory: {directory}", file=sys.stderr)
            return 2
    rules = rule_ids(args.rules_dir)
    annotated = annotated_ids(args.fixtures_dir)
    if not rules or not annotated:
        print(
            f"found {len(rules)} rules and {len(annotated)} annotated ids; expected both > 0",
            file=sys.stderr,
        )
        return 2

    untested = sorted(rules - annotated)
    unknown = sorted(annotated - rules)
    print(f"{len(rules)} rules, {len(rules) - len(untested)} with a ruleid: annotation")
    if untested:
        print("Rules with no ruleid: annotation in the fixtures:", file=sys.stderr)
        for rule in untested:
            print(f"  {rule}", file=sys.stderr)
    if unknown:
        print("Annotations that name a rule that does not exist:", file=sys.stderr)
        for rule in unknown:
            print(f"  {rule}", file=sys.stderr)
    return 1 if untested or unknown else 0


if __name__ == "__main__":
    raise SystemExit(main())
