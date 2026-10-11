"""The restricted generation client (ADR-012), against a fake Ollama (httpx.MockTransport).

No network and no model. Each control has a test built so that only that control can make it pass
(the mutation proofs in ADR-012 break each control in turn and name the test that must fail).
"""

import asyncio
import inspect
import json
import logging
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

import httpx
import pytest

from grc_sidecar.errors import (
    ContextWindowReachedError,
    EndpointNotAllowedError,
    GenerationRejectedError,
    GenerationResponseError,
    GenerationUnavailableError,
    GrcError,
    ModelMissingError,
    ModelsUnavailableError,
    NonLocalModelError,
    NonLoopbackURLError,
    OutputTruncatedError,
    PromptTooLargeError,
)
from grc_sidecar.llm import limits
from grc_sidecar.llm.generation_client import (
    DEFAULT_TIMEOUT,
    FIRST_CALL_TIMEOUT,
    GenerationClient,
)
from grc_sidecar.llm.model_gate import ModelGate
from grc_sidecar.llm.ollama_embeddings import TAGS_CHECK_TIMEOUT

from .clockkit import ManualClock
from .retrievalkit import FakeLister

BASE_URL = "http://127.0.0.1:11434"
MODEL = "llama3.1:8b"
SENTINEL = "CONFIDENTIAL POLICY TEXT"
SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}

T = TypeVar("T")


def run(coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def reply(**overrides: object) -> dict[str, object]:
    """A normal, finished chat reply. Pass a key with None to remove it."""
    body: dict[str, object] = {
        "model": MODEL,
        "created_at": "2026-10-10T10:00:00Z",
        "message": {"role": "assistant", "content": '{"answer": "ok"}'},
        "done": True,
        "done_reason": "stop",
        "total_duration": 5_000_000_000,
        "load_duration": 1_000_000_000,
        "prompt_eval_count": 120,
        "prompt_eval_duration": 2_000_000_000,
        "eval_count": 15,
        "eval_duration": 1_500_000_000,
    }
    body.update(overrides)
    return {key: value for key, value in body.items() if value is not None}


class FakeOllama:
    """A scriptable Ollama that records every request it receives."""

    def __init__(
        self,
        *,
        chat: Callable[[httpx.Request], httpx.Response] | None = None,
        tags: Callable[[httpx.Request], httpx.Response] | None = None,
    ) -> None:
        self.chat = chat
        self.tags = tags
        self.requests: list[httpx.Request] = []

    @property
    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]

    def chat_bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests if r.url.path == "/api/chat"]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/api/chat":
            if self.chat is not None:
                return self.chat(request)
            return httpx.Response(200, json=reply())
        if request.url.path == "/api/tags":
            if self.tags is not None:
                return self.tags(request)
            entry = {"name": MODEL, "model": MODEL, "digest": "sha256:model-digest"}
            return httpx.Response(200, json={"models": [entry]})
        return httpx.Response(404, json={"error": "not found"})


def make_gate(models: tuple[str, ...] | None = (MODEL,), **lister: Any) -> ModelGate:
    return ModelGate(FakeLister(models, **lister), (MODEL,), ManualClock())


def make_client(
    fake: FakeOllama, gate: ModelGate | None = None, model: str = MODEL, **kwargs: Any
) -> GenerationClient:
    return GenerationClient(
        BASE_URL,
        model,
        gate or make_gate(),
        transport=httpx.MockTransport(fake),
        **kwargs,
    )


def generate(client: GenerationClient, system: str = "S", user: str = "U") -> Any:
    return run(client.generate(system, user, SCHEMA))


# ----- the request: exactly what leaves the process ---------------------------------------------


def test_one_chat_request_with_the_exact_body() -> None:
    fake = FakeOllama()
    result = generate(make_client(fake), "the instructions", "the question and sources")
    assert [(r.method, r.url.path) for r in fake.requests] == [("POST", "/api/chat")]
    assert fake.chat_bodies() == [
        {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": "the instructions"},
                {"role": "user", "content": "the question and sources"},
            ],
            "stream": False,
            "format": SCHEMA,
            "shift": False,
            "options": {
                "temperature": 0,
                "seed": limits.SEED,
                "num_ctx": limits.NUM_CTX,
                "num_predict": limits.NUM_PREDICT,
            },
        }
    ]
    assert result.content == '{"answer": "ok"}'
    assert (result.prompt_eval_count, result.eval_count, result.done_reason) == (120, 15, "stop")
    assert result.total_duration == 5_000_000_000 and result.load_duration == 1_000_000_000


def test_every_request_is_exactly_one_system_and_one_user_message() -> None:  # [NEG]
    """Ollama drops messages from the front of a long conversation (ADR-012, row 4). A second
    attempt must therefore be a rebuilt user message, never an assistant turn plus a new one."""
    fake = FakeOllama()
    client = make_client(fake)
    for attempt, user in enumerate(("sources and question", "sources, question and error")):
        generate(client, f"instructions {attempt}", user)
    bodies = fake.chat_bodies()
    assert len(bodies) == 2
    for body in bodies:
        assert [m["role"] for m in body["messages"]] == ["system", "user"]
        assert set(body["messages"][0]) == set(body["messages"][1]) == {"role", "content"}


def test_the_client_cannot_be_given_a_conversation() -> None:  # [NEG]
    parameters = list(inspect.signature(GenerationClient.generate).parameters)
    assert parameters == ["self", "system", "user", "schema"]


def test_every_request_carries_the_schema_in_format() -> None:  # [NEG]
    fake = FakeOllama()
    client = make_client(fake)
    for _ in range(3):
        generate(client)
    assert [body["format"] for body in fake.chat_bodies()] == [SCHEMA, SCHEMA, SCHEMA]


def test_every_request_disables_context_shifting() -> None:  # [NEG]
    fake = FakeOllama()
    client = make_client(fake)
    generate(client)
    generate(client)
    assert [body["shift"] for body in fake.chat_bodies()] == [False, False]


def test_decoding_options_come_from_the_derived_limits() -> None:
    fake = FakeOllama()
    generate(make_client(fake))
    options = fake.chat_bodies()[0]["options"]
    assert options == {"temperature": 0, "seed": 42, "num_ctx": 11_264, "num_predict": 1_900}


# ----- the endpoint allowlist --------------------------------------------------------------------

FORBIDDEN = [
    ("POST", "/api/pull"),
    ("GET", "/api/pull"),
    ("POST", "/api/push"),
    ("POST", "/api/create"),
    ("POST", "/api/copy"),
    ("DELETE", "/api/delete"),
    ("POST", "/api/show"),
    ("POST", "/api/generate"),
    ("POST", "/api/embed"),  # the embedding client's endpoint, not this one's
    ("GET", "/api/chat"),  # right path, wrong method
    ("POST", "/api/tags"),  # right path, wrong method
    ("POST", "/api/chat?stream=true"),  # any query string
    ("GET", "/api/tags?x=1"),
    ("POST", "/api/chat/"),
    ("POST", "/API/chat"),
    ("POST", "/api//chat"),
    ("POST", "/api/./pull"),  # httpx normalises this to the forbidden /api/pull
    ("POST", "/api/../api/pull"),
    ("POST", "http://127.0.0.1:11434/api/pull"),  # an absolute URL override
]


def test_the_client_refuses_every_endpoint_but_chat_and_tags() -> None:  # [NEG] allowlist
    fake = FakeOllama()
    client = make_client(fake)

    async def scenario() -> None:
        for method, target in FORBIDDEN:
            with pytest.raises(EndpointNotAllowedError):
                await client._http.request(method, target)
        assert fake.requests == []  # not one request left the process
        await client._http.request("GET", "/api/tags")  # the two allowed ones still work
        await client._http.request("POST", "/api/chat", json={})
        assert fake.paths == ["/api/tags", "/api/chat"]

    run(scenario())


def test_the_hook_judges_the_path_that_is_actually_sent() -> None:  # [NEG]
    """httpx removes dot segments before the request hook runs, so `/api/./chat` is sent, and
    judged, as `/api/chat`; the forbidden spellings above normalise to forbidden paths. What is
    checked is always what leaves the process."""
    fake = FakeOllama()
    client = make_client(fake)

    async def scenario() -> None:
        await client._http.request("POST", "/api/./chat", json={})
        await client._http.request("POST", "/api/x/../chat", json={})
        for target in ("/api/./pull", "/api/x/../pull", "/api/chat/../pull"):
            with pytest.raises(EndpointNotAllowedError):
                await client._http.request("POST", target)

    run(scenario())
    assert fake.paths == ["/api/chat", "/api/chat"]


def test_normal_use_touches_only_the_chat_and_tags_endpoints() -> None:
    fake = FakeOllama()
    client = make_client(fake)
    generate(client)
    run(client.read_model_entry())
    assert fake.paths == ["/api/chat", "/api/tags"]


def test_a_non_loopback_url_is_refused() -> None:  # [NEG]
    for url in ("http://example.com:11434", "http://localhost:11434", "https://127.0.0.1:11434"):
        with pytest.raises(NonLoopbackURLError):
            GenerationClient(url, MODEL, make_gate())


# ----- the gate comes first ----------------------------------------------------------------------


def test_no_request_is_sent_when_the_model_is_not_installed() -> None:  # [NEG]
    fake = FakeOllama()
    client = make_client(fake, make_gate(models=()))
    with pytest.raises(ModelsUnavailableError) as info:
        generate(client)
    assert info.value.missing == (MODEL,)
    assert fake.requests == []  # the chat endpoint was never contacted


def test_no_request_is_sent_when_ollama_is_unreachable() -> None:  # [NEG]
    fake = FakeOllama()
    client = make_client(fake, make_gate(models=None))
    with pytest.raises(ModelsUnavailableError):
        generate(client)
    assert fake.requests == []


@pytest.mark.parametrize(
    ("model", "listed", "remote"),
    [
        ("llama3.1:cloud", ("llama3.1:cloud",), ()),  # cloud name, listed as an ordinary entry
        ("gpt-oss:20b-cloud", ("gpt-oss:20b-cloud",), ()),  # the "-cloud" tag form
        (MODEL, (MODEL,), (MODEL,)),  # a local-looking name listed with a remote host
    ],
)
def test_a_cloud_or_remote_model_is_refused_before_any_request(
    model: str, listed: tuple[str, ...], remote: tuple[str, ...]
) -> None:  # [NEG] the gate's non-local refusal reaches generation too
    fake = FakeOllama()
    gate = ModelGate(FakeLister(listed, remote=remote), (model,), ManualClock())
    client = make_client(fake, gate, model=model)
    with pytest.raises(ModelsUnavailableError) as info:
        generate(client)
    assert info.value.missing == (model,)
    assert fake.requests == []


def test_a_reply_that_names_a_remote_host_is_a_tripwire() -> None:  # [NEG]
    for key in ("remote_host", "remote_model"):
        fake = FakeOllama(chat=lambda r, key=key: httpx.Response(200, json=reply(**{key: "x"})))
        with pytest.raises(NonLocalModelError):
            generate(make_client(fake))


def test_empty_remote_fields_in_a_reply_are_not_a_tripwire() -> None:
    fake = FakeOllama(
        chat=lambda r: httpx.Response(200, json=reply(remote_host="", remote_model=""))
    )
    assert generate(make_client(fake)).done_reason == "stop"


# ----- the size caps ---------------------------------------------------------------------------


def test_oversize_messages_are_refused_before_anything_is_sent() -> None:  # [NEG]
    fake = FakeOllama()
    client = make_client(fake)
    with pytest.raises(PromptTooLargeError):
        generate(client, "s" * 5_000, "u" * (limits.MAX_MESSAGE_CHARS - 5_000 + 1))
    assert fake.requests == []


def test_messages_at_exactly_the_cap_are_sent() -> None:
    fake = FakeOllama()
    generate(make_client(fake), "s" * 5_000, "u" * (limits.MAX_MESSAGE_CHARS - 5_000))
    assert len(fake.chat_bodies()) == 1


# ----- fail-closed checks on the reply ---------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"done_reason": "length"}, OutputTruncatedError),
        ({"done_reason": "load"}, GenerationResponseError),
        ({"done_reason": None}, GenerationResponseError),
        ({"done_reason": 3}, GenerationResponseError),
        ({"done": False}, GenerationResponseError),
        ({"done": None}, GenerationResponseError),
        ({"message": None}, GenerationResponseError),
        ({"message": {"role": "assistant"}}, GenerationResponseError),
        ({"message": {"role": "assistant", "content": 5}}, GenerationResponseError),
        ({"prompt_eval_count": -1}, GenerationResponseError),
        ({"prompt_eval_count": True}, GenerationResponseError),
        ({"eval_count": "15"}, GenerationResponseError),
        ({"total_duration": 1.5}, GenerationResponseError),
    ],
)
def test_an_unusable_reply_fails_closed(
    overrides: dict[str, object], error: type[GrcError]
) -> None:
    fake = FakeOllama(chat=lambda r: httpx.Response(200, json=reply(**overrides)))
    with pytest.raises(error):
        generate(make_client(fake))


def test_a_reply_that_is_not_an_object_is_refused() -> None:  # [NEG]
    for payload in ([], "text", 3, None):
        fake = FakeOllama(chat=lambda r, p=payload: httpx.Response(200, json=p))
        with pytest.raises(GenerationResponseError):
            generate(make_client(fake))


WINDOW_LIMIT = limits.NUM_CTX - limits.NUM_PREDICT


@pytest.mark.parametrize(
    ("prompt", "cached", "refused"),
    [
        (WINDOW_LIMIT - 1, None, False),  # just below
        (WINDOW_LIMIT, None, True),  # reaches it
        (limits.NUM_CTX, None, True),  # a cut prompt reports about the window size
        (WINDOW_LIMIT - 100, 99, False),  # cached tokens counted: still below
        (WINDOW_LIMIT - 100, 100, True),  # counted together they reach it
    ],
)
def test_a_prompt_count_that_reaches_the_window_fails_closed(
    prompt: int, cached: int | None, refused: bool
) -> None:  # [NEG]
    fake = FakeOllama(
        chat=lambda r: httpx.Response(
            200, json=reply(prompt_eval_count=prompt, prompt_eval_cached_count=cached)
        )
    )
    client = make_client(fake)
    if refused:
        with pytest.raises(ContextWindowReachedError):
            generate(client)
    else:
        assert generate(client).prompt_eval_count == prompt


def test_missing_counts_are_reported_as_none_not_zero() -> None:
    fake = FakeOllama(
        chat=lambda r: httpx.Response(
            200, json=reply(prompt_eval_count=None, eval_count=None, load_duration=None)
        )
    )
    result = generate(make_client(fake))
    assert result.prompt_eval_count is None and result.eval_count is None
    assert result.load_duration is None


# ----- Ollama's words never reach an error or a log -------------------------------------------

SENTINEL_RESPONSES = [
    ("chat 400", lambda r: httpx.Response(400, json={"error": f"too long: {SENTINEL}"})),
    ("chat 404", lambda r: httpx.Response(404, json={"error": f"model {SENTINEL}"})),
    ("chat 403", lambda r: httpx.Response(403, json={"error": SENTINEL})),
    ("chat 500", lambda r: httpx.Response(500, text=SENTINEL)),
    ("chat 200 not json", lambda r: httpx.Response(200, text=f'{{"message": {SENTINEL}')),
    ("chat 200 wrong shape", lambda r: httpx.Response(200, json={"message": SENTINEL})),
    (
        "chat 200 remote",
        lambda r: httpx.Response(200, json=reply(remote_host=SENTINEL)),
    ),
    (
        "chat 200 bad reason",
        lambda r: httpx.Response(200, json=reply(done_reason=SENTINEL)),
    ),
]


@pytest.mark.parametrize(
    "respond",
    [item[1] for item in SENTINEL_RESPONSES],
    ids=[item[0].replace(" ", "_") for item in SENTINEL_RESPONSES],
)
def test_an_ollama_error_body_never_appears_in_the_error_or_its_chain(
    respond: Callable[[httpx.Request], httpx.Response], caplog: pytest.LogCaptureFixture
) -> None:  # [NEG] mutation: include the response text, or chain the original error
    caplog.set_level(logging.DEBUG)
    fake = FakeOllama(chat=respond)
    with pytest.raises(GrcError) as caught:
        generate(make_client(fake), f"system {SENTINEL}", f"user {SENTINEL}")
    error = caught.value
    assert SENTINEL not in str(error) and SENTINEL not in repr(error)
    assert error.__context__ is None  # `raise ... from None` would still leave this set
    assert error.__cause__ is None
    assert SENTINEL not in caplog.text


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (400, GenerationRejectedError),
        (404, ModelMissingError),
        (403, GenerationUnavailableError),
        (500, GenerationUnavailableError),
        (503, GenerationUnavailableError),
    ],
)
def test_http_statuses_map_to_typed_errors(status: int, error: type[GrcError]) -> None:
    fake = FakeOllama(chat=lambda r: httpx.Response(status, json={"error": "x"}))
    with pytest.raises(error):
        generate(make_client(fake))


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError(f"connection refused {SENTINEL}"),
        httpx.ReadTimeout(f"timed out {SENTINEL}"),
        httpx.RemoteProtocolError(f"reset {SENTINEL}"),
        httpx.TooManyRedirects(f"loop {SENTINEL}"),
    ],
)
def test_connection_problems_are_unavailable_errors_without_the_original(
    failure: Exception,
) -> None:  # [NEG]
    def boom(request: httpx.Request) -> httpx.Response:
        raise failure

    with pytest.raises(GenerationUnavailableError) as caught:
        generate(make_client(FakeOllama(chat=boom)))
    assert SENTINEL not in str(caught.value) and SENTINEL not in repr(caught.value)
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_a_redirect_is_never_followed() -> None:  # [NEG]
    fake = FakeOllama(
        chat=lambda r: httpx.Response(307, headers={"location": "http://example.com"})
    )
    with pytest.raises(GenerationUnavailableError):
        generate(make_client(fake))
    assert fake.paths == ["/api/chat"]


def test_every_error_has_a_fixed_message() -> None:
    assert str(GenerationUnavailableError()) == "Generation service is unavailable"
    assert str(GenerationResponseError()) == "Generation response was not usable"
    assert str(GenerationRejectedError()) == "Ollama rejected the generation request"
    assert str(PromptTooLargeError()) == "Prompt is larger than the generation limits"
    assert str(ContextWindowReachedError()) == "Prompt reached the context window"
    assert str(OutputTruncatedError()) == "Generation output was cut off"
    assert str(NonLocalModelError()) == "The model answered from a non-local host"


# ----- timeouts ----------------------------------------------------------------------------------


def test_the_default_timeouts_are_explicit_and_the_first_call_is_longer() -> None:
    for timeout in (DEFAULT_TIMEOUT, FIRST_CALL_TIMEOUT):
        assert None not in (timeout.connect, timeout.read, timeout.write, timeout.pool)
    assert FIRST_CALL_TIMEOUT.read is not None and DEFAULT_TIMEOUT.read is not None
    assert FIRST_CALL_TIMEOUT.read > DEFAULT_TIMEOUT.read


def test_the_first_call_gets_the_longer_timeout_and_later_calls_the_normal_one() -> None:
    fake = FakeOllama()
    client = make_client(
        fake,
        timeout=httpx.Timeout(connect=2.0, read=3.0, write=4.0, pool=5.0),
        first_call_timeout=httpx.Timeout(connect=2.0, read=77.0, write=4.0, pool=5.0),
    )
    generate(client)
    generate(client)
    reads = [r.extensions["timeout"]["read"] for r in fake.requests]
    assert reads == [77.0, 3.0]  # [NEG] a client with no per-request timeout would send 5.0 twice


def test_a_failed_first_call_does_not_use_up_the_long_timeout() -> None:  # [NEG]
    seen: list[float] = []

    def chat(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"]["read"])
        return httpx.Response(500) if len(seen) == 1 else httpx.Response(200, json=reply())

    client = make_client(
        FakeOllama(chat=chat),
        timeout=httpx.Timeout(3.0),
        first_call_timeout=httpx.Timeout(77.0),
    )
    with pytest.raises(GenerationUnavailableError):
        generate(client)
    generate(client)
    assert seen == [77.0, 77.0]  # the model had not loaded yet, so the retry is still "first"


def test_the_digest_listing_uses_the_short_timeout() -> None:
    fake = FakeOllama()
    client = make_client(fake, timeout=httpx.Timeout(99.0), first_call_timeout=httpx.Timeout(99.0))
    run(client.read_model_entry())
    tags = fake.requests[0]
    assert tags.extensions["timeout"]["read"] == TAGS_CHECK_TIMEOUT.read == 3.0


# ----- the digest read ---------------------------------------------------------------------------


def test_read_model_entry_returns_the_local_entry_with_its_digest() -> None:
    entry = run(make_client(FakeOllama()).read_model_entry())
    assert entry.digest == "sha256:model-digest" and entry.remote is False


@pytest.mark.parametrize(
    "tags",
    [
        {"models": []},
        {"models": [{"name": "other:latest", "model": "other:latest", "digest": "sha256:x"}]},
        {"models": [{"name": MODEL, "model": MODEL, "digest": "sha256:x", "remote_host": "h"}]},
        {"models": [{"name": MODEL, "model": MODEL, "digest": "sha256:x", "remote_model": "m"}]},
    ],
)
def test_read_model_entry_refuses_a_missing_or_remote_model(tags: dict[str, object]) -> None:
    fake = FakeOllama(tags=lambda r: httpx.Response(200, json=tags))
    with pytest.raises(ModelMissingError):  # [NEG]
        run(make_client(fake).read_model_entry())


@pytest.mark.parametrize(
    "tags",
    [
        lambda r: httpx.Response(200, text=f"not json {SENTINEL}"),
        lambda r: httpx.Response(200, json={"models": [SENTINEL]}),
        lambda r: httpx.Response(200, json={"models": SENTINEL}),
    ],
)
def test_read_model_entry_with_a_bad_listing_never_echoes_it(
    tags: Callable[[httpx.Request], httpx.Response],
) -> None:  # [NEG]
    with pytest.raises(GenerationResponseError) as caught:
        run(make_client(FakeOllama(tags=tags)).read_model_entry())
    assert SENTINEL not in str(caught.value) and SENTINEL not in repr(caught.value)
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_read_model_entry_with_an_unreachable_ollama_is_unavailable() -> None:
    fake = FakeOllama(tags=lambda r: httpx.Response(500))
    with pytest.raises(GenerationUnavailableError):
        run(make_client(fake).read_model_entry())
