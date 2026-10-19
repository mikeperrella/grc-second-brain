"""A contradiction run you start and then poll (ADR-019 addendum, G40).

A run asks a local model one question per pair, which can take minutes, so `start` answers at once
with a run id and the work goes on in a task; `get` returns the counts so far and, when the run is
done, its report. Modelled on the index job store (`vault/index_jobs.py`).

**One heavy job at a time.** A run holds the shared `JobSlot`, taken synchronously in `start`:
while a questionnaire run, an index job or another contradiction run holds it, `start` raises
`RunInProgressError` (409). The slot is released in a `finally`, so a failure, a cancellation and a
normal end all free it.

**Findings are never kept anywhere but here.** A finished run's report stays in memory only, for the
last `KEPT_RUNS` finished runs (a starting value, fewer than the index store's 20 because a report
holds quotes from the vault). Nothing is written to a file, and nothing from a report, a quote or a
model reply is logged: a failure is logged as a fixed word and the exception's class name.

**Threads.** Every counter is changed and read under one lock and `snapshot()` copies them all
together, so a reader never sees a half update.
"""

import asyncio
import logging
import secrets
import threading
from dataclasses import dataclass
from typing import Literal, Protocol

from grc_sidecar.contradictions.models import DetectionReport
from grc_sidecar.contradictions.service import ProgressCallback
from grc_sidecar.errors import GrcError, IndexNotBuiltError, RunNotFoundError
from grc_sidecar.jobs import JobSlot

_log = logging.getLogger(__name__)

KEPT_RUNS = 5  # finished runs kept in memory (a starting value; reports hold vault quotes)
RunState = Literal["running", "complete", "failed", "cancelled"]


class Detector(Protocol):
    """The part of `ContradictionDetector` a started run uses (a held or failing fake in tests)."""

    async def ensure_index(self) -> None: ...

    async def detect(
        self, *, scope: str | None, cap: int, on_progress: ProgressCallback | None = None
    ) -> DetectionReport: ...


@dataclass(frozen=True)
class RunStatus:
    """A consistent copy of a run's state. Integers, fixed words and, when complete, the report."""

    run_id: str
    status: RunState
    pairs_total: int | None
    pairs_done: int
    failure: str | None
    report: DetectionReport | None


class ContradictionRun:
    """One run's state. Thread-safe: every read and write goes through the lock."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._lock = threading.Lock()
        self._status: RunState = "running"
        self._total: int | None = None
        self._done = 0
        self._failure: str | None = None
        self._report: DetectionReport | None = None

    def progress(self, done: int, total: int) -> None:
        """The detector's callback: pairs finished, and the pairs there will be."""
        with self._lock:
            self._done = done
            self._total = total

    def finish(self, report: DetectionReport) -> None:
        with self._lock:
            self._status = "complete"
            self._report = report
            if self._total is not None:
                self._done = self._total

    def fail(self, code: str) -> None:
        with self._lock:
            self._status = "failed"
            self._failure = code

    def cancel(self) -> None:
        with self._lock:
            if self._status == "running":
                self._status = "cancelled"

    @property
    def finished(self) -> bool:
        with self._lock:
            return self._status != "running"

    def snapshot(self) -> RunStatus:
        with self._lock:
            return RunStatus(
                run_id=self.run_id,
                status=self._status,
                pairs_total=self._total,
                pairs_done=self._done,
                failure=self._failure,
                report=self._report,
            )


def failure_code(error: BaseException) -> str:
    """A fixed word for why a run failed. Never the exception's text."""
    if isinstance(error, IndexNotBuiltError):
        return "index_not_built"
    if isinstance(error, GrcError):
        return "detector_error"
    return "internal_error"


class ContradictionRunJobs:
    """Starts and remembers contradiction runs. At most one runs, through the shared slot."""

    def __init__(self, detector: Detector, slot: JobSlot) -> None:
        self._detector = detector
        self._slot = slot
        self._runs: dict[str, ContradictionRun] = {}
        self._active: tuple[ContradictionRun, asyncio.Task[None]] | None = None

    async def start(self, *, scope: str | None, cap: int) -> str:
        """Take the slot, check the index, begin. Refuses before any task exists.

        Raises RunInProgressError (slot taken) or IndexNotBuiltError (slot released again).
        """
        self._slot.begin()
        try:
            await self._detector.ensure_index()
            run = ContradictionRun(secrets.token_hex(16))
            task = asyncio.create_task(self._execute(run, scope, cap))
            self._active = (run, task)
            self._runs[run.run_id] = run
            self._evict()
        except BaseException:
            self._slot.end()  # a refusal never keeps the slot; `_execute` frees it later
            raise
        return run.run_id

    async def _execute(self, run: ContradictionRun, scope: str | None, cap: int) -> None:
        try:
            run.finish(await self._detector.detect(scope=scope, cap=cap, on_progress=run.progress))
        except asyncio.CancelledError:
            run.cancel()
            raise
        except Exception as error:  # the task boundary: keep a fixed code, never the message
            code = failure_code(error)
            _log.error("contradiction run failed: %s (%s)", code, type(error).__name__)
            run.fail(code)
        finally:
            self._slot.end()
            self._evict()

    def get(self, run_id: str) -> RunStatus:
        run = self._runs.get(run_id)
        if run is None:
            raise RunNotFoundError
        return run.snapshot()

    def _evict(self) -> None:
        """Keep the last `KEPT_RUNS` finished runs; a run that is still going is never dropped."""
        finished = [run_id for run_id, run in self._runs.items() if run.finished]
        for run_id in finished[: max(0, len(finished) - KEPT_RUNS)]:
            del self._runs[run_id]

    async def aclose(self) -> None:
        """Stop a run that is still going (the app is shutting down) and give the slot back."""
        active = self._active
        if active is None:
            return
        run, task = active
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if not run.finished:
            # The task was cancelled before it ever ran, so its `finally` never did.
            run.cancel()
            self._slot.end()
