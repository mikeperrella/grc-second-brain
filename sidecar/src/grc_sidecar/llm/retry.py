"""The retry budget for one question (CLAUDE.md 8.3, ADR-013 decision 12).

**One shared budget of at most two attempts**, covering a reply that is not valid JSON, a reply that
fails the schema, and a quote that fails the grounding check, all together. A question never gets
two attempts for the schema and two more for the grounding.

The loop is generic: it knows nothing about models, prompts or quotes. The caller's `attempt`
function does one full try (build the prompt, call the model, parse, verify) and returns either
`Accepted` or `Rejected` with the exact error text to show the model next time. Failures that are
not about the answer's quality (Ollama unreachable, a refused model, an oversize prompt) are
exceptions, not `Rejected`: they are not retried here, because asking again would not help.

The error text is never logged. Only the attempt number is.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final, Generic, TypeVar

MAX_ATTEMPTS: Final = 2

_log = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class Accepted(Generic[T]):
    value: T


@dataclass(frozen=True)
class Rejected:
    """This attempt's answer was not usable. `error` is the exact report for the next attempt."""

    error: str


@dataclass(frozen=True)
class RetryOutcome(Generic[T]):
    """`value` is None when every attempt was rejected; `last_error` is then the final report."""

    value: T | None
    attempts: int
    last_error: str | None


async def with_retries(
    attempt: Callable[[str | None], Awaitable[Accepted[T] | Rejected]],
) -> RetryOutcome[T]:
    """Call `attempt(None)`, and if it is rejected, `attempt(error)` once. Never more than that."""
    error: str | None = None
    for number in range(1, MAX_ATTEMPTS + 1):
        result = await attempt(error)
        if isinstance(result, Accepted):
            _log.debug("attempt %d accepted", number)
            return RetryOutcome(value=result.value, attempts=number, last_error=None)
        _log.debug("attempt %d rejected", number)  # the reason is never logged
        error = result.error
    return RetryOutcome(value=None, attempts=MAX_ATTEMPTS, last_error=error)
