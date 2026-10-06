import asyncio
import hashlib

import httpx
import pytest

from grc_sidecar.api.health import HealthResponse
from grc_sidecar.main import create_app
from grc_sidecar.security.net import create_async_client

# Deterministic 64-hex fixture derived by hashing, so no secret-looking literal is committed.
TOKEN = hashlib.sha256(b"grc-test-token").hexdigest()
AUTH = {"X-GRC-Auth": TOKEN}


def request(method: str, path: str, headers: dict[str, str] | None = None) -> httpx.Response:
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(TOKEN))
        async with create_async_client("http://127.0.0.1:8000", transport=transport) as client:
            return await client.request(method, path, headers=headers)

    return asyncio.run(go())


def test_health_returns_ok() -> None:
    response = request("GET", "/health", AUTH)
    assert response.status_code == 200
    assert response.content == b'{"status":"ok"}'
    assert HealthResponse.model_validate_json(response.content).status == "ok"


def test_health_requires_the_token() -> None:  # [NEG]
    assert request("GET", "/health").status_code == 401


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_api_docs_are_disabled(path: str) -> None:  # [NEG]
    assert request("GET", path, AUTH).status_code == 404


def test_health_rejects_other_methods() -> None:  # [NEG]
    assert request("POST", "/health", AUTH).status_code == 405
