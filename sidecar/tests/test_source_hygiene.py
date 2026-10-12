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

# Folders that must both exist for a folder to count as the repository root.
ROOT_MARKERS = ("CLAUDE.md", "sidecar/pyproject.toml")
# The skip reason when there is no repository. check_junit_skips.py matches it exactly, and the
# air-gap job lists it in its --allow arguments; the Windows run and python.yml allow nothing.
NO_ROOT_REASON = "Repository root not found (air-gap container holds only sidecar/)"


def find_repository_root(start: Path) -> Path | None:
    """The nearest folder above `start` that holds every marker, or None.

    Counting parent folders is wrong in the air-gap container, where the tests live in
    /app/tests and "two folders up" is the filesystem root. A filesystem root is never accepted.
    """
    for folder in (start, *start.parents):
        if folder == folder.parent:
            return None
        if all((folder / marker).is_file() for marker in ROOT_MARKERS):
            return folder
    return None


REPO_ROOT = find_repository_root(Path(__file__).resolve())
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


def tracked_text_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for path in root.rglob("*"):
        if path.suffix not in SUFFIXES or not path.is_file():
            continue
        if SKIPPED_DIRS & set(path.relative_to(root).parts):
            continue
        found.append(path)
    return found


def test_no_file_in_the_repository_contains_a_direction_control() -> None:  # [NEG]
    if REPO_ROOT is None:
        pytest.skip(NO_ROOT_REASON)  # never walk anything without a root
    files = tracked_text_files(REPO_ROOT)
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


def make_root(folder: Path, markers: tuple[str, ...]) -> None:
    for marker in markers:
        target = folder / marker
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", encoding="utf-8")


def test_the_root_is_found_by_its_markers_from_any_depth(tmp_path: Path) -> None:
    make_root(tmp_path / "repo", ROOT_MARKERS)
    deep = tmp_path / "repo" / "sidecar" / "tests" / "nested"
    deep.mkdir(parents=True)
    assert find_repository_root(deep) == tmp_path / "repo"


def test_the_real_repository_has_a_root_here_or_the_walk_is_skipped() -> None:
    # On a checkout the root is found, so the walk really runs; in the container it is None.
    assert REPO_ROOT is None or all((REPO_ROOT / m).is_file() for m in ROOT_MARKERS)


@pytest.mark.parametrize(
    "markers",
    [(), ("CLAUDE.md",), ("sidecar/pyproject.toml",)],
    ids=["no-marker", "only-CLAUDE.md", "only-sidecar-pyproject"],
)
def test_no_root_is_returned_when_a_marker_is_missing(
    tmp_path: Path, markers: tuple[str, ...]
) -> None:  # [NEG]
    make_root(tmp_path / "partial", markers)
    start = tmp_path / "partial" / "sidecar" / "tests"
    start.mkdir(parents=True, exist_ok=True)
    assert find_repository_root(start) is None


def test_a_filesystem_root_is_never_returned(tmp_path: Path) -> None:  # [NEG]
    anchor = Path(tmp_path.anchor)
    for start in (anchor, tmp_path):
        found = find_repository_root(start)
        assert found is None or found != found.parent  # never "/" or a drive root


@pytest.mark.parametrize("point", sorted(DIRECTION_CONTROLS))
def test_the_scanner_finds_each_of_the_twelve_controls(point: int) -> None:  # [NEG]
    assert offenders(f"x = 1\ny = {chr(point)}2\n") == [2]


def test_the_scanner_leaves_ordinary_text_alone() -> None:
    assert offenders("plain, with accents: é, emoji, and Arabic: عربي") == []
    assert len(DIRECTION_CONTROLS) == 12
