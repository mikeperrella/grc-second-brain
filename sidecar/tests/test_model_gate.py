"""The model gate (CLAUDE.md 3.7, ADR-011), against a fake Ollama (httpx.MockTransport).

The gate is driven through the real `OllamaEmbeddingClient`, so the tests also see the exact
requests and timeouts that would leave the process. Nothing here needs a network or a model.
"""

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

import httpx
import pytest

from grc_sidecar.errors import ModelsUnavailableError
from grc_sidecar.llm.model_gate import RECHECK_MIN_INTERVAL, ModelGate
from grc_sidecar.llm.ollama_embeddings import TAGS_CHECK_TIMEOUT, OllamaEmbeddingClient

from .clockkit import ManualClock

BASE_URL = "http://127.0.0.1:11434"
EMBEDDING = "nomic-embed-text"
GENERATION = "llama3.1:8b"
REQUIRED = (EMBEDDING, GENERATION)

T = TypeVar("T")


def run(coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


class FakeOllama:
    """A scriptable Ollama that records every request, and how many were in flight at once."""

    def __init__(
        self,
        models: tuple[str, ...] = (f"{EMBEDDING}:latest", GENERATION),
        *,
        body: Callable[[], httpx.Response] | None = None,
        error: Callable[[httpx.Request], Exception] | None = None,
        hold: asyncio.Event | None = None,
    ) -> None:
        self.models = list(models)
        self.body = body
        self.error = error
        self.hold = hold
        self.requests: list[httpx.Request] = []
        self.in_flight = 0
        self.max_in_flight = 0

    @property
    def tags_requests(self) -> int:
        return sum(1 for r in self.requests if r.url.path == "/api/tags")

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.hold is not None:
                await self.hold.wait()
            if self.error is not None:
                raise self.error(request)
            if self.body is not None:
                return self.body()
            entries = [{"name": m, "model": m, "digest": "sha256:abc"} for m in self.models]
            return httpx.Response(200, json={"models": entries})
        finally:
            self.in_flight -= 1


def make_gate(fake: FakeOllama, clock: ManualClock | None = None) -> tuple[ModelGate, ManualClock]:
    clock = clock or ManualClock()
    client = OllamaEmbeddingClient(
        BASE_URL, EMBEDDING, expected_dimension=4, transport=httpx.MockTransport(fake)
    )
    return ModelGate(client, REQUIRED, clock), clock


def only_tags_were_requested(fake: FakeOllama) -> bool:
    return all((r.method, r.url.path) == ("GET", "/api/tags") for r in fake.requests)


# ----- the five situations the brief names ---------------------------------------------------


def test_both_models_present_is_ready() -> None:
    fake = FakeOllama()
    gate, _ = make_gate(fake)
    status = run(gate.refresh())
    assert (status.state, status.models_missing, status.ollama) == ("ready", (), "reachable")
    run(gate.require(EMBEDDING, GENERATION))  # does not raise
    assert only_tags_were_requested(fake)


@pytest.mark.parametrize(
    ("installed", "missing"),
    [
        ((GENERATION,), (EMBEDDING,)),
        ((f"{EMBEDDING}:latest",), (GENERATION,)),
        (("some-other-model:latest",), (EMBEDDING, GENERATION)),
        ((), (EMBEDDING, GENERATION)),
    ],
)
def test_missing_models_are_reported_and_refused(  # [NEG]
    installed: tuple[str, ...], missing: tuple[str, ...]
) -> None:
    fake = FakeOllama(installed)
    gate, _ = make_gate(fake)
    status = run(gate.refresh())
    assert (status.state, status.models_missing, status.ollama) == (
        "degraded",
        missing,
        "reachable",
    )
    for model in missing:
        with pytest.raises(ModelsUnavailableError) as info:
            run(gate.require(model))
        assert info.value.missing == (model,)
    assert only_tags_were_requested(fake)  # no embed or generate request was ever sent


def test_the_gate_is_per_model_a_missing_generation_model_does_not_block_embedding() -> None:
    fake = FakeOllama((f"{EMBEDDING}:latest",))
    gate, _ = make_gate(fake)
    run(gate.refresh())
    run(gate.require(EMBEDDING))
    with pytest.raises(ModelsUnavailableError):
        run(gate.require(GENERATION))


def test_ollama_unreachable_is_degraded_and_refuses() -> None:  # [NEG]
    fake = FakeOllama(error=lambda r: httpx.ConnectError("refused", request=r))
    gate, _ = make_gate(fake)
    status = run(gate.refresh())
    assert (status.state, status.models_missing, status.ollama) == (
        "degraded",
        REQUIRED,
        "unreachable",
    )
    with pytest.raises(ModelsUnavailableError):
        run(gate.require(EMBEDDING))
    assert only_tags_were_requested(fake)


def test_a_really_refused_connection_is_unreachable() -> None:  # [NEG]
    # Port 1 on loopback has no listener: a real "connection refused", no mock involved.
    client = OllamaEmbeddingClient("http://127.0.0.1:1", EMBEDDING, expected_dimension=4)
    gate = ModelGate(client, REQUIRED, ManualClock())

    async def scenario() -> tuple[str, str]:
        try:
            status = await gate.refresh()
            with pytest.raises(ModelsUnavailableError):
                await gate.require(EMBEDDING)
            return status.state, status.ollama
        finally:
            await client.aclose()

    assert run(scenario()) == ("degraded", "unreachable")


@pytest.mark.parametrize(
    "body",
    [
        lambda: httpx.Response(200, content=b"not json"),
        lambda: httpx.Response(200, json=[]),
        lambda: httpx.Response(200, json={"models": "x"}),
        lambda: httpx.Response(200, json={"nope": []}),
        lambda: httpx.Response(200, json={"models": [1]}),
        lambda: httpx.Response(200, json={"models": [{"digest": "sha256:abc"}]}),  # no name
        lambda: httpx.Response(200, json={"models": [{"name": 7}]}),
    ],
    ids=[
        "not-json",
        "array",
        "models-not-list",
        "no-models",
        "entry-not-object",
        "no-name",
        "name-int",
    ],
)
def test_a_malformed_listing_is_degraded_and_refuses(  # [NEG]
    body: Callable[[], httpx.Response],
) -> None:
    fake = FakeOllama(body=body)
    gate, _ = make_gate(fake)
    status = run(gate.refresh())
    assert (status.state, status.models_missing, status.ollama) == (
        "degraded",
        REQUIRED,
        "bad_response",
    )
    with pytest.raises(ModelsUnavailableError):
        run(gate.require(EMBEDDING))
    assert only_tags_were_requested(fake)


def test_a_server_error_is_unreachable_and_refuses() -> None:  # [NEG]
    fake = FakeOllama(body=lambda: httpx.Response(500, json={"error": "boom"}))
    gate, _ = make_gate(fake)
    assert run(gate.refresh()).ollama == "unreachable"


def test_the_status_never_contains_response_text() -> None:  # [NEG]
    secret = "CONFIDENTIAL RESPONSE TEXT"
    fake = FakeOllama(body=lambda: httpx.Response(200, json={"models": [], "error": secret}))
    gate, _ = make_gate(fake)
    run(gate.refresh())
    assert secret not in gate.status.model_dump_json()


# ----- before the first check ------------------------------------------------------------------


def test_the_status_before_any_check_is_checking_and_makes_no_request() -> None:
    fake = FakeOllama()
    gate, _ = make_gate(fake)
    assert gate.status.state == "checking"
    assert fake.requests == []


def test_require_before_the_first_check_performs_it() -> None:
    fake = FakeOllama()
    gate, _ = make_gate(fake)
    run(gate.require(EMBEDDING))
    assert fake.tags_requests == 1
    assert gate.status.state == "ready"


# ----- the short timeout (mutation row 27) -----------------------------------------------------


def test_the_tags_check_uses_the_short_timeout_and_reports_unreachable() -> None:  # [NEG]
    # The fake raises a read timeout at once, so no wall time passes. What matters is the timeout
    # the request was sent with: the gate's own short one, not the client's 30 s default.
    fake = FakeOllama(error=lambda r: httpx.ReadTimeout("slow", request=r))
    gate, _ = make_gate(fake)
    status = run(gate.refresh())
    assert status.state == "degraded"
    assert status.ollama == "unreachable"
    assert len(fake.requests) == 1
    assert fake.requests[0].extensions["timeout"] == TAGS_CHECK_TIMEOUT.as_dict()
    assert only_tags_were_requested(fake)  # and no embed request


def test_the_short_timeout_is_really_short() -> None:
    timeout = TAGS_CHECK_TIMEOUT.as_dict()
    assert timeout["read"] is not None
    assert timeout["read"] <= 5.0
    assert timeout["connect"] is not None
    assert timeout["connect"] <= 5.0


# ----- the minimum interval (mutation rows 25 and 15) ------------------------------------------


def test_recheck_respects_the_minimum_interval() -> None:
    fake = FakeOllama((f"{EMBEDDING}:latest",))  # generation stays missing
    gate, clock = make_gate(fake)
    run(gate.refresh())
    assert fake.tags_requests == 1

    clock.advance(RECHECK_MIN_INTERVAL - 0.5)
    for _ in range(3):
        with pytest.raises(ModelsUnavailableError):
            run(gate.require(GENERATION))
    assert fake.tags_requests == 1, "re-checks inside the interval must not reach Ollama"

    clock.advance(0.5)  # now exactly one interval since the last check
    with pytest.raises(ModelsUnavailableError):
        run(gate.require(GENERATION))
    assert fake.tags_requests == 2


def test_gate_recovers_after_model_appears() -> None:
    fake = FakeOllama((f"{EMBEDDING}:latest",))
    gate, clock = make_gate(fake)
    run(gate.refresh())
    with pytest.raises(ModelsUnavailableError):
        run(gate.require(GENERATION))

    fake.models.append(GENERATION)  # the user installs the model in Ollama
    clock.advance(RECHECK_MIN_INTERVAL)  # past the interval, so the re-check is allowed
    run(gate.require(GENERATION))  # does not raise
    assert gate.status.state == "ready"
    assert gate.status.models_missing == ()


def test_a_model_that_appears_is_not_seen_inside_the_interval() -> None:
    fake = FakeOllama((f"{EMBEDDING}:latest",))
    gate, clock = make_gate(fake)
    run(gate.refresh())
    fake.models.append(GENERATION)
    clock.advance(1.0)
    with pytest.raises(ModelsUnavailableError):
        run(gate.require(GENERATION))
    assert fake.tags_requests == 1


def test_refresh_also_respects_the_interval() -> None:
    fake = FakeOllama()
    gate, clock = make_gate(fake)
    run(gate.refresh())
    run(gate.refresh())
    assert fake.tags_requests == 1
    clock.advance(RECHECK_MIN_INTERVAL)
    run(gate.refresh())
    assert fake.tags_requests == 2


# ----- the lock (mutation row 26) --------------------------------------------------------------


def test_background_and_on_demand_checks_never_overlap() -> None:
    async def scenario() -> FakeOllama:
        hold = asyncio.Event()
        fake = FakeOllama((f"{EMBEDDING}:latest",), hold=hold)  # generation missing
        gate, _ = make_gate(fake)

        background = asyncio.create_task(gate.refresh())  # the startup check
        waiters = [asyncio.create_task(gate.require(GENERATION)) for _ in range(2)]
        for _ in range(200):  # let every task run until it is blocked on the held request or lock
            await asyncio.sleep(0)
        assert fake.in_flight >= 1, "the first check must be in flight, held open"
        hold.set()
        await background
        results = await asyncio.gather(*waiters, return_exceptions=True)
        assert all(isinstance(r, ModelsUnavailableError) for r in results)
        return fake

    fake = run(scenario())
    assert fake.max_in_flight == 1, "two checks were in flight at the same time"
    assert fake.tags_requests == 1, "queued callers must reuse the check they waited for"


# ----- never a model request -------------------------------------------------------------------


def test_the_gate_never_calls_anything_but_tags() -> None:  # [NEG]
    fake = FakeOllama((f"{EMBEDDING}:latest",))
    gate, clock = make_gate(fake)
    for _ in range(3):
        run(gate.refresh())
        with pytest.raises(ModelsUnavailableError):
            run(gate.require(GENERATION))
        clock.advance(RECHECK_MIN_INTERVAL)
    assert fake.requests
    assert only_tags_were_requested(fake)
    assert all(r.url.query == b"" for r in fake.requests)


def test_model_dependent_call_refuses_when_degraded() -> None:  # [NEG] mutation row 14
    fake = FakeOllama(())
    gate, _ = make_gate(fake)
    run(gate.refresh())
    ran = False

    async def model_call() -> None:
        nonlocal ran
        await gate.require(EMBEDDING)  # the gate comes first
        ran = True  # stands for "send the request to the model"

    with pytest.raises(ModelsUnavailableError):
        run(model_call())
    assert not ran
    assert all(r.url.path != "/api/embed" for r in fake.requests)
