"""Keep stdout reserved for the single ready line (CLAUDE.md 3.4, ADR-004).

The Rust host fails closed on any byte that follows the ready line. Logs already go to a file,
but a dependency, a stray `print`, or native code could still write to the host's pipe. So
before the server starts, the sidecar takes the pipe away from every way of reaching it:

1. `os.dup(1)` keeps a private copy of the pipe, used only to announce the port.
2. `os.dup2` points file descriptor 1 at the null device. `print`, `sys.stdout` and C code that
   writes to fd 1 now go nowhere.
3. On Windows, `SetStdHandle` points the operating system's standard-output handle at the same
   null device, so native code that calls `GetStdHandle(STD_OUTPUT_HANDLE)` and `WriteFile`
   cannot reach the pipe either.

The caller writes the ready line to the returned stream and closes it. Anything written to
stdout by anyone else is discarded, and is not logged: it could contain document content.
"""

import os
import sys
from typing import TextIO

from grc_sidecar.errors import StdoutSealError

_STDOUT_FD = 1


def _point_windows_std_handle_at_fd1() -> None:
    if sys.platform != "win32":  # also tells mypy this body is Windows-only
        return
    import ctypes
    import msvcrt
    from ctypes import wintypes

    std_output_handle = 0xFFFFFFF5  # (DWORD)-11
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetStdHandle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
    kernel32.SetStdHandle.restype = wintypes.BOOL
    if not kernel32.SetStdHandle(std_output_handle, msvcrt.get_osfhandle(_STDOUT_FD)):
        raise OSError(ctypes.get_last_error(), "SetStdHandle failed")


def seal_stdout() -> TextIO:
    """Redirect fd 1 (and the Windows standard handle) to the null device.

    Returns a text stream on a private copy of the original stdout, for the ready line only.
    Raises StdoutSealError if any step fails; the caller must then exit.
    """
    try:
        ready_fd = os.dup(_STDOUT_FD)
    except OSError:
        raise StdoutSealError from None
    try:
        null_fd = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(null_fd, _STDOUT_FD)
        finally:
            os.close(null_fd)
        _point_windows_std_handle_at_fd1()
        return os.fdopen(ready_fd, "w", encoding="ascii")
    except OSError:
        os.close(ready_fd)
        raise StdoutSealError from None
