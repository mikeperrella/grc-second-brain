"""The one place where domain errors become HTTP responses (CLAUDE.md 7).

Bodies are fixed words plus configured model names, never exception text.
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from grc_sidecar.errors import (
    ConfigurationError,
    ExportRefusedError,
    ImportRootNotConfiguredError,
    IndexNotBuiltError,
    ModelsUnavailableError,
    PathNotAllowedError,
    PathNotFoundError,
    QuestionMappingError,
    QuestionnaireNotConfiguredError,
    RetrievalUnavailableError,
    RunInProgressError,
    RunNotFoundError,
    VaultChangedError,
    WorkbookRefusedError,
)

HTTP_BAD_REQUEST = 400
HTTP_NOT_FOUND = 404
HTTP_CONFLICT = 409
HTTP_UNPROCESSABLE = 422
HTTP_SERVICE_UNAVAILABLE = 503

# Domain error -> (status, fixed error word). The body never holds exception text.
_FIXED: dict[type[Exception], tuple[int, str]] = {
    QuestionnaireNotConfiguredError: (
        HTTP_SERVICE_UNAVAILABLE,
        "questionnaire_root_not_configured",
    ),
    ImportRootNotConfiguredError: (HTTP_SERVICE_UNAVAILABLE, "import_root_not_configured"),
    ConfigurationError: (HTTP_SERVICE_UNAVAILABLE, "questionnaire_root_unusable"),
    RunInProgressError: (HTTP_CONFLICT, "run_in_progress"),
    RunNotFoundError: (HTTP_NOT_FOUND, "run_not_found"),
    VaultChangedError: (HTTP_CONFLICT, "vault_changed_reindex"),
    IndexNotBuiltError: (HTTP_CONFLICT, "index_not_built"),
    RetrievalUnavailableError: (HTTP_SERVICE_UNAVAILABLE, "retrieval_unavailable"),
    PathNotAllowedError: (HTTP_BAD_REQUEST, "path_not_allowed"),
    PathNotFoundError: (HTTP_NOT_FOUND, "file_not_found"),
}


async def _models_unavailable(_request: Request, exc: Exception) -> JSONResponse:
    missing = list(exc.missing) if isinstance(exc, ModelsUnavailableError) else []
    return JSONResponse(
        {"error": "models_unavailable", "models_missing": missing},
        status_code=HTTP_SERVICE_UNAVAILABLE,
    )


async def _fixed(_request: Request, exc: Exception) -> JSONResponse:
    status, word = _FIXED[type(exc)]
    return JSONResponse({"error": word}, status_code=status)


async def _workbook_refused(_request: Request, exc: Exception) -> JSONResponse:
    code = exc.code if isinstance(exc, WorkbookRefusedError) else "unknown"
    return JSONResponse({"error": "workbook_refused", "code": code}, status_code=HTTP_UNPROCESSABLE)


async def _export_refused(_request: Request, exc: Exception) -> JSONResponse:
    """`code` is a fixed word and `column` one of ALIBI's own required names, never file text."""
    body: dict[str, str] = {"error": "export_refused", "code": "unknown"}
    if isinstance(exc, ExportRefusedError):
        body["code"] = exc.code
        if exc.column is not None:
            body["column"] = exc.column
    return JSONResponse(body, status_code=HTTP_UNPROCESSABLE)


async def _mapping_refused(_request: Request, exc: Exception) -> JSONResponse:
    code = exc.code if isinstance(exc, QuestionMappingError) else "unknown"
    return JSONResponse({"error": "mapping_refused", "code": code}, status_code=HTTP_UNPROCESSABLE)


async def _invalid_request(_request: Request, exc: Exception) -> JSONResponse:
    """A malformed request. Names the fields, never echoes the values (a path may be in one)."""
    fields: list[str] = []
    if isinstance(exc, RequestValidationError):
        fields = sorted({".".join(str(part) for part in e["loc"]) for e in exc.errors()})
    return JSONResponse(
        {"error": "invalid_request", "fields": fields}, status_code=HTTP_UNPROCESSABLE
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ModelsUnavailableError, _models_unavailable)
    for error_type in _FIXED:
        app.add_exception_handler(error_type, _fixed)
    app.add_exception_handler(WorkbookRefusedError, _workbook_refused)
    app.add_exception_handler(ExportRefusedError, _export_refused)
    app.add_exception_handler(QuestionMappingError, _mapping_refused)
    app.add_exception_handler(RequestValidationError, _invalid_request)
