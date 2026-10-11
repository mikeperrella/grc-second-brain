"""Slots, the character budget and the framework quota (ADR-010, M4f). Pure functions, no I/O."""

from fractions import Fraction

import pytest

from grc_sidecar.repositories.base import DocInfo
from grc_sidecar.retrieval.fusion import Fused
from grc_sidecar.retrieval.models import SourceKind
from grc_sidecar.retrieval.selection import (
    COMPANY_MIN_SEEDS,
    DEFAULT_CHAR_BUDGET,
    FRAMEWORK_MAX_CHUNKS,
    Candidate,
    expansion_slots,
    framework_char_cap,
    framework_chunk_cap,
    seed_slots,
    select_seeds,
)
from grc_sidecar.vault.models import Chunk


def candidate(name: str, kind: SourceKind, size: int, rank: int) -> Candidate:
    chunk = Chunk(
        chunk_id=name,
        source_path=f"{name}.md",
        doc_id=name,
        start_line=1,
        end_line=1,
        heading_path="",
        text="x" * size,
        text_sha256=name,
    )
    document = DocInfo(
        source_path=chunk.source_path,
        collection="vault" if kind == "company_policy" else "pack:p",
        doc_id=name,
        type="policy" if kind == "company_policy" else "framework",
        review_by=None,
        file_sha256="f" * 64,
    )
    item = Fused(chunk=chunk, score=Fraction(1, 60 + rank), lexical_rank=rank, vector_rank=None)
    return Candidate(item=item, document=document, kind=kind, withdrawn=False)


def company(name: str, size: int = 100, rank: int = 1) -> Candidate:
    return candidate(name, "company_policy", size, rank)


def framework(name: str, size: int = 100, rank: int = 1) -> Candidate:
    return candidate(name, "framework_reference", size, rank)


def names(selection_seeds: tuple[Candidate, ...]) -> list[str]:
    return [c.item.chunk.chunk_id for c in selection_seeds]


def test_the_constants_are_the_decided_ones() -> None:
    # 4,000 tokens x 1.38 characters per token (the worst measured ratio), as an integer.
    assert DEFAULT_CHAR_BUDGET == 5520
    assert FRAMEWORK_MAX_CHUNKS == 2
    assert COMPANY_MIN_SEEDS == 2
    assert framework_char_cap(5520) == 2208  # 40% of the budget


def test_slots_for_the_default_five() -> None:
    assert expansion_slots(5) == 1
    assert seed_slots(5) == 4
    assert framework_chunk_cap(5) == 2


@pytest.mark.parametrize(
    ("max_chunks", "expansion", "seeds", "framework"),
    [(1, 0, 1, 0), (2, 1, 1, 0), (3, 1, 2, 0), (4, 1, 3, 1), (5, 1, 4, 2), (10, 1, 9, 2)],
)
def test_slot_arithmetic(max_chunks: int, expansion: int, seeds: int, framework: int) -> None:
    assert expansion_slots(max_chunks) == expansion
    assert seed_slots(max_chunks) == seeds
    assert framework_chunk_cap(max_chunks) == framework


def test_seeds_are_taken_in_rank_order_up_to_the_slots() -> None:
    ranked = [company(f"c{i}", rank=i) for i in range(1, 9)]
    selection = select_seeds(ranked, max_chunks=5, char_budget=DEFAULT_CHAR_BUDGET)
    assert names(selection.seeds) == ["c1", "c2", "c3", "c4"]  # one slot is kept for expansion


def test_framework_text_never_exceeds_two_chunks(  # [NEG] mutation row 2
) -> None:
    ranked = [framework(f"f{i}", rank=i) for i in range(1, 6)] + [
        company(f"c{i}", rank=5 + i) for i in range(1, 6)
    ]
    selection = select_seeds(ranked, max_chunks=5, char_budget=DEFAULT_CHAR_BUDGET)
    kinds = [c.kind for c in selection.seeds]
    assert kinds.count("framework_reference") == 2
    assert kinds.count("company_policy") == 2
    assert names(selection.seeds) == ["f1", "f2", "c1", "c2"]


def test_company_policy_keeps_its_guaranteed_seeds(  # [NEG] mutation row 2
) -> None:
    ranked = [framework(f"f{i}", rank=i) for i in range(1, 9)] + [company("c1", rank=9)]
    ranked += [company("c2", rank=10)]
    selection = select_seeds(ranked, max_chunks=5, char_budget=DEFAULT_CHAR_BUDGET)
    assert [c.kind for c in selection.seeds].count("company_policy") == COMPANY_MIN_SEEDS


def test_framework_text_is_capped_at_forty_percent_of_the_budget(  # [NEG] mutation row 2
) -> None:
    budget = 1000
    cap = framework_char_cap(budget)  # 400
    ranked = [framework("f1", size=300, rank=1), framework("f2", size=300, rank=2)]
    ranked += [company("c1", size=100, rank=3)]
    selection = select_seeds(ranked, max_chunks=5, char_budget=budget)
    assert names(selection.seeds) == ["f1", "c1"]  # f2 would make framework 600 > 400
    assert selection.framework_chars == 300 <= cap


def test_a_framework_chunk_that_alone_exceeds_the_cap_is_never_taken() -> None:  # [NEG]
    selection = select_seeds([framework("big", size=500)], max_chunks=5, char_budget=1000)
    assert selection.seeds == ()


def test_unused_framework_quota_goes_to_company_policy() -> None:
    ranked = [company(f"c{i}", rank=i) for i in range(1, 6)] + [framework("f1", rank=9)]
    selection = select_seeds(ranked, max_chunks=5, char_budget=DEFAULT_CHAR_BUDGET)
    assert names(selection.seeds) == ["c1", "c2", "c3", "c4"]


def test_a_chunk_that_does_not_fit_is_skipped_and_the_next_is_tried(  # [NEG] mutation row 18
) -> None:
    ranked = [
        company("small1", size=300, rank=1),
        company("huge", size=900, rank=2),
        company("small2", size=300, rank=3),
    ]
    selection = select_seeds(ranked, max_chunks=5, char_budget=1000)
    assert names(selection.seeds) == ["small1", "small2"]
    assert selection.dropped_for_budget == ("huge",)
    assert selection.used_chars == 600


def test_the_budget_is_never_exceeded(  # [NEG] mutation row 18
) -> None:
    ranked = [company(f"c{i}", size=400, rank=i) for i in range(1, 9)]
    for budget in (1, 399, 400, 800, 1200, 5520):
        selection = select_seeds(ranked, max_chunks=9, char_budget=budget)
        assert selection.used_chars <= budget
        assert selection.used_chars == sum(len(c.item.chunk.text) for c in selection.seeds)


def test_nothing_is_ever_truncated() -> None:  # [NEG] mutation row 18
    ranked = [company("a", size=700, rank=1), company("b", size=700, rank=2)]
    selection = select_seeds(ranked, max_chunks=5, char_budget=1000)
    assert [len(c.item.chunk.text) for c in selection.seeds] == [700]  # whole or not at all


def test_an_empty_candidate_list_selects_nothing() -> None:
    selection = select_seeds([], max_chunks=5, char_budget=DEFAULT_CHAR_BUDGET)
    assert selection.seeds == () and selection.used_chars == 0
