"""Runtime enforcement of CLAUDE.md 3.5 (no process creation from app code), ADR-007.

A PEP 578 audit hook vetoes every process-creation event the Python runtime raises. This
matters because libraries start processes without us asking: on Windows, CPython 3.11's
`platform.system()` (called by uvicorn while it starts) runs `ver` through `cmd.exe`. With the
guard installed that attempt raises SpawnBlockedError, a PermissionError, which the standard
library treats as "could not run `ver`" and falls back to `sys.getwindowsversion()`.

The same hook also guards native extension loading (ADR-007 amendment). Loading a native library
runs arbitrary code inside this process, which is worse than starting a process, so
`sqlite3.load_extension` is allowed for exactly one path, the vendored sqlite-vec library, and
vetoed for every other path. Without an approved path every extension load is vetoed.

Limits, stated plainly: an audit hook is not a sandbox. Native code that calls CreateProcess or
fork directly (for example through ctypes) does not raise these events, and native code inside an
approved extension can load further libraries without any Python-visible event. The guard cannot
be removed once installed (Python has no API for that).
"""

import os
import sys
from pathlib import Path
from typing import Any, Final

from grc_sidecar.errors import ExtensionLoadBlockedError, ProcessGuardError, SpawnBlockedError

# Every audit event CPython raises before it creates a process.
_PROCESS_EVENTS: Final[frozenset[str]] = frozenset(
    {
        "subprocess.Popen",
        "_winapi.CreateProcess",
        "os.system",
        "os.exec",
        "os.spawn",
        "os.posix_spawn",
        "os.fork",
        "os.forkpty",
        "os.startfile",
    }
)
_SELFTEST_EVENT: Final = "grc.process_guard.selftest"
# CPython raises this with (connection, path) before it loads a SQLite extension. `path` is the
# string the caller passed, unchanged: no normalization and no file suffix.
_EXTENSION_EVENT: Final = "sqlite3.load_extension"

_installed = False
_selftest_seen = False
# The normalized path of the one extension that may be loaded; None means "none may".
_approved_extension: str | None = None


def _normalize_extension_path(path: str) -> str:
    """Compare-ready form: separators and `..` collapsed, case folded on Windows only.

    A normalization mismatch always fails closed. Two spellings of one file that this does not
    reduce to the same string (a short 8.3 name, a symlink, a relative path) are DENIED, never
    wrongly allowed, because the only way to pass is to equal the approved string exactly.
    """
    return os.path.normcase(os.path.normpath(path))


def _check_extension_load(args: tuple[Any, ...]) -> None:
    approved = _approved_extension
    path = args[1] if len(args) > 1 else None
    if approved is None or not isinstance(path, str) or not os.path.isabs(path):
        raise ExtensionLoadBlockedError
    if _normalize_extension_path(path) != approved:
        raise ExtensionLoadBlockedError


def _audit_hook(event: str, args: tuple[Any, ...]) -> None:
    # Fast path: this runs for EVERY audit event in the process (file opens, imports, socket
    # calls), so the first thing it does is one set lookup, and it returns for anything else.
    # Process events never look at `args`, which can hold an environment (and so a token).
    if event not in _PROCESS_EVENTS:
        if event == _SELFTEST_EVENT:
            global _selftest_seen
            _selftest_seen = True
        elif event == _EXTENSION_EVENT:
            _check_extension_load(args)
        return
    raise SpawnBlockedError


def is_installed() -> bool:
    return _installed


def install_process_guard(approved_extension: Path | None = None) -> None:
    """Install the hook, then prove it is live; raise ProcessGuardError if it is not.

    `sys.addaudithook` does nothing if a hook installed earlier vetoes the new one, so the
    self-check fires a private event and requires that this hook saw it.

    `approved_extension` is the one native SQLite extension that may be loaded (an absolute
    path, as `sqlite_vec.loadable_path()` returns it). Leave it out and none may.
    """
    global _installed, _selftest_seen, _approved_extension
    if _installed:
        return
    _selftest_seen = False
    _approved_extension = (
        None if approved_extension is None else _normalize_extension_path(str(approved_extension))
    )
    try:
        sys.addaudithook(_audit_hook)
        sys.audit(_SELFTEST_EVENT)
    except Exception:
        raise ProcessGuardError from None
    if not _selftest_seen:
        raise ProcessGuardError
    _installed = True
