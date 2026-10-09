"""Runtime enforcement of CLAUDE.md 3.5 (no process creation from app code), ADR-007.

A PEP 578 audit hook vetoes every process-creation event the Python runtime raises. This
matters because libraries start processes without us asking: on Windows, CPython 3.11's
`platform.system()` (called by uvicorn while it starts) runs `ver` through `cmd.exe`. With the
guard installed that attempt raises SpawnBlockedError, a PermissionError, which the standard
library treats as "could not run `ver`" and falls back to `sys.getwindowsversion()`.

Limits, stated plainly: an audit hook is not a sandbox. Native code that calls CreateProcess or
fork directly (for example through ctypes) does not raise these events. The guard cannot be
removed once installed (Python has no API for that).
"""

import sys
from typing import Any, Final

from grc_sidecar.errors import ProcessGuardError, SpawnBlockedError

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

_installed = False
_selftest_seen = False


def _audit_hook(event: str, args: tuple[Any, ...]) -> None:
    # Fast path: this runs for EVERY audit event in the process (file opens, imports, socket
    # calls), so the first thing it does is one set lookup, and it returns for anything else.
    # It never looks at `args`, which can hold an environment (and so a token).
    if event not in _PROCESS_EVENTS:
        if event == _SELFTEST_EVENT:
            global _selftest_seen
            _selftest_seen = True
        return
    raise SpawnBlockedError


def is_installed() -> bool:
    return _installed


def install_process_guard() -> None:
    """Install the hook, then prove it is live; raise ProcessGuardError if it is not.

    `sys.addaudithook` does nothing if a hook installed earlier vetoes the new one, so the
    self-check fires a private event and requires that this hook saw it.
    """
    global _installed, _selftest_seen
    if _installed:
        return
    _selftest_seen = False
    try:
        sys.addaudithook(_audit_hook)
        sys.audit(_SELFTEST_EVENT)
    except Exception:
        raise ProcessGuardError from None
    if not _selftest_seen:
        raise ProcessGuardError
    _installed = True
