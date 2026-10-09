"""Does any subprocess the real sidecar starts inherit the auth token? (ADR-004, CLAUDE.md 3.4/3.5)

On Windows, CPython 3.11's `platform.system()` runs `ver` through a shell (`cmd.exe`), and
uvicorn calls it while the server starts. So the shipped sidecar does spawn `cmd.exe` from library
code (see ADR-004). This test answers the follow-up question: when that spawn happens, is
`GRC_AUTH_TOKEN` still in the environment the child would inherit?

How: the sidecar's real `__main__.main()` runs in a child Python under a small wrapper that patches
`subprocess.Popen.__init__`. At every spawn it appends one JSON line to a file, recording whether
the token is visible (a) in `os.environ` and (b) in the operating system's process environment
block, which is what a `CreateProcess` call with no explicit environment hands to the new process.
The wrapper never changes what a spawn does and never logs the token.

The first record is a CONTROL: the wrapper itself starts a harmless child BEFORE the sidecar reads
and deletes the token, so that record must show the token present. If it did not, the recording
could not tell "present" from "absent" and the assertions below would prove nothing.

Since ADR-007 the sidecar installs a process guard (an audit hook) that vetoes process creation,
so the `ver` attempt that CPython 3.11's `platform.system()` makes on Windows is BLOCKED. Each
record therefore also says whether the spawn really started (`started`). The assertions:
nothing the sidecar attempts ever starts, and on Windows the blocked `ver` attempt is present,
which proves the guard met the real-world spawn (a test that saw no attempt at all would pass
even if the guard did nothing). The control spawn must still start.
"""

import json
import secrets
import sys
from pathlib import Path

from .childproc import minimal_env, running_python
from .test_startup import _get_health, _read_ready

# Runs in the child. Standard library only. The token value is read (not removed) so spawns can be
# checked against it; the sidecar's own `main()` is what deletes it from the environment.
_WRAPPER = r"""
import json, os, runpy, subprocess, sys

LOG = os.environ["GRC_TEST_SPAWN_LOG"]
TOKEN = os.environ.get("GRC_AUTH_TOKEN", "")


def process_env_has_token():
    import ctypes
    if sys.platform == "win32":
        buffer = ctypes.create_unicode_buffer(8)
        return ctypes.windll.kernel32.GetEnvironmentVariableW("GRC_AUTH_TOKEN", buffer, 8) != 0
    libc = ctypes.CDLL(None)
    libc.getenv.restype = ctypes.c_char_p
    return libc.getenv(b"GRC_AUTH_TOKEN") is not None


real_init = subprocess.Popen.__init__


def recording_init(self, args, *positional, **keywords):
    record = {
        "args": str(args),
        "shell": bool(keywords.get("shell", False)),
        "env_argument_is_none": keywords.get("env") is None,
        "token_name_in_os_environ": "GRC_AUTH_TOKEN" in os.environ,
        "token_value_in_os_environ": any(TOKEN in value for value in os.environ.values()),
        "token_in_process_environment_block": process_env_has_token(),
    }
    record["started"] = False
    try:
        real_init(self, args, *positional, **keywords)
        record["started"] = True
    finally:
        with open(LOG, "a", encoding="utf-8") as log:
            log.write(json.dumps(record) + "\n")


subprocess.Popen.__init__ = recording_init

# CONTROL: a spawn before the sidecar has read and deleted the token. It must record the token.
subprocess.run([sys.executable, "-c", "pass"], check=True)

runpy.run_module("grc_sidecar", run_name="__main__", alter_sys=True)
"""


def _records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_no_subprocess_started_by_the_real_sidecar_inherits_the_token(tmp_path: Path) -> None:
    token = secrets.token_hex(32)
    spawn_log = tmp_path / "spawns.jsonl"
    env = minimal_env(
        {
            "GRC_LOG_DIR": str(tmp_path / "logs"),
            "GRC_AUTH_TOKEN": token,
            "GRC_TEST_SPAWN_LOG": str(spawn_log),
        }
    )

    with running_python(
        ["-c", _WRAPPER], env=env, cwd=tmp_path, stderr_path=tmp_path / "stderr.txt"
    ) as (proc, out):
        _line, port = _read_ready(out, tmp_path / "stderr.txt")
        # The sidecar is up and serving with the token: the wrapper did not break startup.
        assert _get_health(port, {"X-GRC-Auth": token}).status_code == 200
        assert proc.poll() is None
        records = _records(spawn_log)

    # The control is first, and it proves the recording can see the token.
    assert records, "the wrapper recorded nothing, not even its control spawn"
    control, *during_sidecar_run = records
    assert control["token_name_in_os_environ"] is True
    assert control["token_value_in_os_environ"] is True
    assert control["token_in_process_environment_block"] is True
    assert control["started"] is True, "the control spawn must start (the guard is not active yet)"

    # Every spawn attempted by the sidecar itself (uvicorn, the standard library) is token-free,
    # by name and by value, in Python's view and in the operating system's, AND is blocked.
    for record in during_sidecar_run:
        assert record["token_name_in_os_environ"] is False, record
        assert record["token_value_in_os_environ"] is False, record
        assert record["token_in_process_environment_block"] is False, record
        assert record["started"] is False, f"a process was started by the sidecar: {record}"

    if sys.platform == "win32":
        # The finding itself: uvicorn's platform check still TRIES to run `ver` through a shell,
        # and the guard stops it.
        assert any(r["shell"] is True and r["args"] == "ver" for r in during_sidecar_run), (
            during_sidecar_run
        )
    else:
        assert not during_sidecar_run, during_sidecar_run
