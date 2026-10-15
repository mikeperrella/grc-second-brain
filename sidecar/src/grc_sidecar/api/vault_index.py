"""The vault index routes (ADR-021 decision 6). Thin: the decisions are in `vault/index_jobs.py`.

Behind the auth middleware like every route. Bodies carry fixed words and integers only, never a
path, a file name or text from the vault.

- `POST /vault/index`           start an index job (202 `{job_id}`), or refuse: 409
                                `run_in_progress`, 503 `models_unavailable` (naming the missing
                                models), 503 `vault_root_not_configured`
- `GET  /vault/index/{job_id}`  the job's counts so far; 404 for an unknown or evicted id
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, ConfigDict

from grc_sidecar.errors import VaultRootNotConfiguredError
from grc_sidecar.vault.index_jobs import IndexJobs, IndexJobStatus

router = APIRouter()

JOB_ID_PATTERN = r"^[0-9a-f]{8,64}$"


def get_index_jobs(request: Request) -> IndexJobs:
    jobs = getattr(request.app.state, "index_jobs", None)
    if not isinstance(jobs, IndexJobs):
        raise VaultRootNotConfiguredError
    return jobs


Jobs = Annotated[IndexJobs, Depends(get_index_jobs)]
JobId = Annotated[str, Path(pattern=JOB_ID_PATTERN)]


class _Body(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class StartRequest(_Body):
    """No fields: the vault and index folders are the sidecar's settings, not the caller's."""


class StartResponse(_Body):
    job_id: str


class StatusResponse(_Body):
    job_id: str
    status: Literal["running", "complete", "incomplete", "failed"]
    files_done: int
    files_total: int
    files_indexed: int
    files_unchanged: int
    files_removed: int
    files_failed: int
    chunks_embedded: int
    failure: str | None


def _status_response(status: IndexJobStatus) -> StatusResponse:
    return StatusResponse(
        job_id=status.job_id,
        status=status.status,
        files_done=status.files_done,
        files_total=status.files_total,
        files_indexed=status.files_indexed,
        files_unchanged=status.files_unchanged,
        files_removed=status.files_removed,
        files_failed=status.files_failed,
        chunks_embedded=status.chunks_embedded,
        failure=status.failure,
    )


@router.post("/vault/index", status_code=202)
async def vault_index_start(jobs: Jobs, _body: StartRequest | None = None) -> StartResponse:
    # An empty request body is fine; a body with any field is refused (extra="forbid").
    return StartResponse(job_id=await jobs.start())


@router.get("/vault/index/{job_id}")
async def vault_index_status(job_id: JobId, jobs: Jobs) -> StatusResponse:
    return _status_response(jobs.get(job_id))
