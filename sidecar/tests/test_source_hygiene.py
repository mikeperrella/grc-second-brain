"""No source or documentation file contains Unicode direction controls ("Trojan Source").

Bandit (B613) and Semgrep flag a file that contains one, because it can make code display in a
different order from the order it runs in. The questionnaire writer has to talk about these
characters, so its source builds them from code points instead of typing them. This test is the
same rule run over the whole repository, so a raw one cannot be added without a failing test
here, before CI's scanners see it, and so that it also covers the tests and the documents they
do not scan.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SUFFIXES = {".py", ".md", ".yml", ".yaml", ".rs", ".ts", ".tsx", ".json", ".toml"}
SKIPPED_DIRS = {".git", "node_modules", "target", ".venv", "__pycache__", "dist", "fixtures-out"}

# U+202A to U+202E, U+2066 to U+2069, U+200E, U+200F, U+061C. Written as numbers on purpose.
DIRECTION_CONTROLS = frozenset(
    {*range(0x202A, 0x202F), *range(0x2066, 0x206A), 0x200E, 0x200F, 0x061C}
)


def offenders(text: str) -> list[int]:
    """The line numbers (from 1) that hold a direction control."""
    return [
        number
        for number, line in enumerate(text.splitlines(), start=1)
        if any(ord(character) in DIRECTION_CONTROLS for character in line)
    ]


def tracked_text_files() -> list[Path]:
    found: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if path.suffix not in SUFFIXES or not path.is_file():
            continue
        if SKIPPED_DIRS & set(path.relative_to(REPO_ROOT).parts):
            continue
        found.append(path)
    return found


def test_no_file_in_the_repository_contains_a_direction_control() -> None:  # [NEG]
    files = tracked_text_files()
    assert len(files) > 100  # the walk found the repository, so an empty result means something
    found: dict[str, list[int]] = {}
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # not text we wrote
        if lines := offenders(text):
            found[str(path.relative_to(REPO_ROOT))] = lines
    assert found == {}


@pytest.mark.parametrize("point", sorted(DIRECTION_CONTROLS))
def test_the_scanner_finds_each_of_the_twelve_controls(point: int) -> None:  # [NEG]
    assert offenders(f"x = 1\ny = {chr(point)}2\n") == [2]


def test_the_scanner_leaves_ordinary_text_alone() -> None:
    assert offenders("plain, with accents: é, emoji, and Arabic: عربي") == []
    assert len(DIRECTION_CONTROLS) == 12
