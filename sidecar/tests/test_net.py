import asyncio
import socketserver
import threading
from collections.abc import Awaitable
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, TypeVar

import httpx
import pytest

from grc_sidecar.errors import NonLoopbackURLError
from grc_sidecar.security.net import DEFAULT_TIMEOUT, create_async_client, validate_loopback_url

T = TypeVar("T")

ACCEPTED = [
    "http://127.0.0.1:11434",
    "http://127.0.0.2:8080",
    "http://[::1]:11434",
    "http://127.0.0.1:11434/v1",
]

REJECTED = [
    "http://example.com",
    "http://localhost:11434",
    "http://192.168.1.10",
    "http://10.0.0.1",
    "http://0.0.0.0:11434",
    "https://127.0.0.1",
    "ftp://127.0.0.1",
    "http://127.0.0.1@evil.com/",
    "http://user:pw@127.0.0.1/",
    "http://127.0.0.1.evil.com",
    "http://2130706433",
    "http://0x7f.0.0.1",
    "http://127.1",
    "http://[::ffff:127.0.0.1]",
    "http://[::ffff:8.8.8.8]",
    "http://::1/",
    "http://[::1%25lo]/",
    "http://[::1%lo]/",
    "http://[127.0.0.1]/",
    "http://[::1",
    "http://[::1]x",
    "//127.0.0.1",
    "127.0.0.1:11434",
    "",
    "http://",
    "http://127.0.0.1:0",
    "http://127.0.0.1:99999",
    "http://127.0.0.1:abc",
    "http://127.0.0.1:",
    "http://127.0.0.1/?x=1",
    "http://127.0.0.1/#f",
    "http://127.0.0.1\\@evil.com",
    "http://127.0.0.1\n",
    "http://127.0.0.1\t",
    "http://127.0.0.1 ",
    " http://127.0.0.1",
    "http://127.0.0.1é",
    "http://１２７.0.0.1",  # noqa: RUF001 (fullwidth digits are deliberate)
]


def run(coro: Awaitable[T]) -> T:
    async def _wrap() -> T:
        return await coro

    return asyncio.run(_wrap())


@pytest.mark.parametrize("url", ACCEPTED)
def test_accepts_loopback_urls_unchanged(url: str) -> None:
    assert validate_loopback_url(url) == url


@pytest.mark.parametrize("url", REJECTED)
def test_rejects_non_loopback_or_malformed_urls(url: str) -> None:  # [NEG]
    with pytest.raises(NonLoopbackURLError):
        validate_loopback_url(url)


def test_error_never_echoes_input() -> None:  # [NEG]
    secret_url = "http://user:s3cr3t-token@evil.example/path"
    with pytest.raises(NonLoopbackURLError) as info:
        validate_loopback_url(secret_url)
    for text in (str(info.value), repr(info.value), repr(info.value.args)):
        assert "s3cr3t" not in text
        assert "evil.example" not in text


@pytest.mark.parametrize("value", [None, 11434, b"http://127.0.0.1", ["http://127.0.0.1"]])
def test_rejects_non_string_input(value: Any) -> None:  # [NEG]
    with pytest.raises(NonLoopbackURLError):
        validate_loopback_url(value)


def test_factory_client_settings() -> None:
    async def go() -> None:
        client = create_async_client("http://127.0.0.1:11434")
        try:
            assert client.trust_env is False
            assert client.follow_redirects is False
            assert str(client.base_url).startswith("http://127.0.0.1:11434")
            assert client.timeout == DEFAULT_TIMEOUT
        finally:
            await client.aclose()

    run(go())


def test_factory_rejects_bad_base_url_before_building_a_client() -> None:  # [NEG]
    with pytest.raises(NonLoopbackURLError):
        create_async_client("http://example.com")


def _recording_transport(
    seen: list[httpx.Request], response: httpx.Response | None = None
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response or httpx.Response(200, text="ok")

    return httpx.MockTransport(handler)


@pytest.mark.parametrize(
    "url", ["http://example.com/x", "https://127.0.0.1/", "http://user@127.0.0.1/"]
)
def test_absolute_url_override_is_blocked(url: str) -> None:  # [NEG]
    seen: list[httpx.Request] = []

    async def go() -> None:
        async with create_async_client(
            "http://127.0.0.1:11434", transport=_recording_transport(seen)
        ) as client:
            await client.get(url)

    with pytest.raises(NonLoopbackURLError):
        run(go())
    assert seen == []


def test_loopback_requests_reach_the_transport() -> None:
    seen: list[httpx.Request] = []

    async def go() -> None:
        async with create_async_client(
            "http://127.0.0.1:11434", transport=_recording_transport(seen)
        ) as client:
            await client.get("/api/tags")
            await client.get("http://127.0.0.1:9999/x")
            await client.get("http://[::1]:11434/x")

    run(go())
    assert len(seen) == 3


def test_redirect_is_not_followed() -> None:  # [NEG]
    seen: list[httpx.Request] = []
    redirect = httpx.Response(302, headers={"Location": "http://example.com/"})

    async def go() -> httpx.Response:
        async with create_async_client(
            "http://127.0.0.1:11434", transport=_recording_transport(seen, redirect)
        ) as client:
            return await client.get("/x")

    response = run(go())
    assert response.status_code == 302
    assert len(seen) == 1


class _QuietHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format: str, *args: Any) -> None:
        pass


class _LoopbackServer(HTTPServer):
    def server_bind(self) -> None:
        # HTTPServer.server_bind does a reverse-DNS lookup; skip it.
        socketserver.TCPServer.server_bind(self)
        self.server_name = "127.0.0.1"
        self.server_port = self.server_address[1]


def test_proxy_environment_variables_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:  # [NEG]
    # If the client honored these, it would try to connect to 192.0.2.1 and the egress
    # guard would raise instead of the request reaching the local server.
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "http://192.0.2.1:3128")
    server = _LoopbackServer(("127.0.0.1", 0), _QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]

        async def go() -> httpx.Response:
            async with create_async_client(f"http://127.0.0.1:{port}") as client:
                return await client.get("/")

        response = run(go())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert response.status_code == 200
    assert response.text == "ok"
