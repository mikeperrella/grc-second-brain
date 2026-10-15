"""The model gate: which required models are installed, and a refusal for the ones that are not.

CLAUDE.md 3.7 and ADR-011. The sidecar does not refuse to start when Ollama is down or a model is
missing. It records what it found and reports it (`ModelStatus`, served on `GET /readiness`), and
every model-dependent operation first calls `ModelGate.require(model)`, which raises the typed
`ModelsUnavailableError` BEFORE any request to that model is made.

Three rules keep this cheap and honest:

* The listing (`GET /api/tags`) has its own short timeout (set by the lister, not here).
* A re-check on demand is limited to one per `RECHECK_MIN_INTERVAL` seconds, measured with the
  injected `Clock`, so a caller that retries in a loop cannot hammer Ollama.
* One lock serializes every check. The background check at startup and an on-demand check never
  run at the same time, and a caller that waited on the lock sees the result of the check it
  waited for instead of sending a second request.

The gate also refuses non-local models (CLAUDE.md 3.7, ADR-012): Ollama can forward a cloud-named
model, or one whose listing carries `remote_host` or `remote_model`, to ollama.com. Such a model is
reported as missing, so the same typed refusal applies and no request is sent to it.

The status holds only model names from our own configuration and fixed words, never anything
Ollama said.
"""

import asyncio
import logging
from typing import Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from grc_sidecar.errors import EmbeddingResponseError, GrcError, ModelsUnavailableError
from grc_sidecar.llm.ollama_embeddings import (
    ModelEntry,
    find_local_entry,
    is_cloud_model_name,
    model_names,
)
from grc_sidecar.vault.clock import Clock

RECHECK_MIN_INTERVAL: Final = 5.0  # seconds between two checks that are not the first

_log = logging.getLogger(__name__)

GateState = Literal["checking", "ready", "degraded"]
OllamaState = Literal["unknown", "reachable", "unreachable", "bad_response"]


class ModelLister(Protocol):
    async def installed_models(self) -> tuple[ModelEntry, ...]:
        """The installed models. Raises GrcError subclasses with fixed messages."""


class ModelStatus(BaseModel):
    """What the last check found. `models_missing` is empty until the first check has finished."""

    model_config = ConfigDict(frozen=True)

    state: GateState
    models_missing: tuple[str, ...]
    ollama: OllamaState


class ModelGate:
    def __init__(
        self,
        lister: ModelLister,
        required: tuple[str, ...],
        clock: Clock,
        *,
        min_interval: float = RECHECK_MIN_INTERVAL,
    ) -> None:
        self._lister = lister
        self._required = required
        self._clock = clock
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last_check: float | None = None
        self._installed: tuple[ModelEntry, ...] = ()
        self._status = ModelStatus(state="checking", models_missing=(), ollama="unknown")

    @property
    def status(self) -> ModelStatus:
        """The last recorded status. Does not contact Ollama."""
        return self._status

    def local_entry(self, model: str) -> ModelEntry | None:
        """The last-seen LOCAL listing entry for `model` (its digest, for the run record), or None.

        None means not installed, cloud-named, or listed as remote. Does not contact Ollama; call
        `require` or `refresh` first to make it current.
        """
        return find_local_entry(self._installed, model)

    async def refresh(self) -> ModelStatus:
        """Check now, unless a check already ran within the minimum interval. Returns the status.

        The startup task and `GET /readiness` both call this, so they share the interval and the
        lock with `require`.
        """
        async with self._lock:
            if self._may_check():
                await self._check()
        return self._status

    async def require(self, *models: str) -> None:
        """Return only if every named model is confirmed installed; else raise.

        A model that is already confirmed costs nothing. Otherwise one on-demand re-check is
        allowed (a user may have installed the model since the last check), subject to the
        minimum interval. Raises ModelsUnavailableError, naming the unconfirmed models.
        """
        if not self._missing(models):
            return
        async with self._lock:
            # Another caller may have finished a check while this one waited for the lock.
            if self._missing(models) and self._may_check():
                await self._check()
            missing = self._missing(models)
        if missing:
            raise ModelsUnavailableError(missing)

    # ----- internals -------------------------------------------------------------------------

    def _missing(self, models: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(m for m in models if self.local_entry(m) is None)

    def _may_check(self) -> bool:
        if self._last_check is None:
            return True
        return self._clock.monotonic() - self._last_check >= self._min_interval

    async def _check(self) -> None:
        """One listing; the caller holds the lock. Never raises a listing failure."""
        ollama: OllamaState = "reachable"
        installed: tuple[ModelEntry, ...] = ()
        try:
            installed = await self._lister.installed_models()
        except EmbeddingResponseError:
            ollama = "bad_response"
        except GrcError:
            ollama = "unreachable"  # not reachable, or refused by the client's own guards
        self._last_check = self._clock.monotonic()
        self._installed = installed
        missing = self._missing(self._required)
        state: GateState = "ready" if not missing and ollama == "reachable" else "degraded"
        if ollama != "reachable":
            missing = self._required  # nothing can be confirmed
        self._status = ModelStatus(state=state, models_missing=missing, ollama=ollama)
        _log.info("model check: state=%s ollama=%s missing=%s", state, ollama, list(missing))
        refused = tuple(m for m in self._required if m in missing and self._is_non_local(m))
        if refused:  # configured names only, never anything Ollama said
            _log.info("model refused as non-local: %s", list(refused))

    def refused_as_non_local(self, model: str) -> bool:
        """True if the gate treats `model` as one Ollama would run elsewhere (cloud-named, or
        listed with `remote_host` or `remote_model`). Does not contact Ollama."""
        return self._is_non_local(model)

    def _is_non_local(self, model: str) -> bool:
        """Installed as far as the listing goes, but Ollama would run it elsewhere."""
        if is_cloud_model_name(model):
            return True
        wanted = model_names(model)
        return any(entry.remote and entry.names & wanted for entry in self._installed)
