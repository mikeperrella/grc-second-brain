"""The one place where domain errors become HTTP responses (CLAUDE.md 7).

Bodies are fixed words plus configured model names, never exception text.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from grc_sidecar.errors import ModelsUnavailableError

HTTP_SERVICE_UNAVAILABLE = 503


async def _models_unavailable(_request: Request, exc: Exception) -> JSONResponse:
    missing = list(exc.missing) if isinstance(exc, ModelsUnavailableError) else []
    return JSONResponse(
        {"error": "models_unavailable", "models_missing": missing},
        status_code=HTTP_SERVICE_UNAVAILABLE,
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ModelsUnavailableError, _models_unavailable)
