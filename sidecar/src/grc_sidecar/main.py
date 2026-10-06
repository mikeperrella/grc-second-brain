"""Server assembly and the startup handshake (CLAUDE.md 3.3 and 3.4).

The sidecar binds 127.0.0.1 on an OS-assigned port (port 0) and, once the socket is
listening, prints exactly one JSON line to stdout: {"event":"ready","port":<int>}.
The Rust host reads that line to learn the port. Nothing else may be written to
stdout; logs go to a file (see logging_config).
"""

import json
import socket
import sys
from typing import Final, TextIO

import uvicorn
from fastapi import FastAPI

from grc_sidecar.api.health import router as health_router
from grc_sidecar.config import Settings
from grc_sidecar.security.auth import ASGIApp, AuthMiddleware

LOOPBACK_HOST: Final = "127.0.0.1"


def create_app(token: str) -> ASGIApp:
    """Build the app with authentication wrapped around it from the outside."""
    api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    api.include_router(health_router)
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

    def __init__(self, config: uvicorn.Config, ready_stream: TextIO | None = None) -> None:
        super().__init__(config)
        self._ready_stream = ready_stream

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        # Server.startup hands our pre-bound socket to the event loop, which starts
        # listening. `started` is only set after that succeeds.
        await super().startup(sockets=sockets)
        if self.started and sockets:
            self._emit_ready(sockets[0].getsockname()[1])

    def _emit_ready(self, port: int) -> None:
        stream = self._ready_stream if self._ready_stream is not None else sys.stdout
        stream.write(ready_line(port) + "\n")
        stream.flush()


def serve(token: str, settings: Settings) -> None:
    config = uvicorn.Config(
        create_app(token),
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
    sock = bind_loopback_socket()
    try:
        ReadySignalServer(config).run(sockets=[sock])
    finally:
        sock.close()
