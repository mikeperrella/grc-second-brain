"""Builders for the answer-status tests: retrieved chunks and parsed model replies, by hand.

Everything the status decision looks at is a plain field, so a test can set exactly one fact at a
time. `reply` goes through the real strict parse, so a test cannot build a reply the parser would
refuse.
"""

import json
from collections.abc import Mapping

from grc_sidecar.llm.schemas import ModelAnswer, parse_model_answer
from grc_sidecar.retrieval.models import RetrievedChunk, SourceKind

DEFAULT_TEXT = (
    "## Screens\n\nLaptops use full-disk encryption and lock their screen after 10 minutes of "
    "inactivity.\n"
)
DEFAULT_QUOTE = "lock their screen after 10 minutes of inactivity"
FILE_SHA = "a" * 64


def chunk(
    text: str = DEFAULT_TEXT,
    *,
    start_line: int = 12,
    kind: SourceKind = "company_policy",
    overdue: bool = False,
    doc_id: str = "MQ-POL-006",
    source_path: str = "acceptable-use-policy.md",
    file_sha256: str = FILE_SHA,
    withdrawn: bool = False,
) -> RetrievedChunk:
    lines = text.count("\n")
    return RetrievedChunk(
        chunk_id=f"chunk-{doc_id}-{start_line}",
        source_path=source_path,
        doc_id=doc_id,
        start_line=start_line,
        end_line=start_line + lines,
        heading_path="",
        text=text,
        text_sha256="b" * 64,
        file_sha256=file_sha256,
        source_kind=kind,
        pack_id=None if kind == "company_policy" else "nist-sp800-53-r5",
        doc_type="policy" if kind == "company_policy" else "framework",
        role="seed",
        expanded_from=(),
        review_by=None,
        review_overdue=overdue,
        withdrawn=withdrawn,
        fused_score=0.5,
        lexical_rank=1,
        vector_rank=1,
    )


def sources(*chunks: RetrievedChunk) -> dict[str, RetrievedChunk]:
    """S1, S2, ... in order, as the prompt builder numbers them."""
    return {f"S{number}": item for number, item in enumerate(chunks, start=1)}


def reply(
    *,
    ids: tuple[str, ...] = ("S1", "S2"),
    answer: str = "Laptops lock after 10 minutes.",
    source_id: str | None = "S1",
    exact_quote: str = DEFAULT_QUOTE,
    model_confidence: float = 0.95,
    coverage: str = "full",
    conflicting_source_ids: tuple[str, ...] = (),
    no_evidence: bool = False,
) -> ModelAnswer:
    body: Mapping[str, object] = {
        "answer": answer,
        "source_id": source_id,
        "exact_quote": exact_quote,
        "model_confidence": model_confidence,
        "coverage": coverage,
        "conflicting_source_ids": list(conflicting_source_ids),
        "no_evidence": no_evidence,
    }
    return parse_model_answer(json.dumps(body), ids)
