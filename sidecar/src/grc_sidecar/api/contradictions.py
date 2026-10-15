"""The contradiction route (ADR-019). Thin: every decision is made in `contradictions/`.

It sits behind the auth middleware that wraps the whole app, so it needs the token like every other
route.

- `POST /contradictions/run`   compare the numbers in the vault's company-policy text, ask the local
                               model one question per kept pair, and return the findings

**One JSON response** (deviation M55-12): the request stays open for the whole run, which is minutes
when each pair takes tens of seconds on a CPU-only machine (speed unmeasured). That is acceptable
on loopback; the Rust host proxy of M6 will need a timeout and probably a run-id design (G40).
Findings carry verified quotes from the vault and the model's capped explanation, so they are
returned in the response body and never logged. The request model has no url, token, credential or
host field, and an unknown field is refused without echoing its value.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
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
from grc_sidecar.contradictions.runs import ContradictionRuns
from grc_sidecar.contradictions.verdict import PAIR_TEMPLATE_SHA256
from grc_sidecar.errors import ContradictionsNotConfiguredError

router = APIRouter()

MAX_SCOPE_CHARS = 260
MAX_CAP = 100  # a starting value, mine and unmeasured: more pairs than this is hours of model time


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


@router.post("/contradictions/run")
async def run_contradictions(body: RunRequest, runs: Runs) -> RunResponse:
    report = await runs.run(scope=body.scope, cap=body.cap or DEFAULT_PAIR_CAP)
    return run_response(report, runs.model_name)
