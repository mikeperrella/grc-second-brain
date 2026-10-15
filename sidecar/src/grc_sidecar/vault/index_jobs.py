"""Building the vault index as a job you start and then poll (ADR-021 decision 6).

Indexing a vault can take minutes, so `start` answers at once with a job id and the work runs in the
background; `get` returns the counts so far. Both take only fixed words and integers: never a path,
a file name or any text from the vault.

**One heavy job at a time.** A job holds the shared `JobSlot`, so it refuses (409) while a
questionnaire run or a contradiction run is going and the other way round. It also passes the model
gate for the embedding model first (503 naming what is missing), and releases the slot on a refusal.

**Threads.** The indexer's progress callback may be called from a thread other than the one that
reads the job (a future change could move the scan to a worker). Every counter is therefore changed
and read under one lock, and `snapshot()` copies them all together, so a reader never sees a half
update.

The last `KEPT_JOBS` jobs are kept in memory, like questionnaire runs; an evicted or unknown id is
`RunNotFoundError` (404).
"""

import asyncio
import logging
import threading
import uuid
from dataclasses import dataclass
from typing import Literal, Protocol

from grc_sidecar.errors import (
    EmbeddingUnavailableError,
    GrcError,
    IndexStoreError,
    ModelMissingError,
    ModelsUnavailableError,
    RunNotFoundError,
)
from grc_sidecar.jobs import JobSlot
from grc_sidecar.vault.indexer import ProgressCallback, SyncReport

_log = logging.getLogger(__name__)

KEPT_JOBS = 20  # finished jobs kept in memory, as for questionnaire runs
IndexJobState = Literal["running", "complete", "incomplete", "failed"]


class Syncer(Protocol):
    async def sync(self, on_progress: ProgressCallback | None = None) -> SyncReport: ...


class ModelRequirement(Protocol):
    async def require(self, *models: str) -> None: ...


@dataclass(frozen=True)
class IndexJobStatus:
    """A consistent copy of a job's counters. Integers and fixed words only."""

    job_id: str
    status: IndexJobState
    files_done: int
    files_total: int
    files_indexed: int
    files_unchanged: int
    files_removed: int
    files_failed: int
    chunks_embedded: int
    failure: str | None


class IndexJob:
    """One job's state. Thread-safe: every read and write goes through the lock."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self._lock = threading.Lock()
        self._status: IndexJobState = "running"
        self._done = 0
        self._total = 0
        self._indexed = 0
        self._unchanged = 0
        self._removed = 0
        self._failed = 0
        self._chunks = 0
        self._failure: str | None = None

    def progress(self, done: int, total: int) -> None:
        """The indexer's callback. Safe to call from any thread."""
        with self._lock:
            self._done = done
            self._total = total

    def finish(self, report: SyncReport) -> None:
        with self._lock:
            self._status = "complete" if report.complete else "incomplete"
            self._done = self._total = report.documents_seen
            self._indexed = len(report.indexed)
            self._unchanged = report.unchanged
            self._removed = len(report.removed)
            self._failed = len(report.failed)
            self._chunks = report.chunks_embedded

    def fail(self, code: str) -> None:
        with self._lock:
            self._status = "failed"
            self._failure = code

    @property
    def finished(self) -> bool:
        with self._lock:
            return self._status != "running"

    def snapshot(self) -> IndexJobStatus:
        with self._lock:
            return IndexJobStatus(
                job_id=self.job_id,
                status=self._status,
                files_done=self._done,
                files_total=self._total,
                files_indexed=self._indexed,
                files_unchanged=self._unchanged,
                files_removed=self._removed,
                files_failed=self._failed,
                chunks_embedded=self._chunks,
                failure=self._failure,
            )


def failure_code(error: BaseException) -> str:
    """A fixed word for why a job failed. Never the exception's text."""
    if isinstance(error, ModelMissingError | ModelsUnavailableError):
        return "models_unavailable"
    if isinstance(error, EmbeddingUnavailableError):
        return "embedding_unavailable"
    if isinstance(error, IndexStoreError):
        return "index_store_error"
    if isinstance(error, GrcError):
        return "index_error"
    return "internal_error"


class IndexJobs:
    """Starts and remembers index jobs. At most one runs, through the shared slot."""

    def __init__(
        self, syncer: Syncer, slot: JobSlot, gate: ModelRequirement, embedding_model: str
    ) -> None:
        self._syncer = syncer
        self._slot = slot
        self._gate = gate
        self._embedding_model = embedding_model
        self._jobs: dict[str, IndexJob] = {}
        self._active: asyncio.Task[None] | None = None

    async def start(self) -> str:
        """Take the slot, pass the model gate, begin. Refuses before any work.

        Raises RunInProgressError (slot taken) or ModelsUnavailableError (slot released again).
        """
        self._slot.begin()
        try:
            await self._gate.require(self._embedding_model)
            job = IndexJob(uuid.uuid4().hex)
            self._remember(job)
            self._active = asyncio.create_task(self._execute(job))
        except BaseException:
            self._slot.end()  # a refusal never keeps the slot; `_execute` frees it later
            raise
        return job.job_id

    async def _execute(self, job: IndexJob) -> None:
        try:
            job.finish(await self._syncer.sync(job.progress))
        except asyncio.CancelledError:
            job.fail("cancelled")
            raise
        except Exception as error:  # the task boundary: keep a fixed code, never the message
            code = failure_code(error)
            _log.error("index job failed: %s (%s)", code, type(error).__name__)
            job.fail(code)
        finally:
            self._slot.end()

    def get(self, job_id: str) -> IndexJobStatus:
        job = self._jobs.get(job_id)
        if job is None:
            raise RunNotFoundError
        return job.snapshot()

    def _remember(self, job: IndexJob) -> None:
        self._jobs[job.job_id] = job
        while len(self._jobs) > KEPT_JOBS:
            oldest = next(iter(self._jobs))
            if not self._jobs[oldest].finished:
                break
            del self._jobs[oldest]

    async def aclose(self) -> None:
        """Stop a job that is still going (the app is shutting down)."""
        task = self._active
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
