"""The real parts of a questionnaire run, assembled from the settings (ADR-014).

Nothing here needs Ollama: it is not running, so a run that gets as far as the model refuses with
a typed error, which is itself the fail-closed behaviour under test.
"""

import asyncio
import hashlib
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from grc_sidecar.config import Settings
from grc_sidecar.errors import ModelsUnavailableError
from grc_sidecar.llm.model_gate import ModelGate
from grc_sidecar.llm.ollama_embeddings import OllamaEmbeddingClient
from grc_sidecar.llm.prompt import PROMPT_TEMPLATE_SHA256
from grc_sidecar.main import create_app
from grc_sidecar.questionnaire.runtime import QuestionnaireRuntime, build_runtime
from grc_sidecar.security.auth import ASGIApp
from grc_sidecar.security.net import create_async_client

from .clockkit import ManualClock
from .retrievalkit import BOTH_INSTALLED, FakeLister
from .runskit import FILE_NAME, MAPPING, make_workbook

pytestmark = pytest.mark.usefixtures("clean_grc_env")

TOKEN = hashlib.sha256(b"grc-test-token").hexdigest()
AUTH = {"X-GRC-Auth": TOKEN}


def folders(tmp_path: Path) -> dict[str, Path]:
    made = {
        "vault": tmp_path / "vault",
        "index": tmp_path / "index",
        "questionnaires": tmp_path / "questionnaires",
        "exports": tmp_path / "exports",
    }
    for folder in made.values():
        folder.mkdir()
    (made["questionnaires"] / FILE_NAME).write_bytes(make_workbook(2))
    return made


REAL_OLLAMA_PORT = 11434


def unused_loopback_url() -> str:
    """A loopback URL on a port nothing listens on, so a connection is refused at once.

    These tests must never reach a real Ollama (the owner's is running on 11434): every client they
    build is either given a mock transport or pointed at this.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert port != REAL_OLLAMA_PORT
    return f"http://127.0.0.1:{port}"


def settings_for(tmp_path: Path, made: dict[str, Path], **overrides: Path | None) -> Settings:
    values: dict[str, Path | str | None] = {
        "log_dir": tmp_path / "logs",
        "vault_dir": made["vault"],
        "index_dir": made["index"],
        "questionnaire_dir": made["questionnaires"],
        "export_dir": made["exports"],
        "ollama_base_url": unused_loopback_url(),
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def refuse_every_connection(_request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("no Ollama (a test double)")


def test_these_tests_never_point_a_client_at_the_real_ollama_port(tmp_path: Path) -> None:  # [NEG]
    settings = settings_for(tmp_path, folders(tmp_path))
    assert not settings.ollama_base_url.endswith(f":{REAL_OLLAMA_PORT}")


def parts(settings: Settings) -> tuple[ModelGate, OllamaEmbeddingClient]:
    embedder = OllamaEmbeddingClient(
        settings.ollama_base_url,
        settings.embedding_model,
        expected_dimension=settings.embedding_dimension,
        transport=httpx.MockTransport(lambda _request: httpx.Response(500)),
    )
    gate = ModelGate(
        FakeLister(BOTH_INSTALLED),
        (settings.embedding_model, settings.generation_model),
        ManualClock(),
    )
    return gate, embedder


@pytest.mark.parametrize("missing", ["vault_dir", "questionnaire_dir", "export_dir"])
def test_there_is_no_runtime_unless_all_three_folders_are_configured(  # [NEG]
    tmp_path: Path, missing: str
) -> None:
    made = folders(tmp_path)
    settings = settings_for(tmp_path, made, **{missing: None})
    gate, embedder = parts(settings)
    assert build_runtime(settings, gate, embedder) is None


def test_the_runtime_is_built_from_the_settings_and_closes(tmp_path: Path) -> None:
    made = folders(tmp_path)
    settings = settings_for(tmp_path, made)
    gate, embedder = parts(settings)
    runtime = build_runtime(
        settings, gate, embedder, generator_transport=httpx.MockTransport(refuse_every_connection)
    )
    assert isinstance(runtime, QuestionnaireRuntime)
    deps = runtime.runs._deps
    assert deps.questionnaire_root == made["questionnaires"]
    assert deps.export_root == made["exports"]
    assert deps.required_models == (settings.embedding_model, settings.generation_model)
    assert deps.model_name == settings.generation_model == "llama3.1:8b"
    assert deps.template_sha256 == PROMPT_TEMPLATE_SHA256
    asyncio.run(runtime.aclose())


def test_the_hash_of_a_cited_file_is_read_from_the_configured_vault(tmp_path: Path) -> None:
    made = folders(tmp_path)
    (made["vault"] / "note.md").write_text("# a note\n", encoding="utf-8")
    settings = settings_for(tmp_path, made)
    gate, embedder = parts(settings)
    runtime = build_runtime(
        settings, gate, embedder, generator_transport=httpx.MockTransport(refuse_every_connection)
    )
    assert runtime is not None
    current = runtime.runs._deps.current_sha256
    assert current("note.md") is not None and current("missing.md") is None
    asyncio.run(runtime.aclose())


def test_without_ollama_a_run_is_refused_before_anything_is_written(
    tmp_path: Path,
) -> None:  # [NEG]
    made = folders(tmp_path)
    settings = settings_for(tmp_path, made)
    gate, embedder = parts(settings)
    runtime = build_runtime(
        settings, gate, embedder, generator_transport=httpx.MockTransport(refuse_every_connection)
    )
    assert runtime is not None

    async def scenario() -> None:
        with pytest.raises(ModelsUnavailableError):
            await runtime.runs.start(FILE_NAME, MAPPING)

    asyncio.run(scenario())
    assert list(made["exports"].iterdir()) == []
    asyncio.run(runtime.aclose())


@asynccontextmanager
async def started(app: ASGIApp) -> AsyncIterator[httpx.AsyncClient]:
    """Run the app's startup and shutdown (the lifespan), and give a client for it.

    The ASGI test transport does not run the lifespan, so this drives it by hand: it sends the
    startup event, waits for it to finish, yields, then does the same for shutdown.
    """
    received: asyncio.Queue[dict[str, str]] = asyncio.Queue()
    sent: asyncio.Queue[dict[str, str]] = asyncio.Queue()
    task = asyncio.create_task(
        app({"type": "lifespan", "asgi": {"version": "3.0"}}, received.get, sent.put)  # type: ignore[arg-type]
    )
    await received.put({"type": "lifespan.startup"})
    assert (await sent.get())["type"] == "lifespan.startup.complete"
    try:
        async with create_async_client(
            "http://127.0.0.1:8000", transport=httpx.ASGITransport(app=app)
        ) as client:
            yield client
    finally:
        await received.put({"type": "lifespan.shutdown"})
        assert (await sent.get())["type"] == "lifespan.shutdown.complete"
        await task


def test_the_app_builds_the_runtime_at_startup_when_the_folders_are_set(tmp_path: Path) -> None:
    made = folders(tmp_path)

    async def scenario() -> None:
        app = create_app(TOKEN, settings=settings_for(tmp_path, made))
        async with started(app) as client:
            response = await client.post(
                "/questionnaire/inspect", json={"file": "missing.xlsx"}, headers=AUTH
            )
            # A configured app got as far as looking for the file.
            assert response.status_code == 404
            assert response.json() == {"error": "file_not_found"}

    asyncio.run(scenario())


def test_the_app_refuses_the_questionnaire_routes_when_a_folder_is_not_set(  # [NEG]
    tmp_path: Path,
) -> None:
    made = folders(tmp_path)

    async def scenario() -> None:
        app = create_app(TOKEN, settings=settings_for(tmp_path, made, export_dir=None))
        async with started(app) as client:
            response = await client.post(
                "/questionnaire/inspect", json={"file": FILE_NAME}, headers=AUTH
            )
            assert response.status_code == 503
            assert response.json() == {"error": "questionnaire_root_not_configured"}

    asyncio.run(scenario())
