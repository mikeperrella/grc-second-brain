"""The one place where domain errors become HTTP responses (CLAUDE.md 7).

Bodies are fixed words plus configured model names, never exception text.
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from grc_sidecar.errors import (
    AuthControlsError,
    BlastRadiusRefusedError,
    CheckRefusedError,
    ConfigurationError,
    ContradictionsNotConfiguredError,
    ControlMapTooLargeError,
    DraftRefusedError,
    ExportRefusedError,
    ExportRootNotConfiguredError,
    GraphRefusedError,
    ImportRefusedError,
    ImportRootNotConfiguredError,
    IndexNotBuiltError,
    LedgerRefusedError,
    LedgerRootNotConfiguredError,
    LedgerSchemaMismatchError,
    LedgerStoreError,
    ModelsUnavailableError,
    PathNotAllowedError,
    PathNotFoundError,
    QuestionMappingError,
    QuestionnaireNotConfiguredError,
    RecallRefusedError,
    RetrievalUnavailableError,
    RunInProgressError,
    RunNotFoundError,
    VaultChangedError,
    VaultRootNotConfiguredError,
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
    ExportRootNotConfiguredError: (HTTP_SERVICE_UNAVAILABLE, "export_root_not_configured"),
    VaultRootNotConfiguredError: (HTTP_SERVICE_UNAVAILABLE, "vault_root_not_configured"),
    ContradictionsNotConfiguredError: (HTTP_SERVICE_UNAVAILABLE, "vault_root_not_configured"),
    LedgerRootNotConfiguredError: (HTTP_SERVICE_UNAVAILABLE, "ledger_root_not_configured"),
    LedgerSchemaMismatchError: (HTTP_SERVICE_UNAVAILABLE, "ledger_unusable"),
    LedgerStoreError: (HTTP_SERVICE_UNAVAILABLE, "ledger_store_error"),
    ConfigurationError: (HTTP_SERVICE_UNAVAILABLE, "questionnaire_root_unusable"),
    RunInProgressError: (HTTP_CONFLICT, "run_in_progress"),
    RunNotFoundError: (HTTP_NOT_FOUND, "run_not_found"),
    VaultChangedError: (HTTP_CONFLICT, "vault_changed_reindex"),
    IndexNotBuiltError: (HTTP_CONFLICT, "index_not_built"),
    RetrievalUnavailableError: (HTTP_SERVICE_UNAVAILABLE, "retrieval_unavailable"),
    PathNotAllowedError: (HTTP_BAD_REQUEST, "path_not_allowed"),
    PathNotFoundError: (HTTP_NOT_FOUND, "file_not_found"),
    ControlMapTooLargeError: (HTTP_UNPROCESSABLE, "control_map_too_large"),
    AuthControlsError: (HTTP_SERVICE_UNAVAILABLE, "auth_controls_invalid"),
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


async def _check_refused(_request: Request, exc: Exception) -> JSONResponse:
    code = exc.code if isinstance(exc, CheckRefusedError) else "unknown"
    return JSONResponse({"error": "check_refused", "code": code}, status_code=HTTP_UNPROCESSABLE)


async def _ledger_refused(_request: Request, exc: Exception) -> JSONResponse:
    """`code` is a fixed word; `customer_id` (an id ALIBI made, never a name) only on a clash."""
    body: dict[str, str] = {"error": "ledger_refused", "code": "unknown"}
    if isinstance(exc, LedgerRefusedError):
        body["code"] = exc.code
        if exc.customer_id is not None:
            body["customer_id"] = exc.customer_id
    return JSONResponse(body, status_code=HTTP_UNPROCESSABLE)


async def _import_refused(_request: Request, exc: Exception) -> JSONResponse:
    """`code` is a fixed word and `counts` holds integers only, never text from the workbook."""
    body: dict[str, object] = {"error": "import_refused", "code": "unknown"}
    if isinstance(exc, ImportRefusedError):
        body["code"] = exc.code
        if exc.counts:
            body["counts"] = exc.counts
    return JSONResponse(body, status_code=HTTP_UNPROCESSABLE)


async def _draft_refused(_request: Request, exc: Exception) -> JSONResponse:
    """`code` is a fixed word and `counts` holds integers only, never text from the workbook."""
    body: dict[str, object] = {"error": "draft_refused", "code": "unknown"}
    if isinstance(exc, DraftRefusedError):
        body["code"] = exc.code
        if exc.counts:
            body["counts"] = exc.counts
    return JSONResponse(body, status_code=HTTP_UNPROCESSABLE)


async def _recall_refused(_request: Request, exc: Exception) -> JSONResponse:
    """`code` is a fixed word and `counts` holds integers only."""
    body: dict[str, object] = {"error": "recall_refused", "code": "unknown"}
    if isinstance(exc, RecallRefusedError):
        body["code"] = exc.code
        if exc.counts:
            body["counts"] = exc.counts
    return JSONResponse(body, status_code=HTTP_UNPROCESSABLE)


async def _blast_radius_refused(_request: Request, exc: Exception) -> JSONResponse:
    """`code` is a fixed word and `counts` holds integers only, never any of the proposed text."""
    body: dict[str, object] = {"error": "blast_radius_refused", "code": "unknown"}
    if isinstance(exc, BlastRadiusRefusedError):
        body["code"] = exc.code
        if exc.counts:
            body["counts"] = exc.counts
    return JSONResponse(body, status_code=HTTP_UNPROCESSABLE)


async def _graph_refused(_request: Request, exc: Exception) -> JSONResponse:
    """`code` is a fixed word and `counts` holds integers only, never a path or vault text."""
    body: dict[str, object] = {"error": "graph_refused", "code": "unknown"}
    if isinstance(exc, GraphRefusedError):
        body["code"] = exc.code
        if exc.counts:
            body["counts"] = exc.counts
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
    app.add_exception_handler(CheckRefusedError, _check_refused)
    app.add_exception_handler(LedgerRefusedError, _ledger_refused)
    app.add_exception_handler(ImportRefusedError, _import_refused)
    app.add_exception_handler(DraftRefusedError, _draft_refused)
    app.add_exception_handler(RecallRefusedError, _recall_refused)
    app.add_exception_handler(BlastRadiusRefusedError, _blast_radius_refused)
    app.add_exception_handler(GraphRefusedError, _graph_refused)
    app.add_exception_handler(QuestionMappingError, _mapping_refused)
    app.add_exception_handler(RequestValidationError, _invalid_request)
