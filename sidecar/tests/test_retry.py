"""One shared budget of at most two attempts per question (CLAUDE.md 8.3, ADR-013 decision 12)."""

import asyncio
import logging

import pytest

from grc_sidecar.errors import GenerationUnavailableError
from grc_sidecar.llm.retry import MAX_ATTEMPTS, Accepted, Rejected, with_retries

SECRET = "SCHEMA-ERROR-TEXT-CANARY"


class Script:
    """An `attempt` function that plays back a list of results and records what it was shown."""

    def __init__(self, results: list[Accepted[str] | Rejected]) -> None:
        self.results = results
        self.seen: list[str | None] = []

    async def __call__(self, error: str | None) -> Accepted[str] | Rejected:
        self.seen.append(error)
        if len(self.seen) > len(self.results):
            raise AssertionError("asked for a third attempt")  # the budget is two
        return self.results[len(self.seen) - 1]


def run(script: Script) -> object:
    return asyncio.run(with_retries(script))


def test_the_budget_is_two() -> None:
    assert MAX_ATTEMPTS == 2


def test_a_first_attempt_that_works_is_the_only_attempt() -> None:
    script = Script([Accepted("answer")])
    outcome = asyncio.run(with_retries(script))
    assert (outcome.value, outcome.attempts, outcome.last_error) == ("answer", 1, None)
    assert script.seen == [None]  # attempt 1 carries no error


def test_a_second_attempt_gets_the_exact_error_of_the_first() -> None:
    script = Script([Rejected("exact_quote: not in S2"), Accepted("answer")])
    outcome = asyncio.run(with_retries(script))
    assert (outcome.value, outcome.attempts, outcome.last_error) == ("answer", 2, None)
    assert script.seen == [None, "exact_quote: not in S2"]  # [NEG] mutation: omit the error


def test_two_rejections_end_in_a_failure_with_the_last_error_and_never_a_third_attempt() -> None:
    script = Script([Rejected("first"), Rejected("second")])
    outcome = asyncio.run(with_retries(script))  # a third call raises AssertionError in Script
    assert (outcome.value, outcome.attempts, outcome.last_error) == (None, 2, "second")
    assert script.seen == [None, "first"]


def test_a_schema_failure_and_a_grounding_failure_share_one_budget() -> None:  # [NEG]
    """Two attempts in total, not two for the schema plus two more for the quote."""
    script = Script([Rejected("schema: bad type"), Rejected("grounding: quote not found")])
    outcome = asyncio.run(with_retries(script))
    assert outcome.attempts == 2 and outcome.value is None
    assert len(script.seen) == 2


def test_a_failure_that_is_not_about_the_answer_is_not_retried() -> None:  # [NEG]
    calls: list[str | None] = []

    async def attempt(error: str | None) -> Accepted[str] | Rejected:
        calls.append(error)
        raise GenerationUnavailableError

    with pytest.raises(GenerationUnavailableError):
        asyncio.run(with_retries(attempt))
    assert calls == [None]  # asking again would not help


def test_the_error_text_is_never_logged(caplog: pytest.LogCaptureFixture) -> None:  # [NEG]
    caplog.set_level(logging.DEBUG)
    asyncio.run(with_retries(Script([Rejected(SECRET), Rejected(SECRET)])))
    assert SECRET not in caplog.text
    assert "attempt 1 rejected" in caplog.text and "attempt 2 rejected" in caplog.text


def test_the_loop_ends_even_if_the_attempt_always_rejects() -> None:  # [NEG] never indefinitely
    count = 0

    async def always_reject(error: str | None) -> Accepted[str] | Rejected:
        nonlocal count
        count += 1
        assert count <= 50, "the loop did not stop"
        return Rejected("again")

    outcome = asyncio.run(with_retries(always_reject))
    assert count == 2 and outcome.attempts == 2
