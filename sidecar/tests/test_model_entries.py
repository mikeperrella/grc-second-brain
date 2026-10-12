"""Which models count as local (ADR-012): the cloud-name rule, the listing parser and the lookup.

Ollama forwards a cloud-named model, and a model whose listing carries `remote_host` or
`remote_model`, to ollama.com. Everything here is about refusing those before a request is sent.
"""

import asyncio
from typing import Any

import httpx
import pytest

from grc_sidecar.errors import EmbeddingResponseError, ModelMissingError
from grc_sidecar.llm.ollama_embeddings import (
    ModelEntry,
    OllamaEmbeddingClient,
    find_local_entry,
    is_cloud_model_name,
    parse_model_entries,
)

from .test_embeddings_ollama import BASE_URL, FakeOllama, make_client, run

# ----- the cloud-name rule (internal/modelref/modelref.go:99-117 at the v0.40.3 commit) -----------


@pytest.mark.parametrize(
    "name",
    [
        "llama3.1:cloud",
        "gpt-oss:20b-cloud",
        "gpt-oss:20b:cloud",  # only the text after the LAST colon counts
        "llama3.1:CLOUD",  # the comparison is case-insensitive
        "gpt-oss:20b-Cloud",
        "llama3.1: cloud ",  # the tag is trimmed
        "library/llama3.1:cloud",
        "registry.example/team/model:v1-cloud",
    ],
)
def test_cloud_names_are_recognised(name: str) -> None:
    assert is_cloud_model_name(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "llama3.1:8b",
        "llama3.1",
        "llama3.1-cloud",  # no colon: Ollama treats this as an ordinary local name
        "nomic-embed-text:latest",
        "cloudy:latest",
        "model:cloudy",
        "model:local",  # explicit local source
        "model:a/b-cloud",  # a slash in the tag: the rule does not apply
        "",
    ],
)
def test_local_names_are_not_cloud(name: str) -> None:
    assert is_cloud_model_name(name) is False


# ----- parse_model_entries ---------------------------------------------------------------------


def listing(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"models": list(entries)}


def entry(name: str = "m:latest", **extra: Any) -> dict[str, Any]:
    return {"name": name, "model": name, "digest": "sha256:abc", **extra}


def test_a_plain_entry_is_local_with_its_digest() -> None:
    (parsed,) = parse_model_entries(listing(entry("m:latest")))
    assert parsed == ModelEntry(names=frozenset({"m:latest"}), digest="sha256:abc", remote=False)


@pytest.mark.parametrize(
    "extra", [{}, {"remote_host": ""}, {"remote_model": None}, {"remote_host": None}]
)
def test_absent_null_or_empty_remote_fields_mean_local(extra: dict[str, Any]) -> None:
    assert parse_model_entries(listing(entry(**extra)))[0].remote is False


@pytest.mark.parametrize(
    "extra",
    [
        {"remote_host": "https://ollama.com:443"},
        {"remote_model": "gpt-oss:120b"},
        {"remote_host": "h", "remote_model": "m"},
        # A shape we do not understand fails closed: it must not pass as local.
        {"remote_host": 0},
        {"remote_host": False},
        {"remote_host": []},
        {"remote_model": {"a": 1}},
    ],
)
def test_any_other_remote_field_marks_the_entry_remote(extra: dict[str, Any]) -> None:  # [NEG]
    assert parse_model_entries(listing(entry(**extra)))[0].remote is True


def test_the_digest_is_optional_in_a_listing() -> None:
    (parsed,) = parse_model_entries(listing({"name": "m", "model": "m"}))
    assert parsed.digest is None
    (empty,) = parse_model_entries(listing(entry(digest="")))
    assert empty.digest is None


@pytest.mark.parametrize(
    "tags",
    [
        None,
        [],
        "models",
        {"models": "x"},
        {"models": ["x"]},
        {"models": [{"digest": "sha256:abc"}]},  # no name at all
        {"models": [{"name": 5}]},
        {"models": [{"name": "m", "model": 5}]},
    ],
)
def test_a_bad_listing_shape_is_an_error_never_a_guess(tags: object) -> None:  # [NEG]
    with pytest.raises(EmbeddingResponseError):
        parse_model_entries(tags)


# ----- find_local_entry ------------------------------------------------------------------------


def entries_of(*raw: dict[str, Any]) -> tuple[ModelEntry, ...]:
    return parse_model_entries(listing(*raw))


def test_find_local_entry_matches_latest_as_the_default_tag() -> None:
    found = find_local_entry(entries_of(entry("nomic-embed-text:latest")), "nomic-embed-text")
    assert found is not None and found.digest == "sha256:abc"


def test_find_local_entry_is_none_for_a_model_that_is_not_installed() -> None:  # [NEG]
    assert find_local_entry(entries_of(entry("other:latest")), "m") is None
    assert find_local_entry((), "m") is None


def test_find_local_entry_refuses_a_remote_entry() -> None:  # [NEG]
    assert find_local_entry(entries_of(entry("m:latest", remote_host="h")), "m") is None


def test_find_local_entry_refuses_a_cloud_name_even_when_listed_as_ordinary() -> None:  # [NEG]
    assert find_local_entry(entries_of(entry("m:cloud")), "m:cloud") is None


def test_one_remote_duplicate_refuses_the_name_even_if_another_entry_is_local() -> None:  # [NEG]
    both = entries_of(entry("m:latest"), entry("m:latest", remote_host="h"))
    assert find_local_entry(both, "m") is None
    assert find_local_entry(tuple(reversed(both)), "m") is None  # whichever comes first


# ----- the embedding client refuses a non-local embedding model too -----------------------------


def test_ensure_ready_refuses_a_remote_embedding_model_and_sends_no_text() -> None:  # [NEG]
    fake = FakeOllama(
        tags_response=lambda r: httpx.Response(
            200,
            json=listing(entry("nomic-embed-text:latest", remote_host="https://ollama.com:443")),
        )
    )
    client = make_client(fake)
    with pytest.raises(ModelMissingError):
        run(client.ensure_ready())
    assert fake.paths == ["/api/tags"]  # the probe text was never embedded


def test_ensure_ready_refuses_a_cloud_named_embedding_model() -> None:  # [NEG]
    fake = FakeOllama(
        tags_response=lambda r: httpx.Response(200, json=listing(entry("embedder:cloud")))
    )
    client = make_client(fake, model="embedder:cloud")
    with pytest.raises(ModelMissingError):
        run(client.ensure_ready())
    assert fake.paths == ["/api/tags"]


def test_ensure_ready_still_accepts_a_local_embedding_model() -> None:
    fake = FakeOllama()
    profile = run(make_client(fake).ensure_ready())
    assert profile.model_digest == "sha256:abc123"


def test_installed_models_returns_entries_with_the_remote_flag() -> None:
    fake = FakeOllama(
        tags_response=lambda r: httpx.Response(
            200, json=listing(entry("a:latest"), entry("b:latest", remote_host="h"))
        )
    )

    async def scenario() -> tuple[ModelEntry, ...]:
        client = OllamaEmbeddingClient(
            BASE_URL, "a", expected_dimension=4, transport=httpx.MockTransport(fake)
        )
        try:
            return await client.installed_models()
        finally:
            await client.aclose()

    local, remote = asyncio.run(scenario())
    assert (local.remote, remote.remote) == (False, True)
