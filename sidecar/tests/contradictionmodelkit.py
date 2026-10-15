"""Helpers for the model half of the contradiction-detector tests (ADR-019, chunk 4b).

Everything is offline: the scripted `FakeModel` of `servicekit.py` plays the model, so a test
decides exactly what the "model" says and can inspect exactly what it was shown. No test here may
reach port 11434, and the egress guard stays on.
"""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from grc_sidecar.contradictions.models import ChunkText, DetectionReport, Finding, Pair
from grc_sidecar.contradictions.pairs import generate_pairs
from grc_sidecar.contradictions.service import build_report_with_model
from grc_sidecar.contradictions.verdict import GateView, ModelAsker

from .contradictionkit import SCENARIO_SECONDS, claims_of, norm, text_chunk
from .servicekit import FakeModel, Request, Step, counting_boundary

MODEL = "llama3.1:8b"
CANARY = "ALIBI-CD-CANARY-5c21e9"
T = TypeVar("T")


def run_scenario(scenario: Awaitable[T]) -> T:
    """Run an async scenario with a time limit, so a broken control fails instead of hanging."""
    return asyncio.run(asyncio.wait_for(scenario, SCENARIO_SECONDS))  # type: ignore[arg-type]


# A strong same-kind pair: the same subject, 90 days against 365 days.
A = text_chunk("Intro.\n\nSecurity logs are kept for 90 days.\n", chunk_id="a", path="a.md")
B = text_chunk("Intro.\n\nSecurity logs are kept for 365 days.\n", chunk_id="b", path="b.md")
A_QUOTE = "Security logs are kept for 90 days."
B_QUOTE = "Security logs are kept for 365 days."
# A weak overlap: the two subjects share the word `logs` and nothing else (the near-miss).
WEAK_A = text_chunk("| Application logs | 14 days |", chunk_id="wa", path="wa.md")
WEAK_B = text_chunk("| Security logs | 90 days |", chunk_id="wb", path="wb.md")
# Days against months: comparable, never a conflict.
DM_A = text_chunk("Chat is kept 90 days.", chunk_id="da", path="da.md")
DM_B = text_chunk("Chat is kept 6 months.", chunk_id="db", path="db.md")


def verdict_json(
    *,
    conflict: bool = True,
    relation: str = "same_subject_value_mismatch",
    subject: str = "Security logs",
    quote_a: str = "",
    quote_b: str = "",
    explanation: str = "The two statements set different values.",
    confidence: float = 0.9,
) -> str:
    return json.dumps(
        {
            "conflict": conflict,
            "relation": relation,
            "subject": subject,
            "quote_a": quote_a,
            "quote_b": quote_b,
            "explanation": explanation,
            "model_confidence": confidence,
        }
    )


def focus_of(request: Request, source_id: str) -> str:
    """The FOCUS statement the model was shown for S1 or S2."""
    found = re.search(
        rf"^\S+ BEGIN FOCUS {source_id}\n(.*?)\n\S+ END FOCUS {source_id}$",
        request.user,
        re.MULTILINE | re.DOTALL,
    )
    assert found is not None, f"no FOCUS block for {source_id}"
    return found.group(1)


def reply(
    conflict: bool = True, relation: str = "same_subject_value_mismatch", **kwargs: Any
) -> Callable[[Request], str]:
    """A model that does what it is told: it quotes both FOCUS statements exactly."""

    def step(request: Request) -> str:
        return verdict_json(
            conflict=conflict,
            relation=relation,
            quote_a=focus_of(request, "S1"),
            quote_b=focus_of(request, "S2"),
            **kwargs,
        )

    return step


def repeat(step: Step, count: int) -> list[Step]:
    return [step] * count


def asker_for(
    steps: list[Step],
    *,
    model_name: str = MODEL,
    gate: GateView | None = None,
) -> tuple[ModelAsker, FakeModel]:
    model = FakeModel(list(steps))
    return ModelAsker(model, model_name=model_name, gate=gate, boundary=counting_boundary()), model


def texts_of(*chunks: ChunkText) -> dict[str, ChunkText]:
    return {c.chunk_id: c for c in chunks}


def pair_for(first: ChunkText, second: ChunkText) -> Pair:
    """The one pair the code makes from the single claim in each chunk (a before b by path)."""
    claims = []
    for chunk in (first, second):
        (found,) = claims_of(chunk.text, chunk_id=chunk.chunk_id, start_line=chunk.start_line)
        claims.append(found.model_copy(update={"source_path": chunk.source_path}))
    ids = frozenset(c.chunk_id for c in (first, second))
    (pair,) = generate_pairs(claims, {c.claim_id: ids for c in claims})
    return pair


async def report_for(
    chunks: tuple[ChunkText, ...],
    steps: list[Step],
    *,
    cap: int = 100,
    model_name: str = MODEL,
    gate: GateView | None = None,
) -> tuple[DetectionReport, FakeModel]:
    """The detector's pure model path over in-memory chunks (every claim neighbours every chunk)."""
    texts = texts_of(*chunks)
    claims = [
        x.model_copy(update={"source_path": c.source_path})
        for c in chunks
        for x in claims_of(c.text, chunk_id=c.chunk_id, start_line=c.start_line)
    ]
    everywhere = {x.claim_id: frozenset(texts) for x in claims}
    asker, model = asker_for(steps, model_name=model_name, gate=gate)
    report = await build_report_with_model(texts, claims, everywhere, cap=cap, asker=asker)
    return report, model


def finding_with(report: DetectionReport, phrase_a: str, phrase_b: str) -> Finding:
    a, b = norm(phrase_a), norm(phrase_b)
    for finding in report.findings:
        if (a in finding.quote_a and b in finding.quote_b) or (
            b in finding.quote_a and a in finding.quote_b
        ):
            return finding
    raise AssertionError(f"no finding holds {phrase_a!r} and {phrase_b!r}")
