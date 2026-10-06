"""Zero-trust IPC authentication (CLAUDE.md 3.4).

The Rust host generates a random token, passes it to the sidecar in the
GRC_AUTH_TOKEN environment variable, and sends it back in the X-GRC-Auth header on
every request. read_auth_token reads the variable exactly once and removes it from
the environment; AuthMiddleware then rejects any HTTP request without the token.

Errors raised here carry fixed messages and are raised outside `except` blocks, so
the token can never appear in an error, its exception chain, or a traceback.
"""

import hmac
import os
import re
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any, Final

from grc_sidecar.errors import InvalidAuthTokenError, MissingAuthTokenError

AUTH_TOKEN_ENV_VAR: Final = "GRC_AUTH_TOKEN"  # noqa: S105  (the variable name, not a secret)
AUTH_HEADER: Final = b"x-grc-auth"  # ASGI servers give header names in lowercase

# 32 random bytes, hex-encoded by the host. Lowercase only, so there is one spelling.
_TOKEN_PATTERN: Final = re.compile(r"[0-9a-f]{64}")

# Minimal ASGI types, defined here so we do not import from a transitive dependency.
Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_WEBSOCKET_POLICY_VIOLATION: Final = 1008


def _validate_token(token: str) -> str:
    if not token:
        raise MissingAuthTokenError
    if not _TOKEN_PATTERN.fullmatch(token):
        raise InvalidAuthTokenError
    return token


def read_auth_token(environ: MutableMapping[str, str] | None = None) -> str:
    """Read the token once and delete it from the environment.

    The variable is removed on every outcome (valid, invalid, or missing), so the
    token cannot leak to child processes or later reads. Call this first at startup.
    """
    env = os.environ if environ is None else environ
    raw = env.pop(AUTH_TOKEN_ENV_VAR, "")
    return _validate_token(raw)


class AuthMiddleware:
    """Pure ASGI middleware: only requests carrying the exact token reach the app.

    It wraps the whole application from the outside, so FastAPI's own responses
    (404 for unknown routes, 405, 422, 500) are also behind authentication.
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._token = _validate_token(token).encode("ascii")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope["type"]
        if scope_type == "lifespan":
            # Server startup/shutdown events carry no client data; uvicorn needs them.
            await self._app(scope, receive, send)
        elif scope_type == "http":
            if self._is_authorized(scope):
                await self._app(scope, receive, send)
            else:
                await _send_unauthorized(send)
        elif scope_type == "websocket":
            await send({"type": "websocket.close", "code": _WEBSOCKET_POLICY_VIOLATION})
        # Any other scope type is dropped without reaching the app (fail closed).

    def _is_authorized(self, scope: Scope) -> bool:
        provided = [
            value for name, value in scope.get("headers", ()) if name.lower() == AUTH_HEADER
        ]
        if len(provided) != 1:  # missing or duplicated header
            return False
        return hmac.compare_digest(provided[0], self._token)


async def _send_unauthorized(send: Send) -> None:
    # Empty body, and no WWW-Authenticate header: see ADR-002.
    await send(
        {"type": "http.response.start", "status": 401, "headers": [(b"content-length", b"0")]}
    )
    await send({"type": "http.response.body", "body": b""})
