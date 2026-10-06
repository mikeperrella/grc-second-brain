"""Self-test for the CI secret scan.

Proves gitleaks can both detect a secret and stay quiet on clean input, without ever
committing anything secret-looking:

1. In a temp directory OUTSIDE the repository, generate a fake GitHub token at run
   time, run `gitleaks dir` on it, and require a non-zero exit with rule `github-pat`.
2. Run gitleaks on a clean directory and require exit 0 and no findings.

If either expectation fails the scanner is blind or broken, and this script exits 1,
so the CI job fails before the real history scan can give a false "all clear".
The fake token is never printed (gitleaks runs with --redact; output is not echoed).

Usage: python gitleaks_selftest.py [--gitleaks CMD [ARG ...]]
"""

import argparse
import json
import math
import os
import secrets
import shutil
import string
import subprocess  # nosec B404 -- the self-test must run gitleaks; arguments are a fixed list
import sys
import tempfile
from collections import Counter
from pathlib import Path

EXPECTED_RULE = "github-pat"
TOKEN_BODY_LENGTH = 36
MIN_ENTROPY = 4.0  # random 36-char alphanumerics almost always exceed this
SCAN_TIMEOUT_SECONDS = 120


def _entropy(text: str) -> float:
    counts = Counter(text)
    return -sum((n / len(text)) * math.log2(n / len(text)) for n in counts.values())


def make_fake_token() -> str:
    """A syntactically valid but fake GitHub PAT, generated fresh on every run."""
    alphabet = string.ascii_letters + string.digits
    while True:
        body = "".join(secrets.choice(alphabet) for _ in range(TOKEN_BODY_LENGTH))
        if _entropy(body) >= MIN_ENTROPY:
            return "ghp_" + body


def scan(command: list[str], directory: Path, report: Path) -> tuple[int, list[dict[str, object]]]:
    """Run `gitleaks dir` and return (exit code, parsed findings)."""
    result = subprocess.run(  # noqa: S603  # nosec B603 -- fixed argument list, shell=False, no user input
        [
            *command,
            "dir",
            str(directory),
            "--redact",
            "--no-banner",
            "--exit-code",
            "1",
            "--report-format",
            "json",
            "--report-path",
            str(report),
        ],
        capture_output=True,
        text=True,
        check=False,
        shell=False,
        timeout=SCAN_TIMEOUT_SECONDS,
    )
    text = report.read_text(encoding="utf-8").strip() if report.exists() else ""
    findings: list[dict[str, object]] = json.loads(text) if text else []
    return result.returncode, findings


def run_self_test(command: list[str], workdir: Path) -> bool:
    positive = workdir / "positive"
    clean = workdir / "clean"
    positive.mkdir()
    clean.mkdir()
    (positive / "settings.txt").write_text(
        f'github_token = "{make_fake_token()}"\n', encoding="utf-8"
    )
    (clean / "notes.txt").write_text("Nothing secret here.\n", encoding="utf-8")

    code, findings = scan(command, positive, workdir / "positive.json")
    rules = {str(f.get("RuleID")) for f in findings}
    detected = code != 0 and EXPECTED_RULE in rules
    verdict = "detected" if detected else "MISSED"
    print(f"fake secret: exit={code} rules={sorted(rules)} -> {verdict}")

    code, findings = scan(command, clean, workdir / "clean.json")
    quiet = code == 0 and not findings
    verdict = "quiet" if quiet else "FALSE ALARM"
    print(f"clean input: exit={code} findings={len(findings)} -> {verdict}")

    return detected and quiet


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gitleaks", nargs="+", default=["gitleaks"], metavar="CMD")
    args = parser.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="gitleaks-selftest-", dir=os.environ.get("RUNNER_TEMP")))
    try:
        if workdir.resolve().is_relative_to(Path.cwd().resolve()):
            print("refusing to run: the temp directory is inside the repository", file=sys.stderr)
            return 2
        passed = run_self_test(args.gitleaks, workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print("gitleaks self-test:", "PASSED" if passed else "FAILED")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
