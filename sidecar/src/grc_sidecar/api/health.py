"""Liveness endpoint. Thin by design: no business logic and no system details."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

router = APIRouter()


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok"] = "ok"


@router.get("/health")
async def health() -> HealthResponse:
    return HealthResponse()
