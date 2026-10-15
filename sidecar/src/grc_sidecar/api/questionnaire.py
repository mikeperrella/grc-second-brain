"""The questionnaire routes (ADR-014). Thin: every decision is made in `questionnaire/runs.py`.

All of them sit behind the auth middleware that wraps the whole app, so they need the token like
every other route, the event stream included. Bodies and events carry codes and counts, never a
question, an answer, a quote or a path.

- `POST /questionnaire/draft`              the rows of a finished draft, joined with its run record
                                           (read-only; verdicts and citations come from the record)
- `POST /questionnaire/inspect`            read and scan a workbook, suggest a mapping
- `POST /questionnaire/runs`               start a run (202), or refuse with a typed error
- `GET  /questionnaire/runs/{id}`          the run's status and, once finished, its summary
- `GET  /questionnaire/runs/{id}/events`   server-sent events: started, progress, done or failed
"""

import json
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from grc_sidecar.api.ledger import Ledger
from grc_sidecar.errors import QuestionnaireNotConfiguredError
from grc_sidecar.ledger.draft import DraftRows
from grc_sidecar.questionnaire.mapping import QuestionMapping
from grc_sidecar.questionnaire.runs import QuestionnaireRuns, RunEvent, RunState, RunSummary

router = APIRouter()

RUN_ID_PATTERN = r"^[0-9a-f]{8,64}$"
MAX_FILE_NAME_CHARS = 260


def get_runs(request: Request) -> QuestionnaireRuns:
    runs = getattr(request.app.state, "questionnaire", None)
    if not isinstance(runs, QuestionnaireRuns):
        raise QuestionnaireNotConfiguredError
    return runs


Runs = Annotated[QuestionnaireRuns, Depends(get_runs)]
RunId = Annotated[str, Path(pattern=RUN_ID_PATTERN)]


class _Body(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class InspectRequest(_Body):
    file: str = Field(min_length=1, max_length=MAX_FILE_NAME_CHARS)


class StartRequest(_Body):
    file: str = Field(min_length=1, max_length=MAX_FILE_NAME_CHARS)
    mapping: QuestionMapping


class SheetInfo(_Body):
    name: str
    rows: int
    columns: int


class InspectResponse(_Body):
    sheets: list[SheetInfo]
    suggestion: QuestionMapping | None
    macro_enabled: bool
    parts: dict[str, int]
    warnings: list[str]
    sha256: str


class StartResponse(_Body):
    run_id: str
    total: int


class SummaryResponse(_Body):
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
    dropped_parts: list[tuple[str, int, int]]
    warnings: list[str]
    seconds: float


class StatusResponse(_Body):
    run_id: str
    status: str
    total: int
    completed: int
    failure: str | None
    summary: SummaryResponse | None


class DraftRequest(_Body):
    file: str = Field(min_length=1, max_length=MAX_FILE_NAME_CHARS)


class CitationOut(_Body):
    source_path: str
    start_line: int
    end_line: int
    quote: str


class DraftRowOut(_Body):
    row: int
    question_text: str  # the workbook's question cell, hash-checked against the run record
    status: str  # the run record's, never the Status cell
    reasons: list[str]  # the run record's, never the Reason cell
    answer_text: str | None  # the Answer cell, untrusted text
    citation: CitationOut | None  # the run record's, never the Evidence cell
    status_edited: bool
    answer_edited: bool
    evidence_edited: bool


class DraftResponse(_Body):
    model: str
    file_name: str
    complete: bool
    question_count: int
    rows: list[DraftRowOut]


def _draft_response(draft: DraftRows) -> DraftResponse:
    return DraftResponse(
        model=draft.model,
        file_name=draft.file_name,
        complete=draft.complete,
        question_count=draft.question_count,
        rows=[
            DraftRowOut(
                row=r.row,
                question_text=r.question_text,
                status=r.status,
                reasons=list(r.reasons),
                answer_text=r.answer_text,
                citation=None if r.citation is None else CitationOut(**asdict(r.citation)),
                status_edited=r.status_edited,
                answer_edited=r.answer_edited,
                evidence_edited=r.evidence_edited,
            )
            for r in draft.rows
        ],
    )


def _summary_response(summary: RunSummary) -> SummaryResponse:
    return SummaryResponse(
        model=summary.model,
        digest_before=summary.digest_before,
        digest_after=summary.digest_after,
        template_sha256=summary.template_sha256,
        input_sha256=summary.input_sha256,
        complete=summary.complete,
        counts=summary.counts,
        reason_counts=summary.reason_counts,
        file_name=summary.file_name,
        rows_written=summary.rows_written,
        dropped_parts=list(summary.dropped_parts),
        warnings=list(summary.warnings),
        seconds=summary.seconds,
    )


def _status_response(state: RunState) -> StatusResponse:
    return StatusResponse(
        run_id=state.run_id,
        status=state.status,
        total=state.total,
        completed=state.completed,
        failure=state.failure,
        summary=None if state.summary is None else _summary_response(state.summary),
    )


def sse_frame(event: RunEvent) -> str:
    """One server-sent event. The data is JSON of codes and counts only."""
    return f"event: {event.name}\ndata: {json.dumps(event.data, separators=(',', ':'))}\n\n"


@router.post("/questionnaire/inspect")
async def inspect(body: InspectRequest, runs: Runs) -> InspectResponse:
    found = await runs.inspect(body.file)
    return InspectResponse(
        sheets=[SheetInfo(name=n, rows=r, columns=c) for n, r, c in found.sheets],
        suggestion=found.suggestion,
        macro_enabled=found.macro_enabled,
        parts=found.inventory.as_dict(),
        warnings=list(found.warnings),
        sha256=found.input_sha256,
    )


@router.post("/questionnaire/runs", status_code=202)
async def start_run(body: StartRequest, runs: Runs) -> StartResponse:
    state = await runs.start(body.file, body.mapping)
    return StartResponse(run_id=state.run_id, total=state.total)


@router.post("/questionnaire/draft")
async def draft_rows(body: DraftRequest, ledger: Ledger) -> DraftResponse:
    """The rows of a finished draft, verdicts from the run record (read-only, ADR-021)."""
    return _draft_response(await ledger.draft_rows(body.file))


@router.get("/questionnaire/runs/{run_id}")
async def run_status(run_id: RunId, runs: Runs) -> StatusResponse:
    return _status_response(runs.get(run_id))


@router.get("/questionnaire/runs/{run_id}/events")
async def run_events(run_id: RunId, runs: Runs) -> StreamingResponse:
    runs.get(run_id)  # an unknown run is a 404 now, not an empty stream

    async def frames() -> AsyncIterator[str]:
        async for event in runs.events(run_id):
            yield sse_frame(event)

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
