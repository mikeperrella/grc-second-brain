"""Helpers for tests that start real child processes (explicit env, no shell)."""

import os
import queue
import subprocess
import sys
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TextIO

CHILD_GUARD_DIR = Path(__file__).resolve().parent / "_child_guard"


def minimal_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """A deliberately small environment for child processes (no inherited secrets)."""
    env = {
        "OTEL_SDK_DISABLED": "true",
        "OTEL_TRACES_EXPORTER": "none",
        "PYTHONPATH": str(CHILD_GUARD_DIR),  # activates the egress guard in the child
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if sys.platform == "win32":
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]  # Winsock needs it
    env.update(extra or {})
    return env


def run_python(
    args: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    timeout: float = 60,
) -> subprocess.CompletedProcess[str]:
    """Run the venv Python to completion. env=None inherits the current environment."""
    return subprocess.run(  # noqa: S603  (fixed argument list, shell=False)
        [sys.executable, *args],
        env=None if env is None else dict(env),
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        shell=False,
    )


class StdoutCollector:
    """Reads a child's stdout on a thread so tests can wait on lines with a timeout."""

    def __init__(self, stream: TextIO) -> None:
        self.lines: list[str] = []
        self._queue: queue.Queue[str] = queue.Queue()
        self._thread = threading.Thread(target=self._pump, args=(stream,), daemon=True)
        self._thread.start()

    def _pump(self, stream: TextIO) -> None:
        for line in stream:
            self.lines.append(line)
            self._queue.put(line)
        self._queue.put("")  # end-of-output marker

    def next_line(self, timeout: float) -> str:
        """Next stdout line; "" means the child closed stdout. Raises queue.Empty on timeout."""
        return self._queue.get(timeout=timeout)

    def finish(self, timeout: float) -> None:
        self._thread.join(timeout)


@contextmanager
def running_python(
    args: Sequence[str], *, env: Mapping[str, str], cwd: Path, stderr_path: Path
) -> Iterator[tuple[subprocess.Popen[str], StdoutCollector]]:
    """Start a child, always kill and reap it, and close its pipes afterwards."""
    with stderr_path.open("w", encoding="utf-8") as stderr_file:
        proc = subprocess.Popen(  # noqa: S603  (fixed argument list, shell=False)
            [sys.executable, *args],
            env=dict(env),
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=stderr_file,
            text=True,
            shell=False,
        )
        assert proc.stdout is not None
        collector = StdoutCollector(proc.stdout)
        try:
            yield proc, collector
        finally:
            proc.kill()
            proc.wait(timeout=30)
            collector.finish(10)
            proc.stdout.close()
