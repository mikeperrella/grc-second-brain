"""Path guard: confine user/frontend-supplied paths to one approved root (CLAUDE.md 3.6).

resolve_within canonicalizes the path (following symlinks and Windows junctions) and
then checks it is still inside the approved root, so "..", absolute paths, and links
that point outside are all rejected.

Callers must open the Path this returns, never the original string. There is still a
small window between this check and the later open (time-of-check/time-of-use): a
local attacker who can swap a link in that window could redirect the open. That is
accepted for a single-user desktop app, and the Rust host repeats the check (M2).

Error messages are fixed strings and never include the candidate or the resolved
target. The OS errors raised while resolving contain the path, so they are caught in
small helpers and the domain errors are raised afterwards, outside any `except`
block. That leaves __cause__ and __context__ empty, so the path cannot surface
through exception chaining, tracebacks, or logs.
"""

import os
from pathlib import Path

from grc_sidecar.errors import ConfigurationError, PathNotAllowedError, PathNotFoundError

_UNC_PREFIXES = ("\\\\", "//")


def _resolve_root(approved_root: Path) -> Path | None:
    try:
        root = approved_root.resolve(strict=True)
        return root if root.is_dir() else None
    except (OSError, RuntimeError, ValueError):
        return None


def _check_candidate_text(candidate: object) -> str:
    """Reject unacceptable input before the filesystem is touched."""
    if not isinstance(candidate, str) or not candidate or "\x00" in candidate:
        raise PathNotAllowedError
    if os.name == "nt" and candidate.startswith(_UNC_PREFIXES):
        # UNC and device paths (\\server\share, \\?\C:\...) can reach the network
        # or raw devices, so they are never accepted on Windows.
        raise PathNotAllowedError
    return candidate


def _resolve(target: Path, *, strict: bool) -> tuple[Path | None, bool]:
    """Resolve target; return (resolved, missing). Never raises OS errors.

    resolved is None on failure. missing is True only when the path does not exist.
    Other failures (symlink loops, invalid names, access errors) return (None, False).
    """
    try:
        return target.resolve(strict=strict), False
    except (FileNotFoundError, NotADirectoryError):
        return None, True
    except (OSError, RuntimeError, ValueError):
        return None, False


def resolve_within(candidate: object, approved_root: Path) -> Path:
    """Return the canonical path of candidate if it exists inside approved_root.

    Raises:
        ConfigurationError: approved_root is missing or not a directory.
        PathNotAllowedError: bad input, or the path resolves outside approved_root.
        PathNotFoundError: the path is inside approved_root but does not exist.
    """
    root = _resolve_root(approved_root)
    if root is None:
        raise ConfigurationError
    target = root.joinpath(_check_candidate_text(candidate))

    resolved, missing = _resolve(target, strict=True)
    if missing:
        # Do not reveal whether paths outside the root exist: decide on where the
        # path *would* be, using a non-strict resolve.
        would_be, _ = _resolve(target, strict=False)
        if would_be is not None and would_be.is_relative_to(root):
            raise PathNotFoundError
        raise PathNotAllowedError
    if resolved is None or not resolved.is_relative_to(root):
        raise PathNotAllowedError
    return resolved
