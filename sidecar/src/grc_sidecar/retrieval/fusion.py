"""Reciprocal Rank Fusion of keyword and vector results (ADR-010, M4e).

`score(chunk) = sum over the lists it appears in of 1 / (K + rank)`, ranks 1-based, K = 60 (the
constant from the original RRF paper; fixed, not tuned here).

Why RRF: a BM25 score and a cosine distance are on different scales, so adding them needs a tuned
weight. RRF uses only the ORDER of each list, has one fixed constant, and still works when only
one list exists (the keyword-only mode).

Determinism, because the same question must give the same answer on every run and every machine:

* each input list is sorted by (its own score, chunk_id) before ranks are assigned, so ties inside a
  list never depend on row ids or insertion order;
* scores are exact fractions, so two chunks with equal rank sums have EXACTLY equal scores;
* the output order is (-score, chunk_id), with chunk_id compared as plain text.

Known limit: rank-only fusion ignores how strong a match was. A weak keyword rank 1 counts the same
as a strong one, and nothing here is a relevance threshold.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Final

from grc_sidecar.repositories.base import LexicalHit, ScoredChunk
from grc_sidecar.vault.models import Chunk

RRF_K: Final = 60


@dataclass(frozen=True)
class Fused:
    chunk: Chunk
    score: Fraction
    lexical_rank: int | None
    vector_rank: int | None


def fuse(lexical: Sequence[LexicalHit], vector: Sequence[ScoredChunk]) -> list[Fused]:
    """Merge the two ranked lists. Either may be empty."""
    lexical_ranked = sorted(lexical, key=lambda hit: (hit.bm25, hit.chunk.chunk_id))
    vector_ranked = sorted(vector, key=lambda hit: (hit.distance, hit.chunk.chunk_id))

    chunks: dict[str, Chunk] = {}
    lexical_rank: dict[str, int] = {}
    vector_rank: dict[str, int] = {}
    for rank, lexical_hit in enumerate(lexical_ranked, start=1):
        chunks[lexical_hit.chunk.chunk_id] = lexical_hit.chunk
        lexical_rank[lexical_hit.chunk.chunk_id] = rank
    for rank, vector_hit in enumerate(vector_ranked, start=1):
        chunks[vector_hit.chunk.chunk_id] = vector_hit.chunk
        vector_rank[vector_hit.chunk.chunk_id] = rank

    fused: list[Fused] = []
    for chunk_id, chunk in chunks.items():
        score = Fraction(0)
        for ranks in (lexical_rank, vector_rank):
            if chunk_id in ranks:
                score += Fraction(1, RRF_K + ranks[chunk_id])
        fused.append(
            Fused(
                chunk=chunk,
                score=score,
                lexical_rank=lexical_rank.get(chunk_id),
                vector_rank=vector_rank.get(chunk_id),
            )
        )
    fused.sort(key=lambda item: (-item.score, item.chunk.chunk_id))
    return fused
