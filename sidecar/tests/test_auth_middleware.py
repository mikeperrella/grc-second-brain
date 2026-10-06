import asyncio
import hashlib
import hmac
from collections.abc import Sequence
from typing import Any

import httpx
import pytest

from grc_sidecar.errors import InvalidAuthTokenError, MissingAuthTokenError
from grc_sidecar.main import create_app
from grc_sidecar.security.auth import AuthMiddleware
from grc_sidecar.security.net import create_async_client

from .leakcheck import assert_no_leak

# Deterministic 64-hex fixture derived by hashing, so no secret-looking literal is committed.
TOKEN = hashlib.sha256(b"grc-test-token").hexdigest()
NAME = b"x-grc-auth"
OTHER_TOKEN = TOKEN[:-1] + ("0" if TOKEN[-1] != "0" else "1")  # same length, differs in last char
Headers = Sequence[tuple[bytes, bytes]]

WRONG_TOKENS = {
    "same length, last char changed": OTHER_TOKEN,
    "one char shorter": TOKEN[:-1],
    "one char longer": TOKEN + "0",
    "empty": "",
    "padded with space": " " + TOKEN,
    "uppercase": TOKEN.upper(),
}


class RecordingApp:
    """Stands in for the real app and records which scopes reached it."""

    def __init__(self) -> None:
        self.scope_types: list[str] = []

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.scope_types.append(scope["type"])
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"inner"})


def http_scope(headers: Headers) -> dict[str, Any]:
    return {"type": "http", "method": "GET", "path": "/x", "headers": list(headers)}


def call(app: Any, scope: dict[str, Any]) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Any) -> None:
        sent.append(dict(message))

    asyncio.run(app(scope, receive, send))
    return sent


def assert_plain_401(sent: list[dict[str, Any]]) -> None:
    assert len(sent) == 2
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 401
    assert sent[0]["headers"] == [(b"content-length", b"0")]  # no WWW-Authenticate
    assert sent[1] == {"type": "http.response.body", "body": b""}


def make_client(token: str = TOKEN) -> httpx.AsyncClient:
    return create_async_client(
        "http://127.0.0.1:8000", transport=httpx.ASGITransport(app=create_app(token))
    )


def test_valid_token_reaches_the_inner_app() -> None:
    inner = RecordingApp()
    sent = call(AuthMiddleware(inner, TOKEN), http_scope([(NAME, TOKEN.encode())]))
    assert inner.scope_types == ["http"]
    assert sent[0]["status"] == 200
    # The header name match is case-insensitive at the byte level.
    inner2 = RecordingApp()
    call(AuthMiddleware(inner2, TOKEN), http_scope([(b"X-GRC-Auth", TOKEN.encode())]))
    assert inner2.scope_types == ["http"]


def test_missing_header_is_rejected() -> None:  # [NEG]
    inner = RecordingApp()
    sent = call(AuthMiddleware(inner, TOKEN), http_scope([(b"accept", b"*/*")]))
    assert_plain_401(sent)
    assert inner.scope_types == []


@pytest.mark.parametrize("wrong", WRONG_TOKENS.values(), ids=WRONG_TOKENS.keys())
def test_wrong_token_is_rejected(wrong: str) -> None:  # [NEG]
    inner = RecordingApp()
    sent = call(AuthMiddleware(inner, TOKEN), http_scope([(NAME, wrong.encode())]))
    assert_plain_401(sent)
    assert inner.scope_types == []


@pytest.mark.parametrize(
    "values",
    [[TOKEN, TOKEN], [TOKEN, OTHER_TOKEN], [OTHER_TOKEN, TOKEN]],
    ids=["both valid", "valid then invalid", "invalid then valid"],
)
def test_duplicate_header_is_rejected(values: list[str]) -> None:  # [NEG]
    inner = RecordingApp()
    headers = [(NAME, value.encode()) for value in values]
    assert_plain_401(call(AuthMiddleware(inner, TOKEN), http_scope(headers)))
    assert inner.scope_types == []


def test_websocket_is_rejected_even_with_a_valid_token() -> None:  # [NEG]
    inner = RecordingApp()
    scope = {"type": "websocket", "path": "/ws", "headers": [(NAME, TOKEN.encode())]}
    sent = call(AuthMiddleware(inner, TOKEN), scope)
    assert sent == [{"type": "websocket.close", "code": 1008}]
    assert inner.scope_types == []


def test_unknown_scope_type_is_dropped() -> None:  # [NEG]
    inner = RecordingApp()
    sent = call(AuthMiddleware(inner, TOKEN), {"type": "something-new", "headers": []})
    assert sent == []
    assert inner.scope_types == []


def test_lifespan_passes_through() -> None:
    inner = RecordingApp()
    call(AuthMiddleware(inner, TOKEN), {"type": "lifespan"})
    assert inner.scope_types == ["lifespan"]


def test_comparison_uses_hmac_compare_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    call(AuthMiddleware(RecordingApp(), TOKEN), http_scope([(NAME, TOKEN.encode())]))
    assert calls == [(TOKEN.encode(), TOKEN.encode())]


@pytest.mark.parametrize(
    ("token", "error"), [("", MissingAuthTokenError), ("not-hex-at-all", InvalidAuthTokenError)]
)
def test_middleware_refuses_to_be_built_with_a_bad_token(
    token: str, error: type[Exception]
) -> None:  # [NEG]
    with pytest.raises(error) as info:
        AuthMiddleware(RecordingApp(), token)
    assert_no_leak(info.value, "not-hex-at-all")


def test_token_is_not_exposed_by_repr_or_responses() -> None:  # [NEG]
    middleware = AuthMiddleware(RecordingApp(), TOKEN)
    assert TOKEN not in repr(middleware)
    for headers in ([], [(NAME, TOKEN.encode())]):
        for message in call(middleware, http_scope(headers)):
            assert TOKEN not in repr(message)


@pytest.mark.parametrize("path", ["/health", "/nope", "/docs", "/redoc", "/openapi.json"])
def test_unauthenticated_requests_get_401_not_404(path: str) -> None:  # [NEG]
    async def go() -> httpx.Response:
        async with make_client() as client:
            return await client.get(path)

    response = asyncio.run(go())
    assert response.status_code == 401
    assert response.content == b""
    assert "www-authenticate" not in response.headers


def test_authenticated_unknown_route_is_404() -> None:
    async def go() -> httpx.Response:
        async with make_client() as client:
            return await client.get("/nope", headers={"X-GRC-Auth": TOKEN})

    assert asyncio.run(go()).status_code == 404
