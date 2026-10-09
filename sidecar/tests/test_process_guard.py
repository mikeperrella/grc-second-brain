"""The process guard (ADR-007, CLAUDE.md 3.5): vetoes process creation, nothing else.

The real guard is installed only in CHILD processes. An audit hook cannot be removed, so the
pytest process itself must never get one (conftest.py fails the session if it does). The hook
function is also tested directly, in-process, because calling it does not install anything.
"""

import json
import queue
import secrets
import sys
import textwrap
import time
from pathlib import Path

import pytest

from grc_sidecar.errors import SpawnBlockedError
from grc_sidecar.security import process_guard

from .childproc import minimal_env, run_python, running_python
from .test_startup import _get_health, _read_ready

# Written out here (not imported) so that dropping an event from the module's set fails a test.
EXPECTED_EVENTS = {
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


def test_the_guard_covers_exactly_the_expected_process_events() -> None:  # [NEG]
    assert process_guard._PROCESS_EVENTS == EXPECTED_EVENTS


@pytest.mark.parametrize("event", sorted(EXPECTED_EVENTS))
def test_the_hook_vetoes_every_process_creation_event(event: str) -> None:  # [NEG]
    with pytest.raises(SpawnBlockedError):
        process_guard._audit_hook(event, ())


@pytest.mark.parametrize(
    "event",
    ["open", "import", "socket.bind", "socket.connect", "os.listdir", "exec", "grc.anything", ""],
)
def test_the_hook_never_raises_for_any_other_event(event: str) -> None:
    # "exec" is the code-object event (not os.exec), and it must stay allowed.
    process_guard._audit_hook(event, ("anything",))


def test_a_blocked_spawn_is_a_permission_error() -> None:
    # The standard library only tolerates a failed `ver` lookup for OSError.
    assert issubclass(SpawnBlockedError, PermissionError)
    assert str(SpawnBlockedError()) == "Starting a process is not allowed"


def _run_child(tmp_path: Path, body: str, *args: str) -> object:
    """Run a child script (guard-aware) and return its single JSON stdout line, parsed."""
    script = tmp_path / "child.py"
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    result = run_python([str(script), *args], env=minimal_env(), cwd=tmp_path, timeout=120)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


_API_PROBES = r"""
import json, os, subprocess, sys
from grc_sidecar.security.process_guard import install_process_guard

install_process_guard()
marker_dir = sys.argv[1]
py = sys.executable


def code_for(name):
    return "open(%r, 'w').close()" % os.path.join(marker_dir, name)


results = {}


def attempt(name, call):
    try:
        call(code_for(name))
        outcome = "started"
    except BaseException as exc:
        outcome = type(exc).__name__
    results[name] = outcome


attempt("popen", lambda c: subprocess.run([py, "-c", c], check=False))
attempt("popen_shell", lambda c: subprocess.run('"%s" -c "%s"' % (py, c), shell=True, check=False))
attempt("system", lambda c: os.system('"%s" -c "%s"' % (py, c)))
attempt("spawn", lambda c: os.spawnv(os.P_WAIT, py, [py, "-c", c]))
if sys.platform == "win32":
    import _winapi
    attempt("winapi", lambda c: _winapi.CreateProcess(
        py, '"%s" -c "%s"' % (py, c), None, None, False, 0, None, None, subprocess.STARTUPINFO()))
    # A path that does not exist: if the guard failed, this is FileNotFoundError, not a launch.
    attempt("startfile", lambda c: os.startfile(os.path.join(marker_dir, "no-such-file.txt")))
else:
    def fork_call(c):
        pid = os.fork()
        if pid == 0:
            os._exit(0)
        os.waitpid(pid, 0)
    def forkpty_call(c):
        pid, _fd = os.forkpty()
        if pid == 0:
            os._exit(0)
        os.waitpid(pid, 0)
    attempt("fork", fork_call)
    attempt("forkpty", forkpty_call)
    attempt("posix_spawn", lambda c: os.posix_spawn(py, [py, "-c", c], os.environ))
# os.exec* replaces the process if it is not blocked, so it goes last.
attempt("exec", lambda c: os.execv(py, [py, "-c", c]))
print(json.dumps(results))
"""


def test_every_process_creation_api_is_blocked_under_the_guard(tmp_path: Path) -> None:  # [NEG]
    markers = tmp_path / "markers"
    markers.mkdir()
    results = _run_child(tmp_path, _API_PROBES, str(markers))
    platform_apis = (
        {"winapi", "startfile"} if sys.platform == "win32" else {"fork", "forkpty", "posix_spawn"}
    )
    expected = {"popen", "popen_shell", "system", "spawn", "exec"} | platform_apis
    assert results == dict.fromkeys(expected, "SpawnBlockedError")
    assert list(markers.iterdir()) == [], "a blocked spawn still ran its code"


_PLATFORM = r"""
import json, platform
from grc_sidecar.security.process_guard import install_process_guard
install_process_guard()
print(json.dumps(platform.system()))
"""


def test_platform_system_still_works_under_the_guard(tmp_path: Path) -> None:
    # On Windows 3.11 this is the call that tries to run `ver`; the standard library falls back.
    expected = {"win32": "Windows", "linux": "Linux", "darwin": "Darwin"}[sys.platform]
    assert _run_child(tmp_path, _PLATFORM) == expected


_ORDINARY = r"""
import importlib, json, os, socket, sys
from grc_sidecar.main import bind_loopback_socket
from grc_sidecar.security.process_guard import install_process_guard

install_process_guard()
done = {}
path = os.path.join(sys.argv[1], "note.txt")


def step(name, call):
    call()
    done[name] = True


def file_roundtrip():
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("hello")
    with open(path, encoding="utf-8") as handle:
        assert handle.read() == "hello"


def socket_roundtrip():
    server = bind_loopback_socket()
    server.listen(1)
    client = socket.create_connection(server.getsockname())
    conn, _ = server.accept()
    for s in (conn, client, server):
        s.close()


step("file", file_roundtrip)
step("socket", socket_roundtrip)
step("import", lambda: importlib.import_module("json.tool"))
step("listdir", lambda: os.listdir(sys.argv[1]))
step("unrelated_audit_event", lambda: sys.audit("grc.test.unrelated", 1))
print(json.dumps(done))
"""


def test_ordinary_activity_works_normally_under_the_guard(tmp_path: Path) -> None:
    assert _run_child(tmp_path, _ORDINARY, str(tmp_path)) == {
        "file": True,
        "socket": True,
        "import": True,
        "listdir": True,
        "unrelated_audit_event": True,
    }


_VETOED = r"""
import runpy, sys


def veto(event, args):
    if event == "sys.addaudithook":
        raise RuntimeError("no more hooks")


sys.addaudithook(veto)
runpy.run_module("grc_sidecar", run_name="__main__", alter_sys=True)
"""


def test_startup_fails_closed_when_the_guard_cannot_be_installed(tmp_path: Path) -> None:  # [NEG]
    env = minimal_env(
        {"GRC_LOG_DIR": str(tmp_path / "logs"), "GRC_AUTH_TOKEN": secrets.token_hex(32)}
    )
    stderr_path = tmp_path / "stderr.txt"
    with running_python(["-c", _VETOED], env=env, cwd=tmp_path, stderr_path=stderr_path) as (
        proc,
        out,
    ):
        try:
            first = out.next_line(timeout=30)
        except queue.Empty:
            pytest.fail("the sidecar neither exited nor printed anything within 30 s")
        # "" means the child closed stdout without printing; anything else is the ready line.
        assert first == "", "sidecar started although the guard could not be installed"
        assert proc.wait(timeout=30) == 2
    assert stderr_path.read_text(encoding="utf-8") == (
        "grc-sidecar: Process guard could not be installed\n"
    )
    assert out.lines == []


# Runs the real sidecar; after the ready line it tries to start a process from inside the server.
_POST_READY = r"""
import json, os, runpy, subprocess, sys
from grc_sidecar import main as sidecar_main

RESULT = os.environ["GRC_TEST_RESULT"]
real_emit = sidecar_main.ReadySignalServer._emit_ready


def emit_then_probe(self, port):
    real_emit(self, port)
    try:
        subprocess.run([sys.executable, "-c", "pass"], check=True)
        outcome = "started"
    except BaseException as exc:
        outcome = type(exc).__name__
    with open(RESULT, "w", encoding="utf-8") as handle:
        json.dump({"spawn": outcome}, handle)


sidecar_main.ReadySignalServer._emit_ready = emit_then_probe
runpy.run_module("grc_sidecar", run_name="__main__", alter_sys=True)
"""


def test_the_real_sidecar_blocks_spawns_after_it_is_ready(tmp_path: Path) -> None:  # [NEG]
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
    with running_python(["-c", _POST_READY], env=env, cwd=tmp_path, stderr_path=stderr_path) as (
        _proc,
        out,
    ):
        _line, port = _read_ready(out, stderr_path)
        deadline = time.monotonic() + 30
        while not result_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert result_file.exists(), "the post-ready probe never reported"
        assert _get_health(port, {"X-GRC-Auth": token}).status_code == 200
    assert json.loads(result_file.read_text(encoding="utf-8")) == {"spawn": "SpawnBlockedError"}
