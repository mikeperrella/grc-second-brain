"""The Ollama embedding client, against a fake Ollama (httpx.MockTransport). No model needed."""

import asyncio
import json
import logging
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

import httpx
import pytest

from grc_sidecar.errors import (
    EmbeddingDimensionError,
    EmbeddingResponseError,
    EmbeddingUnavailableError,
    EndpointNotAllowedError,
    GrcError,
    ModelMissingError,
    NonLoopbackURLError,
)
from grc_sidecar.llm.ollama_embeddings import (
    DEFAULT_TIMEOUT,
    FIRST_CALL_TIMEOUT,
    OllamaEmbeddingClient,
)

BASE_URL = "http://127.0.0.1:11434"
SENTINEL = "CONFIDENTIAL POLICY TEXT"
DIM = 4

T = TypeVar("T")


def run(coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


class FakeOllama:
    """A scriptable stand-in for Ollama that records every request it receives."""

    def __init__(
        self,
        *,
        models: tuple[str, ...] = ("nomic-embed-text:latest",),
        dimension: int = DIM,
        embed_response: Callable[[httpx.Request], httpx.Response] | None = None,
        tags_response: Callable[[httpx.Request], httpx.Response] | None = None,
    ) -> None:
        self.models = models
        self.dimension = dimension
        self.embed_response = embed_response
        self.tags_response = tags_response
        self.requests: list[httpx.Request] = []

    @property
    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]

    def embed_bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests if r.url.path == "/api/embed"]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/api/tags":
            if self.tags_response is not None:
                return self.tags_response(request)
            entries = [{"name": m, "model": m, "digest": "sha256:abc123"} for m in self.models]
            return httpx.Response(200, json={"models": entries})
        if request.url.path == "/api/embed":
            if self.embed_response is not None:
                return self.embed_response(request)
            texts = json.loads(request.content)["input"]
            vectors = [[_marker(text)] + [0.5] * (self.dimension - 1) for text in texts]
            return httpx.Response(
                200, json={"embeddings": vectors, "prompt_eval_count": 3 * len(texts)}
            )
        return httpx.Response(404, json={"error": "not found"})


def _marker(text: str) -> float:
    """`t17` -> 17.0, so a test can tell which text a vector came from."""
    return float(text[1:]) if text.startswith("t") and text[1:].isdigit() else 0.0


def make_client(fake: FakeOllama, **kwargs: Any) -> OllamaEmbeddingClient:
    return OllamaEmbeddingClient(
        BASE_URL,
        kwargs.pop("model", "nomic-embed-text"),
        expected_dimension=kwargs.pop("expected_dimension", DIM),
        transport=httpx.MockTransport(fake),
        **kwargs,
    )


async def ready(client: OllamaEmbeddingClient) -> None:
    await client.ensure_ready()


# ----- readiness: model installed, probe, profile --------------------------------------------


def test_ensure_ready_checks_tags_then_probes_and_returns_the_profile() -> None:
    fake = FakeOllama()
    client = make_client(fake)
    profile = run(client.ensure_ready())
    assert [(r.method, r.url.path) for r in fake.requests] == [
        ("GET", "/api/tags"),
        ("POST", "/api/embed"),
    ]
    assert profile.model == "nomic-embed-text"
    assert profile.model_digest == "sha256:abc123"
    assert profile.dimension == DIM
    assert profile.doc_prefix == "search_document: "
    assert profile.query_prefix == "search_query: "


@pytest.mark.parametrize(
    ("wanted", "installed", "found"),
    [
        ("nomic-embed-text", "nomic-embed-text:latest", True),
        ("nomic-embed-text:latest", "nomic-embed-text:latest", True),
        ("nomic-embed-text:v1.5", "nomic-embed-text:v1.5", True),
        ("nomic-embed-text", "nomic-embed-text-v2:latest", False),
        ("nomic-embed-text", "nomic-embed-text:v1.5", False),
        ("nomic-embed-text", "other:latest", False),
    ],
)
def test_model_names_match_exactly_with_latest_as_the_default_tag(
    wanted: str, installed: str, found: bool
) -> None:
    client = make_client(FakeOllama(models=(installed,)), model=wanted)
    if found:
        run(client.ensure_ready())
    else:
        with pytest.raises(ModelMissingError):
            run(client.ensure_ready())


def test_missing_model_fails_closed(  # [NEG] mutation row 3
) -> None:
    fake = FakeOllama(models=("llama3.1:8b",))
    client = make_client(fake)
    with pytest.raises(ModelMissingError) as caught:
        run(client.ensure_ready())
    assert fake.paths == ["/api/tags"]  # it stopped before any embed request
    assert "install it yourself" in str(caught.value)  # a human provisions models


def test_a_404_from_the_embed_endpoint_also_means_the_model_is_missing() -> None:  # [NEG]
    fake = FakeOllama(embed_response=lambda r: httpx.Response(404, json={"error": SENTINEL}))
    with pytest.raises(ModelMissingError):
        run(make_client(fake).ensure_ready())


def test_probe_dimension_mismatch_fails_closed(  # [NEG] mutation: dimension check
) -> None:
    fake = FakeOllama(dimension=DIM + 1)  # the model answers with 5 numbers, the settings say 4
    with pytest.raises(EmbeddingDimensionError):
        run(make_client(fake).ensure_ready())


def test_embedding_before_ready_is_refused() -> None:  # [NEG]
    fake = FakeOllama()
    with pytest.raises(EmbeddingUnavailableError):
        run(make_client(fake).embed_documents(["t1"]))
    assert fake.requests == []


# ----- request shape ---------------------------------------------------------------------------


def test_requests_disable_truncation() -> None:  # [NEG] mutation row 15
    fake = FakeOllama()
    client = make_client(fake)

    async def scenario() -> None:
        await client.ensure_ready()
        await client.embed_documents(["t1", "t2"])
        await client.embed_query("what")

    run(scenario())
    bodies = fake.embed_bodies()
    assert len(bodies) == 3  # the probe, the documents and the query
    for body in bodies:
        assert body["truncate"] is False  # Ollama's default (true) would silently cut the text
        assert body["model"] == "nomic-embed-text"
        assert isinstance(body["input"], list)


def test_texts_are_sent_in_batches_of_16_and_come_back_in_order() -> None:
    fake = FakeOllama()
    client = make_client(fake)
    texts = [f"t{n}" for n in range(40)]

    async def scenario() -> list[list[float]]:
        await client.ensure_ready()
        return await client.embed_documents(texts)

    vectors = run(scenario())
    assert [len(body["input"]) for body in fake.embed_bodies()[1:]] == [16, 16, 8]
    assert [vector[0] for vector in vectors] == [float(n) for n in range(40)]


def test_a_query_gets_the_query_prefix_and_documents_are_sent_as_given() -> None:
    fake = FakeOllama()
    client = make_client(fake)

    async def scenario() -> None:
        await client.ensure_ready()
        await client.embed_query("multi-factor")
        await client.embed_documents(["search_document: Heading\nbody"])

    run(scenario())
    inputs = [body["input"] for body in fake.embed_bodies()]
    assert inputs[-2] == ["search_query: multi-factor"]
    assert inputs[-1] == ["search_document: Heading\nbody"]  # the chunker already added the prefix


def test_embedding_no_texts_makes_no_request() -> None:
    fake = FakeOllama()
    client = make_client(fake)

    async def scenario() -> list[list[float]]:
        await client.ensure_ready()
        fake.requests.clear()
        return await client.embed_documents([])

    assert run(scenario()) == []
    assert fake.requests == []


def test_the_token_count_is_summed_across_batches() -> None:
    fake = FakeOllama()
    client = make_client(fake)

    async def scenario() -> int | None:
        await client.ensure_ready()
        return (
            await client.embed_documents_with_stats([f"t{n}" for n in range(20)])
        ).prompt_eval_count

    assert run(scenario()) == 3 * 20


# ----- response validation ---------------------------------------------------------------------

GOOD = [0.1, 0.2, 0.3, 0.4]


def _json(payload: object) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(200, json=payload)


def _raw(text: str) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(200, content=text.encode())


@pytest.mark.parametrize(
    "respond",
    [
        _json({"embeddings": []}),  # fewer vectors than inputs
        _json({"embeddings": [GOOD, GOOD]}),  # more vectors than inputs
        _json({"embeddings": [[0.1, 0.2]]}),  # wrong dimension
        _json({"embeddings": [[]]}),  # empty vector
        _json({"embeddings": [["x", 0.2, 0.3, 0.4]]}),  # a string in a vector
        _json({"embeddings": [[True, 0.2, 0.3, 0.4]]}),  # a bool is not a number here
        _json({"embeddings": [None]}),
        _json({"embeddings": "nope"}),
        _json({"vectors": [GOOD]}),  # the key is missing
        _json([GOOD]),  # not an object
        _raw('{"embeddings": [[NaN, 0.2, 0.3, 0.4]]}'),
        _raw('{"embeddings": [[Infinity, 0.2, 0.3, 0.4]]}'),
        _raw('{"embeddings": [[-Infinity, 0.2, 0.3, 0.4]]}'),
        _raw("this is not json"),
    ],
)
def test_an_unusable_embedding_response_is_a_response_error(
    respond: Callable[[httpx.Request], httpx.Response],
) -> None:  # [NEG]
    fake = FakeOllama()
    client = make_client(fake)
    run(ready(client))
    fake.embed_response = respond
    with pytest.raises(EmbeddingResponseError):
        run(client.embed_documents(["t1"]))


def test_ragged_vectors_in_one_response_are_a_response_error() -> None:  # [NEG]
    fake = FakeOllama()
    client = make_client(fake)
    run(ready(client))
    fake.embed_response = _json({"embeddings": [GOOD, [0.1, 0.2, 0.3]]})
    with pytest.raises(EmbeddingResponseError):
        run(client.embed_documents(["t1", "t2"]))


@pytest.mark.parametrize(
    "tags",
    [
        _json({"models": "none"}),
        _json({"models": ["a string"]}),
        _json(["not", "an", "object"]),
        _json({"models": [{"name": "nomic-embed-text:latest", "digest": ""}]}),
        _json({"models": [{"name": "nomic-embed-text:latest"}]}),
        _raw("not json"),
    ],
)
def test_an_unusable_tags_response_is_a_response_error(
    tags: Callable[[httpx.Request], httpx.Response],
) -> None:  # [NEG]
    with pytest.raises(EmbeddingResponseError):
        run(make_client(FakeOllama(tags_response=tags)).ensure_ready())


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, EmbeddingResponseError),  # for example "input is longer than the context"
        (500, EmbeddingUnavailableError),
        (503, EmbeddingUnavailableError),
        (429, EmbeddingUnavailableError),
        (401, EmbeddingUnavailableError),
        (302, EmbeddingUnavailableError),  # redirects are never followed
    ],
)
def test_http_statuses_are_classified(status: int, expected: type[GrcError]) -> None:  # [NEG]
    fake = FakeOllama()
    client = make_client(fake)
    run(ready(client))
    fake.embed_response = lambda request: httpx.Response(
        status, headers={"location": "http://example.com/"}, json={"error": "x"}
    )
    with pytest.raises(expected):
        run(client.embed_documents(["t1"]))
    assert fake.paths[-1] == "/api/embed"  # and no second request followed the redirect


# ----- connection problems and timeouts --------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
        httpx.ConnectTimeout("slow"),
        httpx.RemoteProtocolError("reset"),
    ],
)
def test_connection_problems_and_timeouts_are_unavailable_errors(error: Exception) -> None:  # [NEG]
    def explode(request: httpx.Request) -> httpx.Response:
        raise error

    client = OllamaEmbeddingClient(
        BASE_URL, "nomic-embed-text", expected_dimension=DIM, transport=httpx.MockTransport(explode)
    )
    with pytest.raises(EmbeddingUnavailableError) as caught:
        run(client.ensure_ready())
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_the_first_embed_call_gets_the_longer_timeout() -> None:
    fake = FakeOllama()
    client = make_client(
        fake,
        timeout=httpx.Timeout(connect=2.0, read=3.0, write=4.0, pool=5.0),
        first_call_timeout=httpx.Timeout(connect=2.0, read=77.0, write=4.0, pool=5.0),
    )

    async def scenario() -> None:
        await client.ensure_ready()  # tags (normal timeout), then the probe (first embed call)
        await client.embed_documents(["t1"])
        await client.embed_documents(["t2"])

    run(scenario())
    reads = [(r.url.path, r.extensions["timeout"]["read"]) for r in fake.requests]
    assert reads == [
        ("/api/tags", 3.0),
        ("/api/embed", 77.0),  # loads the model
        ("/api/embed", 3.0),
        ("/api/embed", 3.0),
    ]


def test_the_default_timeouts_are_explicit_and_the_first_call_is_longer() -> None:
    for timeout in (DEFAULT_TIMEOUT, FIRST_CALL_TIMEOUT):
        assert None not in (timeout.connect, timeout.read, timeout.write, timeout.pool)
    assert FIRST_CALL_TIMEOUT.read is not None and DEFAULT_TIMEOUT.read is not None
    assert FIRST_CALL_TIMEOUT.read > DEFAULT_TIMEOUT.read


def test_a_failed_first_call_does_not_use_up_the_long_timeout() -> None:
    state = {"calls": 0}
    seen: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/embed":
            seen.append(request.extensions["timeout"]["read"])
            state["calls"] += 1
            if state["calls"] == 1:
                raise httpx.ReadTimeout("model still loading")
            return httpx.Response(200, json={"embeddings": [GOOD]})
        return httpx.Response(
            200, json={"models": [{"name": "nomic-embed-text:latest", "digest": "d"}]}
        )

    client = OllamaEmbeddingClient(
        BASE_URL,
        "nomic-embed-text",
        expected_dimension=DIM,
        transport=httpx.MockTransport(handler),
        timeout=httpx.Timeout(3.0),
        first_call_timeout=httpx.Timeout(77.0),
    )
    with pytest.raises(EmbeddingUnavailableError):
        run(client.ensure_ready())
    run(client.ensure_ready())
    assert seen == [77.0, 77.0]  # the model had not loaded yet, so the retry is still "first"


# ----- the endpoint allowlist: the runtime control behind "never download models" ---------------

FORBIDDEN = [
    ("POST", "/api/pull"),
    ("GET", "/api/pull"),
    ("POST", "/api/push"),
    ("POST", "/api/create"),
    ("POST", "/api/copy"),
    ("DELETE", "/api/delete"),
    ("POST", "/api/generate"),
    ("POST", "/api/tags"),  # right path, wrong method
    ("GET", "/api/embed"),  # right path, wrong method
    ("POST", "/api/embed?stream=true"),  # any query string
    ("POST", "/api/embed/"),
    ("POST", "/API/embed"),
    ("POST", "/api/embeddings"),
    ("POST", "/api//pull"),
    ("POST", "/api/%70ull"),
    ("POST", "/api/./pull"),
    ("POST", "/api/../api/pull"),
    ("POST", "http://127.0.0.1:11434/api/pull"),  # an absolute URL override
]


def test_the_client_refuses_every_endpoint_but_tags_and_embed() -> None:  # [NEG] allowlist
    fake = FakeOllama()
    client = make_client(fake)

    async def scenario() -> None:
        for method, target in FORBIDDEN:
            with pytest.raises(EndpointNotAllowedError):
                await client._http.request(method, target)
        assert fake.requests == []  # not one request left the process
        await client._http.request("GET", "/api/tags")  # the two allowed ones still work
        await client._http.request("POST", "/api/embed", json={"model": "m", "input": ["t1"]})
        assert fake.paths == ["/api/tags", "/api/embed"]

    run(scenario())


def test_no_client_method_takes_a_path_and_normal_use_touches_only_two_endpoints() -> None:
    fake = FakeOllama()
    client = make_client(fake)

    async def scenario() -> None:
        await client.ensure_ready()
        await client.embed_documents(["t1"])
        await client.embed_query("q")

    run(scenario())
    assert set(fake.paths) == {"/api/tags", "/api/embed"}
    assert {r.method for r in fake.requests} == {"GET", "POST"}


def test_a_non_loopback_url_is_refused() -> None:  # [NEG]
    for url in ("http://example.com:11434", "http://localhost:11434", "https://127.0.0.1:11434"):
        with pytest.raises(NonLoopbackURLError):
            OllamaEmbeddingClient(url, "nomic-embed-text", expected_dimension=DIM)


# ----- Ollama's words never reach an error or a log --------------------------------------------

SENTINEL_RESPONSES = [
    ("embed 400", lambda r: httpx.Response(400, json={"error": f"input too long: {SENTINEL}"})),
    ("embed 500", lambda r: httpx.Response(500, text=SENTINEL)),
    ("embed 404", lambda r: httpx.Response(404, json={"error": SENTINEL})),
    ("embed 200 not json", lambda r: httpx.Response(200, text=f'{{"embeddings": [{SENTINEL}]}}')),
    ("embed 200 string vector", lambda r: httpx.Response(200, json={"embeddings": [[SENTINEL]]})),
    ("embed 200 string list", lambda r: httpx.Response(200, json={"embeddings": SENTINEL})),
    ("embed 200 echoes input", lambda r: httpx.Response(200, json={"error": SENTINEL})),
]


@pytest.mark.parametrize(
    "respond",
    [item[1] for item in SENTINEL_RESPONSES],
    ids=[item[0].replace(" ", "_") for item in SENTINEL_RESPONSES],
)
def test_an_ollama_error_body_never_appears_in_the_error_or_its_chain(
    respond: Callable[[httpx.Request], httpx.Response], caplog: pytest.LogCaptureFixture
) -> None:  # [NEG] mutation: chaining
    caplog.set_level(logging.DEBUG)
    fake = FakeOllama()
    client = make_client(fake)
    run(ready(client))
    fake.embed_response = respond
    with pytest.raises(GrcError) as caught:
        run(client.embed_documents([f"t1 {SENTINEL}"]))  # the input text echoes the sentinel too
    error = caught.value
    assert SENTINEL not in str(error) and SENTINEL not in repr(error)
    assert error.__context__ is None  # `raise ... from None` would still leave this set
    assert error.__cause__ is None
    assert SENTINEL not in caplog.text


@pytest.mark.parametrize(
    "tags",
    [
        lambda r: httpx.Response(200, text=f"not json {SENTINEL}"),
        lambda r: httpx.Response(200, json={"models": [SENTINEL]}),
        lambda r: httpx.Response(500, json={"error": SENTINEL}),
    ],
)
def test_an_ollama_tags_error_never_appears_in_the_error_or_its_chain(
    tags: Callable[[httpx.Request], httpx.Response],
) -> None:  # [NEG]
    with pytest.raises(GrcError) as caught:
        run(make_client(FakeOllama(tags_response=tags)).ensure_ready())
    error = caught.value
    assert SENTINEL not in str(error) and SENTINEL not in repr(error)
    assert error.__context__ is None and error.__cause__ is None


def test_every_error_has_a_fixed_message() -> None:
    assert str(EmbeddingResponseError()) == "Embedding response was not usable"
    assert str(EmbeddingUnavailableError()) == "Embedding service is unavailable"
    assert str(EmbeddingDimensionError()) == "Embedding dimension is not the expected one"
    assert str(EndpointNotAllowedError()) == "This Ollama endpoint is not allowed"
