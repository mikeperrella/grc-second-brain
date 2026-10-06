"""Fail if a pytest run skipped any test for a reason that is not explicitly allowed.

A skipped test is a test that did not run. CI must not let that pass silently, so each
platform states which skips it accepts:

* Linux accepts only the reason on the Windows-only path tests
  (`--allow "Windows-only path behavior"`), because junctions, UNC paths and Windows
  reserved names do not exist there.
* Windows accepts nothing, so every Windows-only and symlink test must really run.

pytest's `xfail` outcomes are recorded as skipped in the junit file and are therefore
rejected too. A missing, unreadable, malformed or empty report is an error (exit 2), so
a crashed pytest can never look like "no skips".

Standard library only. The report is pytest's own output on the CI runner, not
untrusted input.

Usage: python check_junit_skips.py JUNIT_XML [--allow REASON ...]
Exit codes: 0 all skips allowed, 1 a disallowed skip, 2 report missing or invalid.
"""

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def collect(report: Path) -> tuple[int, list[tuple[str, str]]]:
    """Return (number of tests, [(test id, skip reason), ...]) from a junit XML file."""
    # S314 targets untrusted XML; this is pytest's own report, written on this runner.
    root = ET.parse(report).getroot()  # noqa: S314
    total = 0
    skips: list[tuple[str, str]] = []
    for case in root.iter("testcase"):
        total += 1
        skipped = case.find("skipped")
        if skipped is not None:
            test_id = f"{case.get('classname', '')}::{case.get('name', '')}"
            skips.append((test_id, skipped.get("message", "")))
    return total, skips


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="junit XML written by pytest --junitxml")
    parser.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="REASON",
        help="a skip reason that is acceptable (exact match); repeat for several",
    )
    args = parser.parse_args(argv)

    try:
        total, skips = collect(args.report)
    except (OSError, ET.ParseError) as error:
        print(f"cannot read junit report {args.report}: {type(error).__name__}", file=sys.stderr)
        return 2
    if total == 0:
        print(f"junit report {args.report} contains no tests", file=sys.stderr)
        return 2

    disallowed = [(test, reason) for test, reason in skips if reason not in args.allow]
    allowed_count = len(skips) - len(disallowed)
    print(f"{total} tests, {len(skips)} skipped ({allowed_count} with an allowed reason)")
    if disallowed:
        print("Skipped tests that were not allowed:", file=sys.stderr)
        for test, reason in disallowed:
            print(f"  {test}  reason: {reason!r}", file=sys.stderr)
        allowed_text = ", ".join(repr(r) for r in args.allow) or "none"
        print(f"Allowed skip reasons: {allowed_text}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
