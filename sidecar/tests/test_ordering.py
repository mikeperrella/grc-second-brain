"""The order candidates are considered in, for identifier-only questions (ADR-010, M4f)."""

from fractions import Fraction

from grc_sidecar.repositories.base import DocInfo
from grc_sidecar.retrieval.fusion import Fused
from grc_sidecar.retrieval.ordering import order_candidates
from grc_sidecar.retrieval.query import build_lexical_query
from grc_sidecar.retrieval.selection import Candidate
from grc_sidecar.vault.models import Chunk


def cand(name: str, heading: str, score: Fraction, *, lexical: int | None) -> Candidate:
    chunk = Chunk(
        chunk_id=name,
        source_path="p.md",
        doc_id="D",
        start_line=1,
        end_line=1,
        heading_path=heading,
        text=name,
        text_sha256=name,
    )
    document = DocInfo(
        source_path="p.md", collection="pack:p", doc_id="D", type="framework", review_by=None
    )
    item = Fused(chunk=chunk, score=score, lexical_rank=lexical, vector_rank=None)
    return Candidate(item=item, document=document, kind="framework_reference", withdrawn=False)


def ids(candidates: list[Candidate]) -> list[str]:
    return [c.item.chunk.chunk_id for c in candidates]


POOL = [
    cand("vector-only", "Other > Unrelated", Fraction(1, 61), lexical=None),
    cand("keyword", "Other > Mentions AC-2 in passing", Fraction(1, 62), lexical=2),
    cand("enhancement", "AC > AC-2(4) Automated Audit Actions", Fraction(1, 63), lexical=3),
    cand("control", "AC > AC-2 Account Management", Fraction(1, 70), lexical=9),
]


def test_an_identifier_question_puts_the_controls_own_chunk_first(  # [NEG] mutation row 29
) -> None:
    ordered = order_candidates(POOL, build_lexical_query("AC-2"))
    assert ids(ordered) == ["control", "keyword", "enhancement", "vector-only"]


def test_an_ordinary_question_keeps_the_fusion_order() -> None:
    ordered = order_candidates(POOL, build_lexical_query("account management policy"))
    assert ids(ordered) == ["vector-only", "keyword", "enhancement", "control"]


def test_a_question_with_words_and_an_identifier_is_not_identifier_only() -> None:
    ordered = order_candidates(POOL, build_lexical_query("AC-2 account management"))
    assert ids(ordered) == ["vector-only", "keyword", "enhancement", "control"]


def test_ties_inside_a_tier_break_by_chunk_id() -> None:
    pool = [
        cand("b", "x", Fraction(1, 61), lexical=1),
        cand("a", "x", Fraction(1, 61), lexical=1),
    ]
    assert ids(order_candidates(pool, build_lexical_query("AC-2"))) == ["a", "b"]
    assert ids(order_candidates(pool, build_lexical_query("words"))) == ["a", "b"]


def test_all_written_forms_of_the_identifier_count_as_naming_it() -> None:
    pool = [cand("padded", "AC > AC-02 Account Management", Fraction(1, 70), lexical=5), *POOL[:2]]
    ordered = order_candidates(pool, build_lexical_query("AC-2"))
    assert ids(ordered)[0] == "padded"
