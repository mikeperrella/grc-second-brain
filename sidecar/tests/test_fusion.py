"""Reciprocal Rank Fusion: hand-computed scores, ties and determinism (ADR-010, M4e)."""

import hashlib
from fractions import Fraction
from typing import Any

from grc_sidecar.repositories.base import LexicalHit, ScoredChunk
from grc_sidecar.retrieval.fusion import RRF_K, fuse
from grc_sidecar.vault.models import Chunk


def chunk(name: str) -> Chunk:
    # chunk_id is what orders ties, so the tests choose ids whose text order is obvious.
    return Chunk(
        chunk_id=name,
        source_path="p.md",
        doc_id="D",
        start_line=1,
        end_line=1,
        heading_path="",
        text=name,
        text_sha256=name,
    )


def lex(*names: str) -> list[LexicalHit]:
    """Keyword hits in the order given, best first (bm25 rises with the position)."""
    return [LexicalHit(chunk=chunk(n), bm25=-100.0 + i) for i, n in enumerate(names)]


def vec(*names: str) -> list[ScoredChunk]:
    return [ScoredChunk(chunk=chunk(n), distance=0.1 * (i + 1)) for i, n in enumerate(names)]


def test_the_constant_is_the_published_one() -> None:
    assert RRF_K == 60


def test_scores_match_a_hand_computation() -> None:  # [NEG] mutation row 11
    # keyword: A, B   vector: B, C
    fused = {item.chunk.chunk_id: item for item in fuse(lex("A", "B"), vec("B", "C"))}
    assert fused["A"].score == Fraction(1, 61)
    assert fused["B"].score == Fraction(1, 62) + Fraction(1, 61)
    assert fused["C"].score == Fraction(1, 62)
    assert (fused["A"].lexical_rank, fused["A"].vector_rank) == (1, None)
    assert (fused["B"].lexical_rank, fused["B"].vector_rank) == (2, 1)
    assert (fused["C"].lexical_rank, fused["C"].vector_rank) == (None, 2)


def test_a_chunk_found_by_both_lists_beats_one_found_by_one() -> None:
    order = [item.chunk.chunk_id for item in fuse(lex("A", "B"), vec("B", "C"))]
    assert order == ["B", "A", "C"]


def test_equal_scores_break_ties_by_chunk_id() -> None:  # [NEG] mutation row 10
    # Z is keyword rank 1 only, A is vector rank 1 only: exactly equal scores of 1/61.
    result = fuse(lex("Z"), vec("A"))
    assert result[0].score == result[1].score == Fraction(1, 61)
    assert [item.chunk.chunk_id for item in result] == ["A", "Z"]


def test_order_is_independent_of_input_order() -> None:  # [NEG] mutation row 10
    names = [f"c{n}" for n in range(12)]
    lexical = [LexicalHit(chunk=chunk(n), bm25=-1.0) for n in names]  # every bm25 equal
    vector = [ScoredChunk(chunk=chunk(n), distance=0.5) for n in names]  # every distance equal
    expected = [item.chunk.chunk_id for item in fuse(lexical, vector)]
    assert expected == sorted(names)
    for seed in range(20):
        # A different, reproducible order of each input list for every seed.
        def scramble(items: list[Any], seed: int = seed) -> list[Any]:
            return sorted(
                items, key=lambda x: hashlib.sha256(f"{seed}{x.chunk.chunk_id}".encode()).digest()
            )

        again = fuse(scramble(lexical), scramble(vector))
        assert [item.chunk.chunk_id for item in again] == expected
        assert [item.lexical_rank for item in again] == [
            item.lexical_rank for item in fuse(lexical, vector)
        ]


def test_one_list_alone_keeps_its_order_and_either_list_may_be_empty() -> None:
    assert [i.chunk.chunk_id for i in fuse(lex("B", "A"), [])] == ["B", "A"]
    assert [i.chunk.chunk_id for i in fuse([], vec("B", "A"))] == ["B", "A"]
    assert fuse([], []) == []


def test_scores_are_exact_fractions() -> None:
    # Exact arithmetic is what lets "equal score" mean equal, so the tie-break can be trusted.
    result = fuse(lex("A", "X", "Y"), vec("B", "X", "Y"))
    assert all(isinstance(item.score, Fraction) for item in result)
