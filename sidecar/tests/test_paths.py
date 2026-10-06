import os
import sys
from pathlib import Path

import pytest

from grc_sidecar.errors import (
    ConfigurationError,
    GrcError,
    PathNotAllowedError,
    PathNotFoundError,
)
from grc_sidecar.security.paths import resolve_within

from .leakcheck import assert_no_leak

# Skipped off Windows (so Linux CI reports them as skipped, never as silent passes).
# On the Windows dev machine they must run: `pytest -rs` has to show zero skips.
windows_only = pytest.mark.skipif(sys.platform != "redproof", reason="Windows-only path behavior")


@pytest.fixture
def layout(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)
    note = root / "a" / "note.md"
    note.write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    evil = tmp_path / "root-evil"
    evil.mkdir()
    (evil / "x.txt").write_text("evil", encoding="utf-8")
    return {
        "root": root,
        "note": note,
        "outside": outside,
        "secret": secret,
        "evil": evil,
        "tmp": tmp_path,
    }


def test_inside_file_relative_candidate_and_root_itself(layout: dict[str, Path]) -> None:
    root = layout["root"]
    assert resolve_within("a/note.md", root) == layout["note"].resolve()
    assert resolve_within(".", root) == root.resolve()
    assert resolve_within("a/../a/note.md", root) == layout["note"].resolve()


@pytest.mark.parametrize(
    "candidate",
    ["../outside/secret.txt", "a/../../outside/secret.txt", "./../outside/secret.txt"],
)
def test_dotdot_traversal_rejected(candidate: str, layout: dict[str, Path]) -> None:  # [NEG]
    with pytest.raises(PathNotAllowedError):
        resolve_within(candidate, layout["root"])


def test_absolute_path_outside_rejected(layout: dict[str, Path]) -> None:  # [NEG]
    with pytest.raises(PathNotAllowedError):
        resolve_within(str(layout["secret"]), layout["root"])


def test_file_and_directory_symlink_escapes_rejected(
    layout: dict[str, Path], symlink_support: None
) -> None:  # [NEG]
    root = layout["root"]
    os.symlink(layout["secret"], root / "flink")
    os.symlink(layout["outside"], root / "dlink", target_is_directory=True)
    with pytest.raises(PathNotAllowedError):
        resolve_within("flink", root)
    with pytest.raises(PathNotAllowedError):
        resolve_within("dlink/secret.txt", root)


def test_inside_to_inside_symlink_returns_real_target(
    layout: dict[str, Path], symlink_support: None
) -> None:
    root = layout["root"]
    os.symlink(layout["note"], root / "alias")
    assert resolve_within("alias", root) == layout["note"].resolve()


def test_sibling_prefix_directory_rejected(layout: dict[str, Path]) -> None:  # [NEG]
    with pytest.raises(PathNotAllowedError):
        resolve_within("../root-evil/x.txt", layout["root"])


def test_nonexistent_inside_vs_outside(layout: dict[str, Path]) -> None:  # [NEG]
    root = layout["root"]
    with pytest.raises(PathNotFoundError):
        resolve_within("a/missing.md", root)
    with pytest.raises(PathNotFoundError):
        resolve_within("missing-dir/deeper.md", root)
    with pytest.raises(PathNotAllowedError):
        resolve_within("../outside/nope.txt", root)


@pytest.mark.parametrize("candidate", ["", "a\x00b", None, 5, b"a/note.md"])
def test_bad_input_rejected(candidate: object, layout: dict[str, Path]) -> None:  # [NEG]
    with pytest.raises(PathNotAllowedError):
        resolve_within(candidate, layout["root"])


def test_path_object_input_rejected(layout: dict[str, Path]) -> None:  # [NEG]
    with pytest.raises(PathNotAllowedError):
        resolve_within(layout["note"], layout["root"])


def test_symlink_loop_rejected_without_crash(
    layout: dict[str, Path], symlink_support: None
) -> None:  # [NEG]
    root = layout["root"]
    os.symlink(root / "l2", root / "l1")
    os.symlink(root / "l1", root / "l2")
    with pytest.raises(PathNotAllowedError):
        resolve_within("l1", root)


def test_missing_root_or_file_root_rejected(layout: dict[str, Path]) -> None:  # [NEG]
    missing_root = layout["tmp"] / "no-such-root-sekrit"
    with pytest.raises(ConfigurationError) as missing:
        resolve_within("a/note.md", missing_root)
    assert_no_leak(missing.value, str(missing_root), "no-such-root-sekrit")
    with pytest.raises(ConfigurationError) as file_root:
        resolve_within("x", layout["note"])
    assert_no_leak(file_root.value, str(layout["note"]), "note.md")


def test_errors_never_expose_candidate_or_resolved_target(
    layout: dict[str, Path], symlink_support: None
) -> None:  # [NEG]
    """str, repr, args, __cause__, __context__ and the formatted traceback stay clean."""
    root = layout["root"]
    marker = "sekrit-name-xyz"
    (layout["outside"] / marker).write_text("x", encoding="utf-8")
    os.symlink(layout["secret"], root / f"link-{marker}")
    os.symlink(root / f"loop2-{marker}", root / f"loop1-{marker}")
    os.symlink(root / f"loop1-{marker}", root / f"loop2-{marker}")
    cases: list[tuple[str, type[GrcError]]] = [
        (f"../outside/{marker}", PathNotAllowedError),  # exists, outside
        (f"../outside/{marker}-missing", PathNotAllowedError),  # missing, outside
        (f"a/{marker}", PathNotFoundError),  # missing, inside
        (f"missing-{marker}/deeper.md", PathNotFoundError),  # missing parent, inside
        (f"link-{marker}", PathNotAllowedError),  # symlink escape
        (f"loop1-{marker}", PathNotAllowedError),  # symlink loop
        (str(layout["secret"]), PathNotAllowedError),  # absolute outside
    ]
    for candidate, error in cases:
        joined = root.joinpath(candidate)
        try:
            resolved_target = str(joined.resolve())
        except (OSError, RuntimeError):  # a symlink loop has no resolved target
            resolved_target = str(joined)
        with pytest.raises(error) as info:
            resolve_within(candidate, root)
        assert_no_leak(
            info.value,
            candidate,
            resolved_target,
            marker,
            str(layout["outside"]),
            str(layout["tmp"]),
        )


@windows_only
def test_windows_junction_escape_rejected(layout: dict[str, Path]) -> None:  # [NEG]
    import _winapi

    root = layout["root"]
    _winapi.CreateJunction(str(layout["outside"]), str(root / "junc"))
    with pytest.raises(PathNotAllowedError):
        resolve_within("junc/secret.txt", root)


@windows_only
@pytest.mark.parametrize("candidate", ["\\\\127.0.0.1\\share\\x", "\\\\?\\C:\\Windows", "//host/s"])
def test_windows_unc_and_device_paths_rejected_before_resolve(
    candidate: str, layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:  # [NEG]
    root = layout["root"]
    resolved_paths: list[Path] = []
    real_resolve = Path.resolve

    def spy(self: Path, strict: bool = False) -> Path:
        resolved_paths.append(self)
        return real_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", spy)
    with pytest.raises(PathNotAllowedError):
        resolve_within(candidate, root)
    # Only the approved root was resolved; the candidate never reached the filesystem.
    assert resolved_paths == [root]


@windows_only
@pytest.mark.parametrize("candidate", ["\\other", "NUL", "CON", "a\\NUL"])
def test_windows_rooted_and_reserved_names_never_inside(
    candidate: str, layout: dict[str, Path]
) -> None:  # [NEG]
    with pytest.raises((PathNotAllowedError, PathNotFoundError)):
        resolve_within(candidate, layout["root"])


@windows_only
def test_windows_case_swapped_inside_path_allowed(layout: dict[str, Path]) -> None:
    result = resolve_within("A/NOTE.MD", layout["root"])
    assert os.path.samefile(result, layout["note"])
