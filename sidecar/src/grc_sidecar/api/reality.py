"""The reality-check routes (ADR-016). Thin: every decision is made under `reality/`.

Behind the auth middleware that wraps the whole app, so they need the token like every other
route. There is no URL, token, credential or host field in any request: the check never connects
to a live system.

- `POST /reality/inspect`   read an export from the import folder and return counts only
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from grc_sidecar.errors import ImportRootNotConfiguredError
from grc_sidecar.reality.inspect import ExportInspection
from grc_sidecar.reality.runtime import RealityRuntime

router = APIRouter()

MAX_FILE_NAME_CHARS = 260


def get_reality(request: Request) -> RealityRuntime:
    runtime = getattr(request.app.state, "reality", None)
    if not isinstance(runtime, RealityRuntime):
        raise ImportRootNotConfiguredError
    return runtime


Reality = Annotated[RealityRuntime, Depends(get_reality)]


class InspectRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    file: str = Field(min_length=1, max_length=MAX_FILE_NAME_CHARS)


@router.post("/reality/inspect")
async def inspect(body: InspectRequest, reality: Reality) -> ExportInspection:
    return await reality.inspect(body.file)
