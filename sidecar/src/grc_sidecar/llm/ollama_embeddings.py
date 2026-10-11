"""Embeddings from a local Ollama (CLAUDE.md 3: loopback only, models provisioned by a human).

Three rules shape this module:

1. **Two endpoints, nothing else.** `GET /api/tags` (is the model installed, and which build) and
   `POST /api/embed`. A request hook refuses any other method, path or query before it leaves the
   process, and no method takes a path argument. This, not a text search of the source, is the
   control behind "the app never downloads models": the download endpoint cannot be spelled in a
   way that gets through, however its path is built.
2. **No Ollama text in errors.** A response body or an exception from the HTTP library can quote
   the input (document text). Errors are fixed strings, raised AFTER the `except` block that saw
   the original, so the original is not chained to them as `__context__`.
3. **`truncate: false`.** Ollama's default silently cuts the end of an over-long input. A cut
   chunk would no longer match its vector, so an over-long input is an error instead.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from grc_sidecar.errors import (
    EmbeddingDimensionError,
    EmbeddingResponseError,
    EmbeddingUnavailableError,
    EndpointNotAllowedError,
    GrcError,
    ModelMissingError,
)
from grc_sidecar.llm.embeddings import QUERY_PREFIX
from grc_sidecar.repositories.base import EmbeddingProfile
from grc_sidecar.security.net import (
    AsyncBaseTransport,
    HTTPError,
    Request,
    Timeout,
    create_async_client,
)
from grc_sidecar.vault.chunker import EMBED_DOCUMENT_PREFIX

TAGS_PATH: Final = "/api/tags"
EMBED_PATH: Final = "/api/embed"
# The only (method, path) pairs the client may send. Compared exactly, query string included.
ALLOWED_ENDPOINTS: Final = frozenset({("GET", TAGS_PATH), ("POST", EMBED_PATH)})

BATCH_SIZE: Final = 16
# Seconds. The first embed call makes Ollama load the model into memory, which can take a long
# time on a cold machine, so it gets a much longer read timeout than every later call.
DEFAULT_TIMEOUT: Final = Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)
FIRST_CALL_TIMEOUT: Final = Timeout(connect=5.0, read=180.0, write=30.0, pool=5.0)
# The model gate's `GET /api/tags` is only a listing, so it gets a short timeout of its own: an
# unresponsive Ollama must show up as "unreachable" in seconds, not hold a caller for 30 s.
TAGS_CHECK_TIMEOUT: Final = Timeout(connect=2.0, read=3.0, write=3.0, pool=2.0)

_PROBE_TEXT: Final = f"{EMBED_DOCUMENT_PREFIX}dimension probe"


async def _enforce_endpoint(request: Request) -> None:
    """Request hook: only `GET /api/tags` and `POST /api/embed`, with no query or fragment."""
    if (request.method, request.url.path) not in ALLOWED_ENDPOINTS or request.url.query:
        raise EndpointNotAllowedError


@dataclass(frozen=True)
class EmbedResult:
    vectors: list[list[float]]
    prompt_eval_count: int | None  # tokens Ollama counted; None if it did not say


def model_names(model: str) -> set[str]:
    """`nomic-embed-text` and `nomic-embed-text:latest` are the same installed model."""
    return {model, model if ":" in model else f"{model}:latest"}


def _installed_names(tags: object) -> frozenset[str]:
    """Every name Ollama lists for an installed model. A bad shape is an error, never a guess."""
    if not isinstance(tags, dict) or not isinstance(tags.get("models"), list):
        raise EmbeddingResponseError
    names: set[str] = set()
    for entry in tags["models"]:
        if not isinstance(entry, dict):
            raise EmbeddingResponseError
        found = [value for value in (entry.get("name"), entry.get("model")) if value]
        if not found or not all(isinstance(value, str) for value in found):
            raise EmbeddingResponseError
        names.update(found)
    return frozenset(names)


def _digest_of_installed(tags: object, model: str) -> str | None:
    """The digest of the installed model, None if it is not installed. Bad shapes are errors."""
    if not isinstance(tags, dict) or not isinstance(tags.get("models"), list):
        raise EmbeddingResponseError
    wanted = model_names(model)
    for entry in tags["models"]:
        if not isinstance(entry, dict):
            raise EmbeddingResponseError
        if {entry.get("name"), entry.get("model")} & wanted:
            digest = entry.get("digest")
            if not isinstance(digest, str) or not digest:
                raise EmbeddingResponseError
            return digest
    return None


def _parse_embeddings(payload: object, count: int, dimension: int | None) -> EmbedResult:
    if not isinstance(payload, dict):
        raise EmbeddingResponseError
    raw = payload.get("embeddings")
    if not isinstance(raw, list) or len(raw) != count:
        raise EmbeddingResponseError
    vectors: list[list[float]] = []
    for item in raw:
        if not isinstance(item, list) or not item:
            raise EmbeddingResponseError
        vector: list[float] = []
        for value in item:
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise EmbeddingResponseError
            if not math.isfinite(value):
                raise EmbeddingResponseError
            vector.append(float(value))
        vectors.append(vector)
    expected = dimension if dimension is not None else len(vectors[0])
    if any(len(vector) != expected for vector in vectors):
        raise EmbeddingResponseError
    evaluated = payload.get("prompt_eval_count")
    tokens = evaluated if isinstance(evaluated, int) and not isinstance(evaluated, bool) else None
    return EmbedResult(vectors=vectors, prompt_eval_count=tokens)


class OllamaEmbeddingClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        expected_dimension: int,
        transport: AsyncBaseTransport | None = None,
        timeout: Timeout = DEFAULT_TIMEOUT,
        first_call_timeout: Timeout = FIRST_CALL_TIMEOUT,
    ) -> None:
        self._model = model
        self._expected_dimension = expected_dimension
        self._timeout = timeout
        self._first_call_timeout = first_call_timeout
        self._model_loaded = False  # becomes True after the first successful embed
        self._dimension: int | None = None
        self._http = create_async_client(base_url, timeout=timeout, transport=transport)
        self._http.event_hooks["request"].append(_enforce_endpoint)

    async def aclose(self) -> None:
        await self._http.aclose()

    # ----- the one place that talks to Ollama ------------------------------------------------

    async def _call(
        self, method: str, path: str, body: dict[str, Any] | None, timeout: Timeout
    ) -> object:
        """Send one request and return the parsed JSON, or raise a fixed-message domain error.

        The outcome is recorded in `failure` inside the `except` blocks and the error is raised
        after them. `raise ... from None` would only hide the original from the traceback; it
        would stay attached as `__context__`, and the original can quote document text.
        """
        failure: str | None = None
        payload: object = None
        try:
            response = await self._http.request(method, path, json=body, timeout=timeout)
            status = response.status_code
            if status == 200:
                payload = response.json()
            elif status == 404 and path == EMBED_PATH:
                failure = "model_missing"
            elif status == 400:
                failure = "response"  # for example "the input is longer than the context"
            else:
                failure = "unavailable"
        except GrcError:
            raise  # our own refusals (a non-loopback URL, a forbidden endpoint) arrive as they are
        except HTTPError:
            failure = "unavailable"  # refused, reset, timed out, undecodable, too many redirects
        except ValueError:
            failure = "response"  # the body was not JSON
        if failure == "model_missing":
            raise ModelMissingError
        if failure == "unavailable":
            raise EmbeddingUnavailableError
        if failure == "response":
            raise EmbeddingResponseError
        return payload

    async def _embed_raw(self, texts: Sequence[str], dimension: int | None) -> EmbedResult:
        vectors: list[list[float]] = []
        total_tokens: int | None = 0
        for start in range(0, len(texts), BATCH_SIZE):
            batch = list(texts[start : start + BATCH_SIZE])
            body = {"model": self._model, "input": batch, "truncate": False}
            timeout = self._timeout if self._model_loaded else self._first_call_timeout
            payload = await self._call("POST", EMBED_PATH, body, timeout)
            result = _parse_embeddings(payload, len(batch), dimension)
            self._model_loaded = True
            vectors.extend(result.vectors)
            if total_tokens is not None and result.prompt_eval_count is not None:
                total_tokens += result.prompt_eval_count
            else:
                total_tokens = None
        return EmbedResult(vectors=vectors, prompt_eval_count=total_tokens)

    async def installed_models(self) -> frozenset[str]:
        """The names of the models installed in Ollama (`GET /api/tags`, the model gate's input).

        Uses the same restricted client as everything else (no new endpoint) and a short timeout.
        Raises EmbeddingUnavailableError or EmbeddingResponseError, with fixed messages.
        """
        return _installed_names(await self._call("GET", TAGS_PATH, None, TAGS_CHECK_TIMEOUT))

    # ----- the EmbeddingClient protocol ------------------------------------------------------

    async def ensure_ready(self) -> EmbeddingProfile:
        tags = await self._call("GET", TAGS_PATH, None, self._timeout)
        digest = _digest_of_installed(tags, self._model)
        if digest is None:
            raise ModelMissingError
        probe = await self._embed_raw([_PROBE_TEXT], dimension=None)
        measured = len(probe.vectors[0])
        if measured != self._expected_dimension:
            raise EmbeddingDimensionError
        self._dimension = measured
        return EmbeddingProfile(
            model=self._model,
            model_digest=digest,
            dimension=measured,
            doc_prefix=EMBED_DOCUMENT_PREFIX,
            query_prefix=QUERY_PREFIX,
        )

    def _ready_dimension(self) -> int:
        if self._dimension is None:
            raise EmbeddingUnavailableError("Embedding client is not ready")
        return self._dimension

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return (await self.embed_documents_with_stats(texts)).vectors

    async def embed_documents_with_stats(self, texts: Sequence[str]) -> EmbedResult:
        """Like `embed_documents`, with Ollama's token count (used by the live size check)."""
        return await self._embed_raw(texts, self._ready_dimension())

    async def embed_query(self, text: str) -> list[float]:
        result = await self._embed_raw([QUERY_PREFIX + text], self._ready_dimension())
        return result.vectors[0]
