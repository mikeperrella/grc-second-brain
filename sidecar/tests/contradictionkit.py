"""Helpers for the contradiction-detector tests (ADR-019): the evaluation set, small chunk builders,
and one call that indexes a vault offline and runs the detector over it.

Everything is offline: the fake embedder, an index in a temporary folder, no model.
"""

import asyncio
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from grc_sidecar.contradictions.claims import extract_claims
from grc_sidecar.contradictions.models import ChunkText, Claim, DetectionReport, Finding
from grc_sidecar.contradictions.service import ContradictionDetector

from .retrievalkit import stack
from .vaultkit import CONTRADICTION_SET

SCENARIO_SECONDS = 30  # every async scenario has a time limit (deviation 42)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class Place(_Strict):
    path: str
    start_line: int
    end_line: int
    phrase: str


class Item(_Strict):
    id: str
    split: Literal["dev", "heldout"]
    category: str
    chunk_a: Place
    chunk_b: Place
    label: Literal["conflict", "no_conflict"]
    expected_status: Literal["VALUE_CONFLICT", "CANDIDATE", "NOT_PAIRED", "EXCLUDED"]
    offline_status: Literal["CANDIDATE", "NOT_PAIRED", "EXCLUDED"]
    notes: str


class KnownCase(_Strict):
    id: str
    note: str
    vault: str
    chunk_a: Place
    chunk_b: Place


class ContradictionSet(_Strict):
    description: str
    schema_doc: dict[str, str] = Field(alias="schema")
    known_cases: tuple[KnownCase, ...]
    items: tuple[Item, ...]


def load_set() -> ContradictionSet:
    return ContradictionSet.model_validate(json.loads(CONTRADICTION_SET.read_text("utf-8")))


def norm(text: str) -> str:
    return " ".join(text.split())


def run_detector(
    vault: Path, index_dir: Path, *, cap: int = 1000, scope: str | None = None
) -> DetectionReport:
    """Index `vault` with the fake embedder, then run the code half over it."""

    async def scenario() -> DetectionReport:
        async with stack(vault, index_dir) as s:
            return await ContradictionDetector(s.vault_repo).detect(scope=scope, cap=cap)

    return asyncio.run(asyncio.wait_for(scenario(), SCENARIO_SECONDS))


def find_finding(report: DetectionReport, phrase_a: str, phrase_b: str) -> Finding | None:
    """The finding whose two quotes hold these two phrases, in either order."""
    a, b = norm(phrase_a), norm(phrase_b)
    for finding in report.findings:
        if (a in finding.quote_a and b in finding.quote_b) or (
            b in finding.quote_a and a in finding.quote_b
        ):
            return finding
    return None


def mentions(report: DetectionReport, phrase: str) -> bool:
    wanted = norm(phrase)
    return any(wanted in f.quote_a or wanted in f.quote_b for f in report.findings)


def text_chunk(
    text: str, *, start_line: int = 1, chunk_id: str = "c1", path: str = "note.md"
) -> ChunkText:
    return ChunkText(chunk_id=chunk_id, source_path=path, start_line=start_line, text=text)


def claims_of(text: str, *, start_line: int = 1, chunk_id: str = "c1") -> list[Claim]:
    return extract_claims(text_chunk(text, start_line=start_line, chunk_id=chunk_id))
