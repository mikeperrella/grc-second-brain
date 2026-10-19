"""The contradiction route (ADR-019). Thin: every decision is made in `contradictions/`.

It sits behind the auth middleware that wraps the whole app, so it needs the token like every other
route.

- `POST /contradictions/run`   compare the numbers in the vault's company-policy text, ask the local
                               model one question per kept pair, and return the findings
- `POST /contradictions/runs`  the same run as start then poll (G40): takes the shared slot, starts
                               a task and answers 202 `{run_id}`; 409 `run_in_progress`, 409
                               `index_not_built`, 503 `vault_root_not_configured` as `run` does
- `GET  /contradictions/runs/{run_id}`  the run's status, its pair counts and, once complete, the
                               same response `run` gives; 404 `run_not_found` for an unknown or
                               evicted id (the last few finished runs are kept, in memory only)

**`/run` is one JSON response** (deviation M55-12): the request stays open for the whole run, which
is minutes when each pair takes tens of seconds on a CPU-only machine (speed unmeasured). That is
acceptable on loopback, and it stays for the existing tests and outside the host proxy (N7). The
start-then-poll pair is the one the window uses (M6.5): no request is held open for the whole run.
Findings carry verified quotes from the vault and the model's capped explanation, so they are
returned in the response body and never logged or stored. The request model has no url, token,
credential or host field, and an unknown field is refused without echoing its value.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from fastapi import Path as PathParam
from pydantic import BaseModel, ConfigDict, Field, field_validator

from grc_sidecar.contradictions.models import (
    CandidateReason,
    DetectionReport,
    Finding,
    FindingStatus,
    Flag,
    ModelUse,
    Relation,
)
from grc_sidecar.contradictions.pairs import DEFAULT_PAIR_CAP
from grc_sidecar.contradictions.run_jobs import RunStatus
from grc_sidecar.contradictions.runs import ContradictionRuns
from grc_sidecar.contradictions.verdict import PAIR_TEMPLATE_SHA256
from grc_sidecar.errors import ContradictionsNotConfiguredError

router = APIRouter()

MAX_SCOPE_CHARS = 260
MAX_CAP = 100  # a starting value, mine and unmeasured: more pairs than this is hours of model time
RUN_ID_PATTERN = r"^[0-9a-f]{8,64}$"


def get_contradictions(request: Request) -> ContradictionRuns:
    runs = getattr(request.app.state, "contradictions", None)
    if not isinstance(runs, ContradictionRuns):
        raise ContradictionsNotConfiguredError
    return runs


Runs = Annotated[ContradictionRuns, Depends(get_contradictions)]


class _Body(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RunRequest(_Body):
    """The only inputs: an optional vault-relative prefix and an optional cap on pairs."""

    scope: str | None = Field(default=None, min_length=1, max_length=MAX_SCOPE_CHARS)
    cap: int | None = Field(default=None, ge=1, le=MAX_CAP)

    @field_validator("scope")
    @classmethod
    def _scope_is_a_vault_relative_prefix(cls, value: str | None) -> str | None:
        """Only a string match on index paths is done, but refuse what is not a relative path."""
        if value is None:
            return None
        parts = value.split("/")
        if (
            "\\" in value
            or ":" in value
            or "\x00" in value
            or value.startswith("/")
            or ".." in parts
        ):
            raise ValueError("scope must be a vault-relative path prefix")
        return value


class VerdictOut(_Body):
    conflict: bool
    relation: Relation
    explanation: str  # the model's words: untrusted, capped, never logged
    model_confidence: float  # shown, decides nothing


class FindingOut(_Body):
    finding_id: str
    status: FindingStatus
    quote_a: str
    source_a: str
    quote_b: str
    source_b: str
    subject: str
    numbers: list[str]
    flags: list[Flag]
    reasons: list[CandidateReason]
    verdict: VerdictOut | None


class RunResponse(_Body):
    findings: list[FindingOut]
    model: str
    model_status: ModelUse
    template_sha256: str
    says: str  # what was and was not checked, on every response
    over_cap: int
    dropped: dict[str, int]
    verdict_counts: dict[str, dict[str, int]]
    no_verdict: int
    model_calls: int
    chunks_read: int
    excluded_chunks: int
    claims_found: int
    pairs_generated: int
    withheld_stale: int


def _finding_out(finding: Finding) -> FindingOut:
    verdict = finding.verdict
    return FindingOut(
        finding_id=finding.finding_id,
        status=finding.status,
        quote_a=finding.quote_a,
        source_a=finding.source_a,
        quote_b=finding.quote_b,
        source_b=finding.source_b,
        subject=finding.subject,
        numbers=list(finding.numbers),
        flags=list(finding.flags),
        reasons=list(finding.reasons),
        verdict=None
        if verdict is None
        else VerdictOut(
            conflict=verdict.conflict,
            relation=verdict.relation,
            explanation=verdict.explanation,
            model_confidence=verdict.model_confidence,
        ),
    )


def run_response(report: DetectionReport, model: str) -> RunResponse:
    return RunResponse(
        findings=[_finding_out(f) for f in report.findings],
        model=model,
        model_status=report.model_status,
        template_sha256=PAIR_TEMPLATE_SHA256,
        says=report.says,
        over_cap=report.over_cap,
        dropped=report.dropped,
        verdict_counts=report.verdict_counts,
        no_verdict=report.no_verdict,
        model_calls=report.model_calls,
        chunks_read=report.chunks_read,
        excluded_chunks=report.excluded_chunks,
        claims_found=report.claims_found,
        pairs_generated=report.pairs_generated,
        withheld_stale=report.withheld_stale,
    )


class StartResponse(_Body):
    run_id: str


class RunStatusResponse(_Body):
    run_id: str
    status: Literal["running", "complete", "failed", "cancelled"]
    pairs_total: int | None  # None until reading and verification are done
    pairs_done: int
    failure: str | None  # a fixed word, never exception text
    report: RunResponse | None  # set only when complete


def _status_response(status: RunStatus, model_name: str) -> RunStatusResponse:
    return RunStatusResponse(
        run_id=status.run_id,
        status=status.status,
        pairs_total=status.pairs_total,
        pairs_done=status.pairs_done,
        failure=status.failure,
        report=None if status.report is None else run_response(status.report, model_name),
    )


@router.post("/contradictions/run")
async def run_contradictions(body: RunRequest, runs: Runs) -> RunResponse:
    report = await runs.run(scope=body.scope, cap=body.cap or DEFAULT_PAIR_CAP)
    return run_response(report, runs.model_name)


@router.post("/contradictions/runs", status_code=202)
async def start_contradictions(body: RunRequest, runs: Runs) -> StartResponse:
    return StartResponse(
        run_id=await runs.start(scope=body.scope, cap=body.cap or DEFAULT_PAIR_CAP)
    )


RunId = Annotated[str, PathParam(pattern=RUN_ID_PATTERN)]


@router.get("/contradictions/runs/{run_id}")
async def contradictions_status(run_id: RunId, runs: Runs) -> RunStatusResponse:
    return _status_response(runs.get(run_id), runs.model_name)
