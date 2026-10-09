"""After the ready line nothing else may reach the host's stdout pipe (ADR-004, ADR-007).

The Rust host kills the sidecar on any stdout byte after the ready line. These tests run the real
sidecar, and from inside it (right after the ready line) write to stdout in every way a library or
native code could: `print`, a raw `os.write(1, ...)`, and a native write that bypasses Python
(`WriteFile` on the Windows standard handle, `write(2)` from libc elsewhere). The parent checks
that the pipe saw exactly the ready line.

Every write must also SUCCEED. A write that failed against a dead handle would not reach the
pipe either, so "nothing arrived" alone would prove nothing about the redirect.
"""

import json
import secrets
import sys
import time
from pathlib import Path

from .childproc import minimal_env, run_python, running_python
from .test_startup import _get_health, _read_ready

FILE_TYPE_CHAR = 2  # GetFileType result for a character device such as NUL
STRAY_NATIVE = b"stray-native\n"

_STRAY_WRITES = r"""
import ctypes, json, os, runpy, sys
from grc_sidecar import main as sidecar_main

RESULT = os.environ["GRC_TEST_RESULT"]
DATA = b"stray-native\n"
real_emit = sidecar_main.ReadySignalServer._emit_ready


def native_write():
    if sys.platform == "win32":
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetFileType.argtypes = [wintypes.HANDLE]
        kernel32.GetFileType.restype = wintypes.DWORD
        kernel32.WriteFile.argtypes = [
            wintypes.HANDLE, ctypes.c_char_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
        ]
        kernel32.WriteFile.restype = wintypes.BOOL
        handle = kernel32.GetStdHandle(0xFFFFFFF5)
        written = wintypes.DWORD(0)
        ok = bool(kernel32.WriteFile(handle, DATA, len(DATA), ctypes.byref(written), None))
        return {"file_type": kernel32.GetFileType(handle), "ok": ok, "written": written.value}
    libc = ctypes.CDLL(None)
    libc.write.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_size_t]
    libc.write.restype = ctypes.c_ssize_t
    return {"written": libc.write(1, DATA, len(DATA))}


def emit_then_write_strays(self, port):
    real_emit(self, port)
    result = {"ready_stream_closed": self._ready_stream.closed}
    print("stray-print", flush=True)
    result["print"] = "ok"
    result["os_write"] = os.write(1, b"stray-fd\n")
    result["native"] = native_write()
    with open(RESULT, "w", encoding="utf-8") as handle:
        json.dump(result, handle)


sidecar_main.ReadySignalServer._emit_ready = emit_then_write_strays
runpy.run_module("grc_sidecar", run_name="__main__", alter_sys=True)
"""


def test_stray_writes_after_ready_never_reach_the_host_pipe(tmp_path: Path) -> None:  # [NEG]
    token = secrets.token_hex(32)
    result_file = tmp_path / "result.json"
    env = minimal_env(
        {
            "GRC_LOG_DIR": str(tmp_path / "logs"),
            "GRC_AUTH_TOKEN": token,
            "GRC_TEST_RESULT": str(result_file),
        }
    )
    stderr_path = tmp_path / "stderr.txt"
    with running_python(["-c", _STRAY_WRITES], env=env, cwd=tmp_path, stderr_path=stderr_path) as (
        _proc,
        out,
    ):
        line, port = _read_ready(out, stderr_path)
        deadline = time.monotonic() + 30
        while not result_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert result_file.exists(), "the stray-write probe never reported"
        assert _get_health(port, {"X-GRC-Auth": token}).status_code == 200
    # After the child is killed and its pipe is drained: exactly the ready line, nothing else.
    assert out.lines == [line], f"stdout held more than the ready line: {out.lines!r}"

    result = json.loads(result_file.read_text(encoding="utf-8"))
    expected_native: dict[str, object] = (
        {"file_type": FILE_TYPE_CHAR, "ok": True, "written": len(STRAY_NATIVE)}
        if sys.platform == "win32"
        else {"written": len(STRAY_NATIVE)}
    )
    # Every write succeeded (it went somewhere harmless) instead of failing against a dead handle.
    assert result == {
        "ready_stream_closed": True,
        "print": "ok",
        "os_write": len(b"stray-fd\n"),
        "native": expected_native,
    }


_SEAL_FAILS = r"""
import os, runpy


def refuse(fd):
    raise OSError("dup refused")


os.dup = refuse
runpy.run_module("grc_sidecar", run_name="__main__", alter_sys=True)
"""


def test_startup_fails_closed_when_stdout_cannot_be_sealed(tmp_path: Path) -> None:  # [NEG]
    env = minimal_env(
        {"GRC_LOG_DIR": str(tmp_path / "logs"), "GRC_AUTH_TOKEN": secrets.token_hex(32)}
    )
    result = run_python(["-c", _SEAL_FAILS], env=env, cwd=tmp_path, timeout=60)
    assert result.returncode == 2
    assert result.stderr == "grc-sidecar: Standard output could not be sealed\n"
    assert result.stdout == ""
