"""Answering one question: retrieve, prompt, generate, verify, decide (CLAUDE.md 8.4, ADR-013).

This is the one place the pieces meet, and it adds no decision of its own:

* retrieval (M4) supplies the context and the facts about it (keyword-only? files withheld?);
* the prompt builder (M5d) wraps the question and the sources as data;
* the generation client (M5c) calls the model, always as exactly two messages;
* the verifier and status code (M5e) decide from facts, and the model can only lower the result;
* the retry loop (M5d) spends one shared budget of two attempts.

Every outcome is an `AnswerResult`. A failure that is about THIS question (an oversize prompt, a
reply that never verifies) becomes a blank result with a reason. A failure of the MODEL (Ollama
unreachable, a refused or non-local model) becomes a blank result too, flagged `model_unavailable`
so the run that is asking can stop instead of failing every remaining question one by one. Errors
about the INDEX (not built, cannot be certified fresh) are not answered: they propagate, because no
question can be answered without retrieval (a run refuses to start in that state, ADR-014).

Nothing in here logs question, source or reply text: only counts and reason codes, at INFO.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from grc_sidecar.errors import (
    ContextWindowReachedError,
    GenerationRejectedError,
    GenerationResponseError,
    GenerationUnavailableError,
    ModelMissingError,
    ModelsUnavailableError,
    NonLocalModelError,
    OutputTruncatedError,
    PromptTooLargeError,
)
from grc_sidecar.llm.generation_client import GenerationResult
from grc_sidecar.llm.limits import QUESTION_MAX_CHARS
from grc_sidecar.llm.prompt import BoundaryFactory, PromptSource, build_prompt, new_boundary
from grc_sidecar.llm.retry import Accepted, Rejected, with_retries
from grc_sidecar.llm.schemas import (
    ModelAnswer,
    output_schema,
    parse_model_answer,
    render_validation_error,
)
from grc_sidecar.questionnaire.models import AnswerResult, ReasonCode, Rejection
from grc_sidecar.questionnaire.status import (
    blank_result,
    context_limits,
    finalize_rejection,
    judge,
    no_evidence_result,
)
from grc_sidecar.retrieval.models import RetrievalResult, RetrievedChunk
from grc_sidecar.vault.clock import Clock

_log = logging.getLogger(__name__)


class TextGenerator(Protocol):
    """The part of the generation client the service uses (a scripted fake in the tests)."""

    async def generate(
        self, system: str, user: str, schema: Mapping[str, object]
    ) -> GenerationResult: ...


class Retrieving(Protocol):
    """The part of the retriever the service uses."""

    async def retrieve(self, query: str) -> RetrievalResult: ...


@dataclass(frozen=True)
class AnswerOutcome:
    """The result for one question, plus the measurements the run reports (never any text)."""

    result: AnswerResult
    attempts: int  # model calls that returned a reply
    prompt_eval_count: int | None  # summed over attempts; None if Ollama reported none
    eval_count: int | None
    prompt_chars: int  # characters sent over all attempts (system + user)
    done_reasons: tuple[str, ...]  # Ollama's stop reason for each reply
    retrieval_mode: str | None  # "hybrid" or "lexical_only"; None if retrieval did not run
    sources: int  # chunks in the context given to the model
    duration: float  # seconds, from the injected monotonic clock
    model_unavailable: bool  # True: the model could not be used, so a run should stop asking


class _Attempts:
    """What the attempts of one question leave behind (the loop's closure writes it)."""

    def __init__(self) -> None:
        self.calls = 0
        self.prompt_chars = 0
        self.prompt_tokens: int | None = None
        self.output_tokens: int | None = None
        self.done_reasons: list[str] = []
        self.last_rejection: Rejection | None = None
        self.last_confidence: float | None = None

    def record(self, built_chars: int, generated: GenerationResult) -> None:
        self.calls += 1
        self.prompt_chars += built_chars
        self.done_reasons.append(generated.done_reason)
        self.prompt_tokens = _add(self.prompt_tokens, generated.prompt_eval_count)
        self.output_tokens = _add(self.output_tokens, generated.eval_count)


def _add(total: int | None, more: int | None) -> int | None:
    if more is None:
        return total
    return more if total is None else total + more


class AnswerService:
    def __init__(
        self,
        *,
        retriever: Retrieving,
        generator: TextGenerator,
        clock: Clock,
        boundary: BoundaryFactory = new_boundary,
    ) -> None:
        self._retriever = retriever
        self._generator = generator
        self._clock = clock
        self._boundary = boundary

    async def answer(self, question: str) -> AnswerOutcome:
        started = self._clock.monotonic()
        outcome = await self._answer(question, started)
        _log.info(
            "answered: status=%s reasons=%s attempts=%d sources=%d",
            outcome.result.status,
            list(outcome.result.reasons),
            outcome.attempts,
            outcome.sources,
        )
        return outcome

    # ----- the steps ---------------------------------------------------------------------------

    def _outcome(
        self,
        result: AnswerResult,
        started: float,
        *,
        state: _Attempts | None = None,
        retrieval: RetrievalResult | None = None,
        sources: int = 0,
        unavailable: bool = False,
    ) -> AnswerOutcome:
        state = state or _Attempts()
        return AnswerOutcome(
            result=result,
            attempts=state.calls,
            prompt_eval_count=state.prompt_tokens,
            eval_count=state.output_tokens,
            prompt_chars=state.prompt_chars,
            done_reasons=tuple(state.done_reasons),
            retrieval_mode=None if retrieval is None else retrieval.mode,
            sources=sources,
            duration=self._clock.monotonic() - started,
            model_unavailable=unavailable,
        )

    async def _answer(self, question: str, started: float) -> AnswerOutcome:
        if not question.strip():
            return self._outcome(blank_result("empty_question"), started)
        if len(question) > QUESTION_MAX_CHARS:
            return self._outcome(blank_result("question_too_long"), started)

        retrieval = await self._retriever.retrieve(question)  # index errors propagate (see above)
        lexical_only = retrieval.mode == "lexical_only"
        withheld = bool(retrieval.withheld_unindexed)
        chunks = retrieval.chunks
        if not chunks:  # nothing to cite, and no reason to ask the model
            result = no_evidence_result(None, lexical_only=lexical_only, withheld=withheld)
            return self._outcome(result, started, retrieval=retrieval)

        sources: dict[str, RetrievedChunk] = {f"S{n}": c for n, c in enumerate(chunks, start=1)}
        prompt_sources = [
            PromptSource(source_id, chunk.source_kind, chunk.review_overdue, chunk.text)
            for source_id, chunk in sources.items()
        ]
        ids = tuple(sources)
        schema = output_schema(ids)
        state = _Attempts()

        async def attempt(error: str | None) -> Accepted[AnswerResult] | Rejected:
            built = build_prompt(
                question, prompt_sources, boundary=self._boundary(), retry_error=error
            )
            generated = await self._generator.generate(built.system, built.user, schema)
            state.record(len(built.system) + len(built.user), generated)
            reply: ModelAnswer | None = None
            problem = ""
            try:
                reply = parse_model_answer(generated.content, ids)
            except ValidationError as exc:
                problem = render_validation_error(exc)
            if reply is None:
                state.last_rejection = None  # a schema failure, not a grounding one
                return Rejected(problem)
            state.last_confidence = reply.model_confidence
            judged = judge(question, reply, sources, lexical_only=lexical_only, withheld=withheld)
            if isinstance(judged, Rejection):
                state.last_rejection = judged
                return Rejected(judged.message)
            return Accepted(judged)

        failure: ReasonCode | None = None
        unavailable = False
        try:
            retried = await with_retries(attempt)
        except (
            GenerationUnavailableError,
            GenerationResponseError,
            ModelsUnavailableError,
            ModelMissingError,
            NonLocalModelError,
        ):
            failure, unavailable = "model_unavailable", True
        except (PromptTooLargeError, ContextWindowReachedError, GenerationRejectedError):
            failure = "prompt_too_large"
        except OutputTruncatedError:
            failure = "output_truncated"
        limits = context_limits(lexical_only=lexical_only, withheld=withheld)
        if failure is not None:
            result = blank_result(failure, *limits, model_confidence=state.last_confidence)
        elif retried.value is not None:
            result = retried.value
        elif state.last_rejection is not None:
            result = finalize_rejection(
                state.last_rejection,
                lexical_only=lexical_only,
                withheld=withheld,
                model_confidence=state.last_confidence,
            )
        else:  # every attempt was a reply that failed the schema
            result = blank_result(
                "retry_exhausted", *limits, model_confidence=state.last_confidence
            )
        return self._outcome(
            result,
            started,
            state=state,
            retrieval=retrieval,
            sources=len(chunks),
            unavailable=unavailable,
        )
