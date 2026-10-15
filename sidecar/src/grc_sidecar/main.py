"""Server assembly and the startup handshake (CLAUDE.md 3.3 and 3.4).

The sidecar binds 127.0.0.1 on an OS-assigned port (port 0) and, once the socket is
listening, prints exactly one JSON line to stdout: {"event":"ready","port":<int>}.
The Rust host reads that line to learn the port. Nothing else may be written to
stdout; logs go to a file (see logging_config).
"""

import asyncio
import json
import socket
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from typing import Final, TextIO

import uvicorn
from fastapi import FastAPI

from grc_sidecar.api.contradictions import router as contradictions_router
from grc_sidecar.api.errors import register_error_handlers
from grc_sidecar.api.health import router as health_router
from grc_sidecar.api.ledger import router as ledger_router
from grc_sidecar.api.questionnaire import router as questionnaire_router
from grc_sidecar.api.readiness import router as readiness_router
from grc_sidecar.api.reality import router as reality_router
from grc_sidecar.api.vault_index import router as vault_index_router
from grc_sidecar.config import Settings
from grc_sidecar.contradiction_runtime import build_contradiction_runtime
from grc_sidecar.contradictions.runs import ContradictionRuns
from grc_sidecar.index_runtime import build_index_runtime
from grc_sidecar.jobs import JobSlot
from grc_sidecar.ledger.service import LedgerService, build_ledger_service
from grc_sidecar.llm.model_gate import ModelGate
from grc_sidecar.llm.ollama_embeddings import OllamaEmbeddingClient
from grc_sidecar.questionnaire.runs import QuestionnaireRuns
from grc_sidecar.questionnaire.runtime import build_runtime
from grc_sidecar.reality.runtime import RealityRuntime, build_reality_runtime
from grc_sidecar.security.auth import ASGIApp, AuthMiddleware
from grc_sidecar.vault.clock import SystemClock
from grc_sidecar.vault.index_jobs import IndexJobs

LOOPBACK_HOST: Final = "127.0.0.1"


def _model_check_lifespan(
    settings: Settings,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Startup and shutdown: build the Ollama client and the model gate, check in the background.

    The check is a task, not an `await` before `yield`: the ready line is printed once startup
    finishes, and the host gives the sidecar a fixed time to print it (CLAUDE.md 3.4), so a slow
    or absent Ollama must never delay it (ADR-011).
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = OllamaEmbeddingClient(
            settings.ollama_base_url,
            settings.embedding_model,
            expected_dimension=settings.embedding_dimension,
        )
        gate = ModelGate(
            client, (settings.embedding_model, settings.generation_model), SystemClock()
        )
        app.state.gate = gate
        slot = JobSlot()  # one heavy job at a time, shared by questionnaire and contradiction runs
        runtime = build_runtime(settings, gate, client, slot=slot)
        app.state.questionnaire = None if runtime is None else runtime.runs
        contradictions = build_contradiction_runtime(settings, gate, slot)
        app.state.contradictions = None if contradictions is None else contradictions.runs
        ledger, close_ledger = build_ledger_service(
            settings, None if runtime is None else runtime.ledger, SystemClock()
        )
        app.state.ledger = ledger
        app.state.reality = build_reality_runtime(settings)
        indexing = build_index_runtime(settings, gate, client, slot)
        app.state.index_jobs = None if indexing is None else indexing.jobs
        check = asyncio.create_task(gate.refresh())
        try:
            yield
        finally:
            check.cancel()
            with suppress(asyncio.CancelledError):
                await check
            if runtime is not None:
                await runtime.aclose()
            if contradictions is not None:
                await contradictions.aclose()
            if indexing is not None:
                await indexing.aclose()
            if close_ledger is not None:
                await close_ledger()
            await client.aclose()

    return lifespan


def create_app(
    token: str,
    *,
    settings: Settings | None = None,
    gate: ModelGate | None = None,
    questionnaire: QuestionnaireRuns | None = None,
    reality: RealityRuntime | None = None,
    ledger: LedgerService | None = None,
    contradictions: ContradictionRuns | None = None,
    index_jobs: IndexJobs | None = None,
) -> ASGIApp:
    """Build the app with authentication wrapped around it from the outside.

    With `settings`, startup builds the Ollama client and a model gate and checks the models in
    the background, and, if the vault, questionnaire and export folders are all configured, the
    questionnaire runs; with the import and vault folders configured, the reality check; with the
    ledger folder configured, the run record; with the vault folder configured, the contradiction
    detector. A ready-made `gate`, `questionnaire`, `reality`, `ledger` and `contradictions` are for
    tests; with none of them,
    `/readiness` and anything that needs a model or a folder refuse (503) instead of proceeding
    unchecked.
    """
    lifespan = _model_check_lifespan(settings) if settings is not None and gate is None else None
    api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    api.state.gate = gate
    api.state.questionnaire = questionnaire
    api.state.reality = reality
    api.state.ledger = ledger
    api.state.contradictions = contradictions
    api.state.index_jobs = index_jobs
    register_error_handlers(api)
    api.include_router(health_router)
    api.include_router(readiness_router)
    api.include_router(questionnaire_router)
    api.include_router(contradictions_router)
    api.include_router(reality_router)
    api.include_router(ledger_router)
    api.include_router(vault_index_router)
    # Wrapping (instead of api.add_middleware) puts auth outside Starlette's own
    # error handling, so even 404/405/422/500 responses require the token.
    return AuthMiddleware(api, token)


def bind_loopback_socket() -> socket.socket:
    """Create an IPv4 socket bound to 127.0.0.1 on an OS-chosen port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform == "win32":
            # Stops another process from binding the same port (never use SO_REUSEADDR).
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind((LOOPBACK_HOST, 0))
    except OSError:
        sock.close()
        raise
    return sock


def ready_line(port: int) -> str:
    return json.dumps({"event": "ready", "port": port}, separators=(",", ":"))


class ReadySignalServer(uvicorn.Server):
    """uvicorn server that announces its port once it is listening."""

    def __init__(self, config: uvicorn.Config, ready_stream: TextIO) -> None:
        super().__init__(config)
        self._ready_stream = ready_stream

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        # Server.startup hands our pre-bound socket to the event loop, which starts
        # listening. `started` is only set after that succeeds.
        await super().startup(sockets=sockets)
        if self.started and sockets:
            self._emit_ready(sockets[0].getsockname()[1])

    def _emit_ready(self, port: int) -> None:
        # The stream is the private copy made by seal_stdout. Closing it right after the line
        # leaves this process holding no handle to the host's pipe.
        with self._ready_stream as stream:
            stream.write(ready_line(port) + "\n")


def serve(token: str, settings: Settings, sock: socket.socket, ready_stream: TextIO) -> None:
    """Serve on an already bound loopback socket and close it afterwards."""
    config = uvicorn.Config(
        create_app(token, settings=settings),
        host=LOOPBACK_HOST,
        port=0,
        log_config=None,  # logging is configured by us, to a file
        log_level=settings.log_level.lower(),
        access_log=False,
        server_header=False,
        http="h11",
        ws="none",
        proxy_headers=False,
    )
    try:
        ReadySignalServer(config, ready_stream).run(sockets=[sock])
    finally:
        sock.close()
