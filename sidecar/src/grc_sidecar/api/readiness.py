"""Which models the app needs and which are installed (CLAUDE.md 3.7, ADR-011).

Separate from `/health` on purpose: `/health` is the host's liveness contract (exactly
`{"status":"ok"}`, parsed by Rust and Zod), and a degraded model state does not mean the sidecar
is unhealthy. Thin by design: the gate does the work.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from grc_sidecar.api.deps import get_model_gate
from grc_sidecar.llm.model_gate import ModelGate, ModelStatus

router = APIRouter()


@router.get("/readiness")
async def readiness(gate: Annotated[ModelGate, Depends(get_model_gate)]) -> ModelStatus:
    # `refresh` re-checks at most once per minimum interval, so polling this cannot hammer Ollama,
    # and a user who has just installed a model sees it without restarting the app.
    return await gate.refresh()
