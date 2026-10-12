"""Text generation from a local Ollama (CLAUDE.md 3 and 8.3, ADR-012).

Built like the embedding client (ADR-009) and with the same rules, plus the ones a generation call
adds:

1. **Two endpoints, nothing else.** `POST /api/chat` and `GET /api/tags`, compared exactly with no
   query string, by a request hook that runs before anything leaves the process. No method takes a
   path. The word `/api/chat` is spelled in this module only (a Semgrep rule and a test keep it so).
2. **Exactly two messages, always.** `generate(system, user, schema)` builds `[system, user]`
   itself; there is no way to pass a conversation. Ollama drops messages from the FRONT of a chat
   when a prompt is too long, so a longer chat could silently lose the user message that holds the
   sources (ADR-012, row 4). With two messages nothing can be dropped, which is what makes
   `shift: false` a complete overflow control.
3. **Truncation fails closed.** Every request sets `shift: false`, so an oversize prompt is an error
   (HTTP 400) instead of a quiet cut. Before sending, the assembled size is checked against the
   caps in `limits.py`; after the reply, the reported token count must be below the window and the
   stop reason must be `stop`. The last two are secondary (see `limits.py`).
4. **The gate first.** Every call goes through `ModelGate.require(model)`, so a missing, cloud-named
   or remote model is refused before a request is made.
5. **No Ollama text in errors.** Errors are fixed strings raised AFTER the `except` block that saw
   the original, so nothing is chained as `__context__` and no response body or prompt text can
   reach a log.
6. **A schema on every request** (`format`), and the caller parses the reply strictly with Pydantic.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from grc_sidecar.errors import (
    ContextWindowReachedError,
    EmbeddingResponseError,
    EndpointNotAllowedError,
    GenerationRejectedError,
    GenerationResponseError,
    GenerationUnavailableError,
    GrcError,
    ModelMissingError,
    NonLocalModelError,
    OutputTruncatedError,
    PromptTooLargeError,
)
from grc_sidecar.llm.limits import (
    MAX_MESSAGE_CHARS,
    NUM_CTX,
    NUM_PREDICT,
    SEED,
    TEMPERATURE,
)
from grc_sidecar.llm.model_gate import ModelGate
from grc_sidecar.llm.ollama_embeddings import (
    TAGS_CHECK_TIMEOUT,
    TAGS_PATH,
    ModelEntry,
    find_local_entry,
    parse_model_entries,
)
from grc_sidecar.security.net import (
    AsyncBaseTransport,
    HTTPError,
    Request,
    Timeout,
    create_async_client,
)

CHAT_PATH: Final = "/api/chat"
# The only (method, path) pairs this client may send. Compared exactly, query string included.
ALLOWED_ENDPOINTS: Final = frozenset({("POST", CHAT_PATH), ("GET", TAGS_PATH)})

# Seconds. The first call makes Ollama load the model into memory, which can take a long time on a
# cold machine, so it gets a longer read timeout than every later call. Starting values, to be
# tuned from live check A.
DEFAULT_TIMEOUT: Final = Timeout(connect=5.0, read=120.0, write=30.0, pool=5.0)
FIRST_CALL_TIMEOUT: Final = Timeout(connect=5.0, read=180.0, write=30.0, pool=5.0)


async def _enforce_endpoint(request: Request) -> None:
    """Request hook: only `POST /api/chat` and `GET /api/tags`, with no query or fragment."""
    if (request.method, request.url.path) not in ALLOWED_ENDPOINTS or request.url.query:
        raise EndpointNotAllowedError


@dataclass(frozen=True)
class GenerationResult:
    """One reply. `content` is the model's text: JSON that the caller must validate strictly.

    The counts and durations are what Ollama reported, or None if it omitted them (Ollama leaves a
    zero out of the JSON). Durations are nanoseconds.
    """

    content: str
    done_reason: str
    prompt_eval_count: int | None
    prompt_eval_cached_count: int | None
    eval_count: int | None
    total_duration: int | None
    load_duration: int | None
    prompt_eval_duration: int | None
    eval_duration: int | None


def _count(payload: Mapping[str, object], key: str) -> int | None:
    """A non-negative integer field, or None if absent. Any other type is not usable."""
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GenerationResponseError
    return value


def _parse_reply(payload: object) -> GenerationResult:
    """Validate one chat reply and apply the fail-closed checks. Raises fixed-message errors."""
    if not isinstance(payload, dict):
        raise GenerationResponseError
    # A reply that says it came from another host: the prompt has already left (a tripwire).
    if any(payload.get(key) not in (None, "") for key in ("remote_host", "remote_model")):
        raise NonLocalModelError
    if payload.get("done") is not True:
        raise GenerationResponseError
    message = payload.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise GenerationResponseError
    content: str = message["content"]
    reason = payload.get("done_reason")
    if not isinstance(reason, str):
        raise GenerationResponseError
    if reason == "length":
        raise OutputTruncatedError
    if reason != "stop":
        raise GenerationResponseError
    result = GenerationResult(
        content=content,
        done_reason=reason,
        prompt_eval_count=_count(payload, "prompt_eval_count"),
        prompt_eval_cached_count=_count(payload, "prompt_eval_cached_count"),
        eval_count=_count(payload, "eval_count"),
        total_duration=_count(payload, "total_duration"),
        load_duration=_count(payload, "load_duration"),
        prompt_eval_duration=_count(payload, "prompt_eval_duration"),
        eval_duration=_count(payload, "eval_duration"),
    )
    if result.prompt_eval_count is not None:
        # Whether the count includes cached tokens is unverified (ADR-012), so add them: the
        # larger figure can only make this check stricter.
        seen = result.prompt_eval_count + (result.prompt_eval_cached_count or 0)
        if seen >= NUM_CTX - NUM_PREDICT:
            raise ContextWindowReachedError
    return result


class GenerationClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        gate: ModelGate,
        *,
        transport: AsyncBaseTransport | None = None,
        timeout: Timeout = DEFAULT_TIMEOUT,
        first_call_timeout: Timeout = FIRST_CALL_TIMEOUT,
    ) -> None:
        self._model = model
        self._gate = gate
        self._timeout = timeout
        self._first_call_timeout = first_call_timeout
        self._model_loaded = False  # becomes True after the first successful reply
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
        after them: `raise ... from None` would only hide the original from the traceback, and the
        original (an HTTP-library error) can quote the prompt.
        """
        failure: str | None = None
        payload: object = None
        try:
            response = await self._http.request(method, path, json=body, timeout=timeout)
            status = response.status_code
            if status == 200:
                payload = response.json()
            elif status == 404 and path == CHAT_PATH:
                failure = "model_missing"
            elif status == 400:
                failure = "rejected"  # for example "the prompt is longer than the context length"
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
        if failure == "rejected":
            raise GenerationRejectedError
        if failure == "unavailable":
            raise GenerationUnavailableError
        if failure == "response":
            raise GenerationResponseError
        return payload

    # ----- public ----------------------------------------------------------------------------

    async def generate(
        self, system: str, user: str, schema: Mapping[str, object]
    ) -> GenerationResult:
        """One chat call: exactly the two messages given, the schema in `format`.

        Raises PromptTooLargeError before anything is sent if the messages exceed the caps,
        ModelsUnavailableError if the gate does not confirm a local model, and the fixed-message
        Generation* errors, ContextWindowReachedError, OutputTruncatedError and NonLocalModelError
        for what can go wrong after.
        """
        if len(system) + len(user) > MAX_MESSAGE_CHARS:
            raise PromptTooLargeError
        await self._gate.require(self._model)
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "format": dict(schema),
            "shift": False,  # an oversize prompt is an error, not a silent cut (ADR-012)
            "options": {
                "temperature": TEMPERATURE,
                "seed": SEED,
                "num_ctx": NUM_CTX,
                "num_predict": NUM_PREDICT,
            },
        }
        timeout = self._timeout if self._model_loaded else self._first_call_timeout
        payload = await self._call("POST", CHAT_PATH, body, timeout)
        result = _parse_reply(payload)
        self._model_loaded = True
        return result

    async def read_model_entry(self) -> ModelEntry:
        """A fresh `GET /api/tags` and the LOCAL entry for the configured model (its digest).

        Used at the start and the end of a run, so a model replaced in between is recorded. Raises
        ModelMissingError if the model is not installed or is not local.
        """
        tags = await self._call("GET", TAGS_PATH, None, TAGS_CHECK_TIMEOUT)
        bad_shape = False
        entries: tuple[ModelEntry, ...] = ()
        try:
            entries = parse_model_entries(tags)
        except EmbeddingResponseError:
            bad_shape = True
        if bad_shape:
            raise GenerationResponseError
        entry = find_local_entry(entries, self._model)
        if entry is None:
            raise ModelMissingError
        return entry
