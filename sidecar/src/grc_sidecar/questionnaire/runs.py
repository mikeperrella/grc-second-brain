"""Running a questionnaire: one run at a time, progress as events, one file at the end (ADR-014).

A run reads a workbook, answers every question through the answer service, and writes the answers
file. This module is the orchestration; every decision about a single answer is made elsewhere.

**Refusals come before any work.** `start` refuses, with a typed error and no file written, if a
run is already going, the file or the mapping is refused, a required model is not confirmed, the
generation model's digest cannot be read, the index is not built, or any vault file changed after
it was indexed (`VaultChangedError`).

**An Ollama failure part-way stops the asking, not the file** (ADR-013, "Ollama fails partway"):
when one answer reports the model unavailable, every remaining question gets a blank
`model_unavailable` result, the file is still written, and the summary says `complete: false`.
This includes a generation read timeout (`limits.READ_TIMEOUT_SECONDS`): it fails closed.

**Events carry codes, never text.** Each event holds counts, indexes, status and reason codes and
the run id (hex), never a question, an answer, a quote or a path. A test enforces that.

The workbook, the mapping, the files and the SQLite work all run off the event loop
(`asyncio.to_thread` and the repository's own worker), so `/health` keeps answering during a run.
"""

import asyncio
import logging
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Literal, Protocol

from grc_sidecar.errors import (
    GrcError,
    IndexNotBuiltError,
    ModelsUnavailableError,
    QuestionMappingError,
    RetrievalUnavailableError,
    RunInProgressError,
    RunNotFoundError,
    VaultChangedError,
    WorkbookWriteError,
)
from grc_sidecar.llm.ollama_embeddings import ModelEntry
from grc_sidecar.questionnaire.loader import LoadedWorkbook, load_questionnaire, read_input_bytes
from grc_sidecar.questionnaire.mapping import (
    QuestionMapping,
    QuestionSet,
    extract_questions,
    suggest_mapping,
)
from grc_sidecar.questionnaire.models import AnswerResult
from grc_sidecar.questionnaire.package import PartInventory
from grc_sidecar.questionnaire.service import AnswerOutcome
from grc_sidecar.questionnaire.status import blank_result
from grc_sidecar.questionnaire.writer import ExportResult, write_answers
from grc_sidecar.vault.clock import Clock

_log = logging.getLogger(__name__)

RunStatus = Literal["running", "complete", "stopped", "failed"]
KEPT_RUNS = 20  # finished runs kept in memory for `GET /questionnaire/runs/{id}`


class QuestionAnswerer(Protocol):
    async def answer(self, question: str) -> AnswerOutcome: ...


class VaultChecker(Protocol):
    async def vault_withheld(self) -> tuple[str, ...]: ...


class ModelReader(Protocol):
    async def read_model_entry(self) -> ModelEntry: ...


class ModelRequirement(Protocol):
    async def require(self, *models: str) -> None: ...


@dataclass(frozen=True)
class RunDependencies:
    """Everything a run needs, passed in so tests can fake each part."""

    questionnaire_root: Path
    export_root: Path
    answerer: QuestionAnswerer
    vault: VaultChecker
    model_reader: ModelReader
    gate: ModelRequirement
    required_models: tuple[str, ...]
    model_name: str  # the generation model, as configured
    clock: Clock
    current_sha256: Callable[[str], str | None]
    template_sha256: str
    new_run_id: Callable[[], str] = field(default=lambda: uuid.uuid4().hex)


@dataclass(frozen=True)
class RunEvent:
    """One event. `data` holds only ints, status and reason codes, a bool and the run id."""

    name: Literal["started", "progress", "done", "failed"]
    data: dict[str, object]


@dataclass(frozen=True)
class RunSummary:
    """What a finished run reports (no question, answer, quote or path text)."""

    model: str
    digest_before: str | None
    digest_after: str | None
    template_sha256: str
    input_sha256: str
    complete: bool
    counts: dict[str, int]
    reason_counts: dict[str, int]
    file_name: str | None
    rows_written: int
    dropped_parts: tuple[tuple[str, int, int], ...]
    warnings: tuple[str, ...]
    seconds: float


@dataclass
class RunState:
    run_id: str
    total: int
    status: RunStatus = "running"
    completed: int = 0
    events: list[RunEvent] = field(default_factory=list)
    summary: RunSummary | None = None
    failure: str | None = None
    input_sha256: str = ""

    @property
    def finished(self) -> bool:
        return self.status != "running"


@dataclass(frozen=True)
class Inspection:
    """What `inspect` learns about a workbook without answering anything."""

    sheets: tuple[tuple[str, int, int], ...]  # name, rows, columns
    suggestion: QuestionMapping | None
    macro_enabled: bool
    inventory: PartInventory
    warnings: tuple[str, ...]
    input_sha256: str
    uncompressed_bytes: int


def failure_code(error: BaseException) -> str:
    """A fixed word for why a run failed after it started. Never the exception's text."""
    if isinstance(error, IndexNotBuiltError):
        return "index_not_built"
    if isinstance(error, RetrievalUnavailableError):
        return "retrieval_unavailable"
    if isinstance(error, WorkbookWriteError):
        return f"write_{error.code}"
    if isinstance(error, GrcError):
        return "run_error"
    return "internal_error"


class QuestionnaireRuns:
    """Owns the runs. One at a time; the last few are kept for status and events."""

    def __init__(self, deps: RunDependencies) -> None:
        self._deps = deps
        self._runs: dict[str, RunState] = {}
        self._active: asyncio.Task[None] | None = None
        self._starting = asyncio.Lock()
        self._changed = asyncio.Condition()

    # ----- before a run ------------------------------------------------------------------------

    async def inspect(self, file: str) -> Inspection:
        """Read and scan a workbook and suggest a mapping. Writes nothing, answers nothing."""
        data = await asyncio.to_thread(read_input_bytes, file, self._deps.questionnaire_root)
        loaded = await asyncio.to_thread(load_questionnaire, data)
        try:
            suggestion = await asyncio.to_thread(suggest_mapping, loaded.workbook)
            sheets = tuple((s.title, s.max_row, s.max_column) for s in loaded.workbook.worksheets)
            scan = loaded.scan
            return Inspection(
                sheets=sheets,
                suggestion=suggestion,
                macro_enabled=scan.macro_enabled,
                inventory=scan.inventory,
                warnings=loaded.warnings,
                input_sha256=scan.sha256,
                uncompressed_bytes=scan.uncompressed_bytes,
            )
        finally:
            loaded.close()

    async def start(self, file: str, mapping: QuestionMapping) -> RunState:
        """Check everything that can be checked, then begin. Raises before any work on a refusal."""
        async with self._starting:
            if self._active is not None and not self._active.done():
                raise RunInProgressError
            data = await asyncio.to_thread(read_input_bytes, file, self._deps.questionnaire_root)
            loaded = await asyncio.to_thread(load_questionnaire, data)
            try:
                questions = await asyncio.to_thread(extract_questions, loaded.workbook, mapping)
                if not questions.questions:
                    raise QuestionMappingError("no_mapping")
                digest_before = await self._preflight()
            except BaseException:
                loaded.close()
                raise
            state = RunState(
                run_id=self._deps.new_run_id(),
                total=len(questions.questions),
                input_sha256=loaded.scan.sha256,
            )
            self._remember(state)
            await self._emit(
                state, RunEvent("started", {"run_id": state.run_id, "total": state.total})
            )
            self._active = asyncio.create_task(
                self._execute(state, loaded, mapping, questions, file, digest_before)
            )
            return state

    async def _preflight(self) -> str | None:
        """Models confirmed, the generation model's digest readable, the index current."""
        await self._deps.gate.require(*self._deps.required_models)
        readable, digest = await self._read_digest()
        if not readable:
            raise ModelsUnavailableError((self._deps.model_name,))
        withheld = await self._deps.vault.vault_withheld()
        if withheld:
            raise VaultChangedError
        return digest

    async def _read_digest(self) -> tuple[bool, str | None]:
        """(readable, digest): a fresh `GET /api/tags` for the generation model's entry."""
        try:
            entry = await self._deps.model_reader.read_model_entry()
        except GrcError:
            return False, None
        return True, entry.digest

    # ----- the run -----------------------------------------------------------------------------

    async def _execute(
        self,
        state: RunState,
        loaded: LoadedWorkbook,
        mapping: QuestionMapping,
        questions: QuestionSet,
        file: str,
        digest_before: str | None,
    ) -> None:
        started = self._deps.clock.monotonic()
        try:
            results = await self._answer_all(state, questions)
            digest_after = await self._digest_now()
            export = await asyncio.to_thread(
                write_answers,
                loaded,
                mapping,
                questions.questions,
                results,
                export_root=self._deps.export_root,
                source_name=Path(file.replace("\\", "/")).name,
                run_id=state.run_id,
                read_input=lambda: read_input_bytes(file, self._deps.questionnaire_root),
                current_sha256=self._deps.current_sha256,
            )
            complete = not any("model_unavailable" in r.reasons for r in results.values())
            state.summary = self._summary(
                state, results, export, digest_before, digest_after, complete, started
            )
            state.status = "complete" if complete else "stopped"
            await self._emit(
                state,
                RunEvent(
                    "done",
                    {
                        "complete": complete,
                        "total": state.total,
                        "counts": dict(state.summary.counts),
                    },
                ),
            )
        except asyncio.CancelledError:
            state.status, state.failure = "failed", "cancelled"
            await self._emit(state, RunEvent("failed", {"code": "cancelled"}))
            raise
        except Exception as error:  # the task boundary: record a fixed code, never the message
            code = failure_code(error)
            _log.error("run failed: %s (%s)", code, type(error).__name__)
            state.status, state.failure = "failed", code
            await self._emit(state, RunEvent("failed", {"code": code}))
        finally:
            loaded.close()

    async def _answer_all(self, state: RunState, questions: QuestionSet) -> dict[int, AnswerResult]:
        results: dict[int, AnswerResult] = {}
        stopped = False
        for index, question in enumerate(questions.questions, start=1):
            if stopped:
                result = blank_result("model_unavailable")
            else:
                outcome = await self._deps.answerer.answer(question.text)
                result = outcome.result
                stopped = outcome.model_unavailable
            results[question.row] = result
            state.completed = index
            await self._emit(
                state,
                RunEvent(
                    "progress",
                    {
                        "index": index,
                        "total": state.total,
                        "status": result.status,
                        "reasons": list(result.reasons),
                    },
                ),
            )
        return results

    async def _digest_now(self) -> str | None:
        _, digest = await self._read_digest()
        return digest

    def _summary(
        self,
        state: RunState,
        results: dict[int, AnswerResult],
        export: ExportResult,
        digest_before: str | None,
        digest_after: str | None,
        complete: bool,
        started: float,
    ) -> RunSummary:
        counts = Counter(r.status for r in results.values())
        reasons = Counter(reason for r in results.values() for reason in r.reasons)
        return RunSummary(
            model=self._deps.model_name,
            digest_before=digest_before,
            digest_after=digest_after,
            template_sha256=self._deps.template_sha256,
            input_sha256=state.input_sha256,
            complete=complete,
            counts={
                status: counts.get(status, 0) for status in ("SUPPORTED", "PARTIAL", "NEEDS_REVIEW")
            },
            reason_counts=dict(sorted(reasons.items())),
            file_name=export.file_name,
            rows_written=export.rows_written,
            dropped_parts=tuple((c.category, c.before, c.after) for c in export.dropped),
            warnings=export.warnings,
            seconds=round(self._deps.clock.monotonic() - started, 1),
        )

    # ----- events and status -------------------------------------------------------------------

    async def _emit(self, state: RunState, event: RunEvent) -> None:
        async with self._changed:
            state.events.append(event)
            self._changed.notify_all()

    def _remember(self, state: RunState) -> None:
        self._runs[state.run_id] = state
        while len(self._runs) > KEPT_RUNS:
            oldest = next(iter(self._runs))
            if not self._runs[oldest].finished:
                break
            del self._runs[oldest]

    def get(self, run_id: str) -> RunState:
        state = self._runs.get(run_id)
        if state is None:
            raise RunNotFoundError
        return state

    async def events(self, run_id: str) -> AsyncIterator[RunEvent]:
        """Every event of a run from the first, then each new one, ending after the last."""
        state = self.get(run_id)
        sent = 0
        while True:
            async with self._changed:
                await self._changed.wait_for(partial(_has_news, state, sent))
            while sent < len(state.events):
                yield state.events[sent]
                sent += 1
            if state.finished and sent >= len(state.events):
                return

    async def aclose(self) -> None:
        """Stop a run that is still going (the app is shutting down)."""
        task = self._active
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def _has_news(state: RunState, sent: int) -> bool:
    """True when a stream that has sent `sent` events has something to send or to end on."""
    return len(state.events) > sent or state.finished
