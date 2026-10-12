"""Helpers for the answer-service tests: a scripted model, a stub retriever and a merged vault.

The scripted model never touches a network. It records every request it is given and plays back
a list of steps (a reply string, a function of the request, or an exception to raise), so a test
decides exactly what the "model" says and can inspect exactly what it was shown.
"""

import json
import re
import shutil
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grc_sidecar.llm.generation_client import GenerationResult
from grc_sidecar.questionnaire.service import AnswerService
from grc_sidecar.retrieval.models import BudgetReport, RetrievalResult, RetrievedChunk

from .answerkit import chunk
from .retrievalkit import Stack, stack
from .vaultkit import ADVERSARIAL_M5_VAULT, DEMO_VAULT

_BEGIN = re.compile(r"^\S+ BEGIN SOURCE (S\d+) (.*)$")
_END = re.compile(r"^\S+ END SOURCE (S\d+)$")


@dataclass(frozen=True)
class Request:
    """One call the service made to the model."""

    system: str
    user: str
    schema: Mapping[str, object]

    def sources(self) -> dict[str, tuple[str, str]]:
        """Source id to (the header after the id, the text) as the model was shown it."""
        found: dict[str, tuple[str, str]] = {}
        current: str | None = None
        header = ""
        body: list[str] = []
        for line in self.user.splitlines():
            begin = _BEGIN.match(line)
            if begin and current is None:
                current, header, body = begin.group(1), begin.group(2), []
            elif current is not None and _END.match(line):
                found[current] = (header, "\n".join(body))
                current = None
            elif current is not None:
                body.append(line)
        return found

    def source_containing(self, phrase: str) -> str:
        """The id of the source whose text contains `phrase`. Fails the test if none does."""
        for source_id, (_, text) in self.sources().items():
            if phrase in text:
                return source_id
        raise AssertionError(f"no source contains {phrase!r}; ids: {list(self.sources())}")

    def boundary(self) -> str:
        return self.user.split(" ", 1)[0]


Step = str | Exception | Callable[[Request], str | Exception]


def reply_json(
    source_id: str | None,
    quote: str,
    *,
    answer: str = "An answer drawn from the source.",
    confidence: float = 0.95,
    coverage: str = "full",
    conflicts: tuple[str, ...] = (),
    no_evidence: bool = False,
) -> str:
    return json.dumps(
        {
            "answer": answer,
            "source_id": source_id,
            "exact_quote": quote,
            "model_confidence": confidence,
            "coverage": coverage,
            "conflicting_source_ids": list(conflicts),
            "no_evidence": no_evidence,
        }
    )


NO_EVIDENCE_REPLY = reply_json(
    None, "", answer="", confidence=0.0, coverage="none", no_evidence=True
)


def cite(phrase: str, **kwargs: object) -> Callable[[Request], str]:
    """A step that cites whichever source contains `phrase`, quoting exactly `phrase`."""

    def step(request: Request) -> str:
        return reply_json(request.source_containing(phrase), phrase, **kwargs)  # type: ignore[arg-type]

    return step


@dataclass
class FakeModel:
    """A scripted `TextGenerator`. Asking for more steps than were scripted fails the test, which
    is how the "never more than two attempts" rule is checked."""

    steps: list[Step]
    requests: list[Request] = field(default_factory=list)
    prompt_tokens: int = 800
    output_tokens: int = 30
    on_call: Callable[[], None] | None = None

    async def generate(
        self, system: str, user: str, schema: Mapping[str, object]
    ) -> GenerationResult:
        request = Request(system, user, schema)
        self.requests.append(request)
        if self.on_call is not None:
            self.on_call()
        if len(self.requests) > len(self.steps):
            raise AssertionError("the model was asked for more attempts than were scripted")
        step = self.steps[len(self.requests) - 1]
        outcome = step(request) if callable(step) else step
        if isinstance(outcome, Exception):
            raise outcome
        return GenerationResult(
            content=outcome,
            done_reason="stop",
            prompt_eval_count=self.prompt_tokens,
            prompt_eval_cached_count=None,
            eval_count=self.output_tokens,
            total_duration=None,
            load_duration=None,
            prompt_eval_duration=None,
            eval_duration=None,
        )


def retrieval_of(
    *chunks: RetrievedChunk, mode: str = "hybrid", withheld: tuple[str, ...] = ()
) -> RetrievalResult:
    return RetrievalResult(
        query="q",
        mode=mode,  # type: ignore[arg-type]
        degraded_reason=None if mode == "hybrid" else "embedding_unavailable",
        chunks=chunks,
        budget=BudgetReport(
            max_chunks=5,
            char_budget=5520,
            used_chars=sum(len(c.text) for c in chunks),
            framework_chars=0,
            dropped_for_budget=(),
        ),
        withheld_unindexed=withheld,
        packs_indexed=False,
    )


class StubRetriever:
    """Returns a fixed result and counts the calls (or raises a given error)."""

    def __init__(self, result: RetrievalResult | None = None, error: Exception | None = None):
        self.result = result if result is not None else retrieval_of(chunk())
        self.error = error
        self.calls: list[str] = []

    async def retrieve(self, query: str) -> RetrievalResult:
        self.calls.append(query)
        if self.error is not None:
            raise self.error
        return self.result


def counting_boundary() -> Callable[[], str]:
    """A different, predictable boundary for every attempt."""
    counter = iter(range(1, 1000))
    return lambda: f"BND-{next(counter):024d}"


@asynccontextmanager
async def answering(
    tmp_path: Path,
    steps: list[Step],
    *,
    extra_notes: dict[str, str] | None = None,
    **stack_kwargs: Any,
) -> AsyncIterator[tuple[AnswerService, FakeModel, Stack]]:
    """The real retriever over the merged vaults (fake embedder), with a scripted model."""
    vault = merged_vault(tmp_path / "vault", extra=extra_notes)
    async with stack(vault, tmp_path / "index", **stack_kwargs) as s:
        model = FakeModel(list(steps))
        service = AnswerService(
            retriever=s.retriever, generator=model, clock=s.clock, boundary=counting_boundary()
        )
        yield service, model, s


def merged_vault(destination: Path, *, extra: Mapping[str, str] | None = None) -> Path:
    """The demo vault and the four hostile notes together, as the evaluation indexes them."""
    destination.mkdir(parents=True, exist_ok=True)
    for source in (DEMO_VAULT, ADVERSARIAL_M5_VAULT):
        for path in source.glob("*.md"):
            shutil.copyfile(path, destination / path.name)
    for name, text in (extra or {}).items():
        (destination / name).write_text(text, encoding="utf-8", newline="\n")
    return destination
