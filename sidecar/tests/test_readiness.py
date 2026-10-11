"""`GET /readiness` and the 503 mapping for a missing model (CLAUDE.md 3.7, ADR-011)."""

import asyncio
import hashlib

import httpx
import pytest
from fastapi import FastAPI

from grc_sidecar.api.errors import register_error_handlers
from grc_sidecar.errors import ModelsUnavailableError
from grc_sidecar.llm.model_gate import ModelGate
from grc_sidecar.llm.ollama_embeddings import OllamaEmbeddingClient
from grc_sidecar.main import create_app
from grc_sidecar.security.net import create_async_client

from .clockkit import ManualClock
from .test_model_gate import BASE_URL, EMBEDDING, GENERATION, REQUIRED, FakeOllama

TOKEN = hashlib.sha256(b"grc-test-token").hexdigest()
AUTH = {"X-GRC-Auth": TOKEN}


def gate_for(fake: FakeOllama) -> ModelGate:
    client = OllamaEmbeddingClient(
        BASE_URL, EMBEDDING, expected_dimension=4, transport=httpx.MockTransport(fake)
    )
    return ModelGate(client, REQUIRED, ManualClock())


def request(
    app: object, method: str, path: str, headers: dict[str, str] | None = None
) -> httpx.Response:
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with create_async_client("http://127.0.0.1:8000", transport=transport) as client:
            return await client.request(method, path, headers=headers)

    return asyncio.run(go())


def test_readiness_reports_ready() -> None:
    fake = FakeOllama()
    response = request(create_app(TOKEN, gate=gate_for(fake)), "GET", "/readiness", AUTH)
    assert response.status_code == 200
    assert response.json() == {"state": "ready", "models_missing": [], "ollama": "reachable"}


def test_readiness_reports_a_missing_model_by_name() -> None:
    fake = FakeOllama((f"{EMBEDDING}:latest",))
    response = request(create_app(TOKEN, gate=gate_for(fake)), "GET", "/readiness", AUTH)
    assert response.status_code == 200
    assert response.json() == {
        "state": "degraded",
        "models_missing": [GENERATION],
        "ollama": "reachable",
    }


def test_readiness_reports_an_unreachable_ollama() -> None:
    fake = FakeOllama(error=lambda r: httpx.ConnectError("refused", request=r))
    response = request(create_app(TOKEN, gate=gate_for(fake)), "GET", "/readiness", AUTH)
    assert response.status_code == 200
    assert response.json() == {
        "state": "degraded",
        "models_missing": list(REQUIRED),
        "ollama": "unreachable",
    }


def test_readiness_requires_the_token() -> None:  # [NEG]
    fake = FakeOllama()
    app = create_app(TOKEN, gate=gate_for(fake))
    assert request(app, "GET", "/readiness").status_code == 401
    assert request(app, "GET", "/readiness", {"X-GRC-Auth": "0" * 64}).status_code == 401
    assert fake.requests == [], "an unauthenticated request must not reach Ollama"


def test_readiness_rejects_other_methods() -> None:  # [NEG]
    app = create_app(TOKEN, gate=gate_for(FakeOllama()))
    assert request(app, "POST", "/readiness", AUTH).status_code == 405


def test_an_app_without_a_gate_refuses_instead_of_proceeding() -> None:  # [NEG]
    response = request(create_app(TOKEN), "GET", "/readiness", AUTH)
    assert response.status_code == 503
    assert response.json() == {"error": "models_unavailable", "models_missing": []}


def test_health_is_unchanged_when_models_are_missing() -> None:
    fake = FakeOllama(())
    app = create_app(TOKEN, gate=gate_for(fake))
    response = request(app, "GET", "/health", AUTH)
    assert response.status_code == 200
    assert response.content == b'{"status":"ok"}'
    assert fake.requests == [], "/health must not depend on Ollama"


def test_a_model_error_becomes_a_503_with_names_only() -> None:  # [NEG]
    api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    register_error_handlers(api)

    @api.get("/needs-a-model")
    async def needs_a_model() -> None:
        raise ModelsUnavailableError((GENERATION,))

    response = request(api, "GET", "/needs-a-model")
    assert response.status_code == 503
    assert response.json() == {"error": "models_unavailable", "models_missing": [GENERATION]}


@pytest.mark.parametrize("path", ["/readiness", "/health"])
def test_unauthenticated_requests_never_get_a_body(path: str) -> None:  # [NEG]
    response = request(create_app(TOKEN, gate=gate_for(FakeOllama())), "GET", path)
    assert response.status_code == 401
    assert response.content == b""
