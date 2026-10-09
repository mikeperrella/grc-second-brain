"""Real-child-process tests: `python -m grc_sidecar` with an explicit minimal env."""

import asyncio
import json
import queue
import secrets
import socket
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from grc_sidecar.main import bind_loopback_socket
from grc_sidecar.security.net import create_async_client

from .childproc import StdoutCollector, minimal_env, run_python, running_python

MISSING_MESSAGE = "grc-sidecar: Authentication token is missing\n"
INVALID_MESSAGE = "grc-sidecar: Authentication token is invalid\n"


@pytest.fixture
def token() -> str:
    return secrets.token_hex(32)


def _env(tmp_path: Path, token: str | None) -> dict[str, str]:
    extra = {"GRC_LOG_DIR": str(tmp_path / "logs")}
    if token is not None:
        extra["GRC_AUTH_TOKEN"] = token
    return minimal_env(extra)


def _read_ready(out: StdoutCollector, stderr_path: Path) -> tuple[str, int]:
    try:
        line = out.next_line(timeout=60)
    except queue.Empty:
        pytest.fail(f"no ready line within 60 s; stderr: {stderr_path.read_text(encoding='utf-8')}")
    payload = json.loads(line)
    return line, payload["port"]


@contextmanager
def _sidecar(tmp_path: Path, token: str) -> Iterator[tuple[str, int, StdoutCollector]]:
    stderr_path = tmp_path / "stderr.txt"
    with running_python(
        ["-m", "grc_sidecar"], env=_env(tmp_path, token), cwd=tmp_path, stderr_path=stderr_path
    ) as (_proc, out):
        line, port = _read_ready(out, stderr_path)
        yield line, port, out


def _get_health(port: int, headers: dict[str, str] | None = None) -> httpx.Response:
    async def go() -> httpx.Response:
        async with create_async_client(f"http://127.0.0.1:{port}") as client:
            return await client.get("/health", headers=headers)

    return asyncio.run(go())


def test_ready_line_has_the_exact_shape_and_health_works(tmp_path: Path, token: str) -> None:
    with _sidecar(tmp_path, token) as (line, port, _out):
        payload = json.loads(line)
        assert set(payload) == {"event", "port"}
        assert payload["event"] == "ready"
        assert type(payload["port"]) is int
        assert 1 <= port <= 65535
        assert line == f'{{"event":"ready","port":{port}}}\n'
        response = _get_health(port, {"X-GRC-Auth": token})
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


PORT_CLOSE_DEADLINE_SECONDS = 10.0
PORT_POLL_FIRST_SECONDS = 0.005
PORT_POLL_MAX_SECONDS = 0.25


def _wait_until_port_refuses(port: int, deadline_seconds: float) -> str | None:
    """Poll until a connection to the loopback port fails (the listener is really gone).

    Returns None as soon as a connect attempt raises OSError. If the port is still accepting
    connections when the deadline passes, returns a description of the last accepted one.

    Why this is polled: when `Popen.wait()` returns for a killed process, Windows may not have
    closed that process's listening socket yet, so a connect right after the kill can still be
    accepted for a few milliseconds (measured: about 1 kill in 10 under CPU load). The wait is on
    the condition itself and bounded by a deadline, so a listener that is never released (a leaked
    socket, a surviving child) still fails.

    The poll interval doubles up to a cap, which keeps the number of connects under a few dozen
    in the whole deadline. That matters: connections to a listener that never calls accept()
    pile up in its backlog (2048 for the sidecar), and a full backlog also refuses connects.
    """
    deadline = time.monotonic() + deadline_seconds
    interval = PORT_POLL_FIRST_SECONDS
    while True:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
                accepted = f"accepted a connection from local address {connection.getsockname()}"
        except OSError:
            return None
        if time.monotonic() >= deadline:
            return accepted
        time.sleep(interval)
        interval = min(interval * 2, PORT_POLL_MAX_SECONDS)


def test_exactly_one_line_is_ever_written_to_stdout(tmp_path: Path, token: str) -> None:  # [NEG]
    with _sidecar(tmp_path, token) as (line, port, out):
        _get_health(port, {"X-GRC-Auth": token})
        _get_health(port)  # an unauthenticated request must not print anything either
    assert out.lines == [line], (
        f"stdout must hold exactly the ready line {line!r}, "
        f"but it held {len(out.lines)} line(s): {out.lines!r}"
    )
    still_open = _wait_until_port_refuses(port, PORT_CLOSE_DEADLINE_SECONDS)
    assert still_open is None, (
        f"the killed sidecar's port {port} still accepted connections "
        f"{PORT_CLOSE_DEADLINE_SECONDS}s after it was killed ({still_open})"
    )


def test_real_server_rejects_missing_and_wrong_tokens(tmp_path: Path, token: str) -> None:  # [NEG]
    wrong = token[:-1] + ("0" if token[-1] != "0" else "1")
    with _sidecar(tmp_path, token) as (_line, port, _out):
        for headers in (None, {"X-GRC-Auth": wrong}):
            response = _get_health(port, headers)
            assert response.status_code == 401
            assert response.content == b""
            assert "www-authenticate" not in response.headers


def test_bind_loopback_socket_is_ipv4_loopback_and_ephemeral() -> None:  # [NEG]
    sock = bind_loopback_socket()
    try:
        host, port = sock.getsockname()
        assert sock.family == socket.AF_INET
        assert host == "127.0.0.1"
        assert port > 0
    finally:
        sock.close()


def test_server_is_not_reachable_over_ipv6_loopback(tmp_path: Path, token: str) -> None:  # [NEG]
    # A dual-stack wildcard bind ("::") would accept this connection.
    with _sidecar(tmp_path, token) as (_line, port, _out), pytest.raises(OSError):
        socket.create_connection(("::1", port), timeout=2).close()


def test_missing_token_exits_2_with_fixed_message(tmp_path: Path) -> None:  # [NEG]
    result = run_python(["-m", "grc_sidecar"], env=_env(tmp_path, None), cwd=tmp_path)
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == MISSING_MESSAGE


def test_invalid_token_exits_2_without_echoing_it(tmp_path: Path) -> None:  # [NEG]
    bad = "TOKEN-VALUE-THAT-MUST-NOT-LEAK"
    result = run_python(["-m", "grc_sidecar"], env=_env(tmp_path, bad), cwd=tmp_path)
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == INVALID_MESSAGE
    assert bad not in result.stdout + result.stderr


def test_token_never_appears_in_log_stdout_or_stderr(tmp_path: Path, token: str) -> None:  # [NEG]
    with _sidecar(tmp_path, token) as (_line, port, out):
        _get_health(port, {"X-GRC-Auth": token})
        _get_health(port, {"X-GRC-Auth": "wrong-" + token})
    log_text = (tmp_path / "logs" / "grc-sidecar.log").read_text(encoding="utf-8")
    assert log_text  # the log was really written, so the check below is not vacuous
    stderr_text = (tmp_path / "stderr.txt").read_text(encoding="utf-8")
    for text in (log_text, "".join(out.lines), stderr_text):
        assert token not in text


def test_child_processes_run_under_the_egress_guard() -> None:  # [NEG]
    code = "import socket; socket.create_connection(('192.0.2.1', 80), timeout=1)"
    result = run_python(["-c", code], env=minimal_env())
    assert result.returncode != 0
    assert "EgressBlockedError" in result.stderr


_NO_HOME = """
import runpy, sys
if sys.platform != "win32":
    import pwd

    def no_such_user(uid):
        raise KeyError(uid)

    pwd.getpwuid = no_such_user  # HOME is already absent; this removes the user-database fallback
runpy.run_module("grc_sidecar", run_name="__main__", alter_sys=True)
"""


def test_missing_home_directory_exits_2_with_one_fixed_line(  # [NEG]
    tmp_path: Path, token: str
) -> None:
    env = minimal_env({"GRC_AUTH_TOKEN": token})  # no GRC_LOG_DIR, no home directory variables
    result = run_python(["-c", _NO_HOME], env=env, cwd=tmp_path, timeout=60)
    assert result.returncode == 2
    assert result.stderr == "grc-sidecar: Home directory is not available; set GRC_LOG_DIR\n"
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


_BIND_FAILS = """
import runpy, socket


def refuse(self, address):
    raise OSError("bind refused")


socket.socket.bind = refuse
runpy.run_module("grc_sidecar", run_name="__main__", alter_sys=True)
"""


def test_a_failed_loopback_bind_exits_2_with_one_fixed_line(  # [NEG]
    tmp_path: Path, token: str
) -> None:
    env = minimal_env({"GRC_LOG_DIR": str(tmp_path / "logs"), "GRC_AUTH_TOKEN": token})
    result = run_python(["-c", _BIND_FAILS], env=env, cwd=tmp_path, timeout=60)
    assert result.returncode == 2
    assert result.stderr == "grc-sidecar: Loopback socket could not be bound\n"
    assert "Traceback" not in result.stderr
    assert result.stdout == ""
