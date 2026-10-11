"""`VectorRepository.lexical_search` and identifier queries against real FTS5 rows (M4d)."""

import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest

from grc_sidecar.errors import IndexStoreError
from grc_sidecar.retrieval.query import build_lexical_query

from .leakcheck import assert_no_leak
from .repokit import axis, make_chunk, make_doc, table_counts
from .test_vector_repository import opened

# Rows that trap every identifier form: longer IDs that start the same way, the same digits
# spaced out, and the sentence-final dot. Id -> text.
IDS: dict[int, str] = {
    1: "03.01.01 Account Management",  # 800-171 Rev 3 style
    2: "3.1.1 Account Management in the older style",
    3: "03.01.10 Device Lock",
    4: "13.1.1 Boundary Protection",
    5: "3 1 1 spaced out like a part number",
    6: "AC-02 Account Management written padded",
    7: "AC-2(1) Automated System Account Management",
    8: "AC-20 Use of External Systems",
    9: "A.5.15 Access control",
    10: "5.15 Access rules",
    11: "CC6.1 Logical access safeguards",
    12: "CC6.10 Something else entirely",
    13: "PR.AA-01 Identities and credentials",
    14: "Policy cites the control CC6.1.",
    15: "HR-POL-005 Data Retention Policy",
    16: "the AC-2 control as plain ac-2 text",
}


@pytest.fixture
def index_path(tmp_path: Path) -> Path:
    return tmp_path / "index.sqlite3"


async def _load(
    index_path: Path, rows: dict[int, str], *, order: Sequence[int] | None = None
) -> None:
    doc = make_doc()
    starts = list(order) if order is not None else list(rows)
    chunks = [make_chunk(doc, start, rows[start]) for start in starts]
    async with opened(index_path) as repo:
        await repo.replace_document(doc, chunks, [axis(i % 4) for i in range(len(chunks))], [])


async def _ids(index_path: Path, user_text: str, k: int = 50) -> list[int]:
    async with opened(index_path) as repo:
        hits = await repo.lexical_search(build_lexical_query(user_text).match, k)
    return [hit.chunk.start_line for hit in hits]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("3.1.1", {1, 2}),  # both written forms, not 03.01.10, 13.1.1 or the spaced-out row...
        ("03.01.01", {1, 2}),
        ("5.15", {9, 10}),
        ("A.5.15", {9, 10}),
        ("AC-2", {6, 7, 16}),  # AC-02, the child AC-2(1) and the plain text; not AC-20
        ("AC-02", {6, 7, 16}),
        ("ac-2", {6, 7, 16}),
        ("AC-2(1)", {7}),
        ("CC6.1", {11, 14}),  # including the sentence-final "CC6.1."; not CC6.10
        ("SOC2:CC6.1", {11, 14}),
        ("PR.AA-01", {13}),
        ("HR-POL-005", {15}),
    ],
)
def test_every_written_form_finds_the_same_rows(
    index_path: Path, query: str, expected: set[int]
) -> None:
    asyncio.run(_load(index_path, IDS))
    found = set(asyncio.run(_ids(index_path, query)))
    # "3 1 1 spaced out" (row 5) is a phrase of the tokens 3, 1, 1: the digits-with-spaces trap
    # is a known, documented false positive of the dot-as-separator tokenizer (ADR-010).
    assert found - {5} == expected


def test_a_longer_identifier_is_not_a_hit(index_path: Path) -> None:  # [NEG]
    asyncio.run(_load(index_path, IDS))
    assert 3 not in asyncio.run(_ids(index_path, "3.1.1"))  # 03.01.10
    assert 4 not in asyncio.run(_ids(index_path, "3.1.1"))  # 13.1.1
    assert 8 not in asyncio.run(_ids(index_path, "AC-2"))  # AC-20
    assert 12 not in asyncio.run(_ids(index_path, "CC6.1"))  # CC6.10


def test_the_same_identifier_in_any_form_returns_the_same_rows_in_the_same_order(
    index_path: Path,
) -> None:
    asyncio.run(_load(index_path, IDS))
    assert asyncio.run(_ids(index_path, "3.1.1")) == asyncio.run(_ids(index_path, "03.01.01"))
    assert asyncio.run(_ids(index_path, "AC-2")) == asyncio.run(_ids(index_path, "ac-02"))


# ----- ordering ---------------------------------------------------------------------------------


def test_order_does_not_depend_on_insertion_order(index_path: Path, tmp_path: Path) -> None:
    rows = {n: "retention of security logs policy" for n in range(1, 9)}  # identical text: ties
    asyncio.run(_load(index_path, rows))
    other = tmp_path / "other.sqlite3"
    asyncio.run(_load(other, rows, order=[5, 3, 8, 1, 7, 2, 6, 4]))
    first = asyncio.run(_ids(index_path, "retention"))
    second = asyncio.run(_ids(other, "retention"))
    assert len(first) == 8
    # Same chunk_ids in both stores (same document, same lines), so the tie-break gives one order.
    assert first == second


def test_equal_scores_are_ordered_by_chunk_id(index_path: Path) -> None:
    rows = {n: "identical text about retention" for n in range(1, 9)}
    asyncio.run(_load(index_path, rows))

    async def ids_and_chunk_ids() -> tuple[list[str], list[float]]:
        async with opened(index_path) as repo:
            hits = await repo.lexical_search('"retention"', 50)
        return [h.chunk.chunk_id for h in hits], [h.bm25 for h in hits]

    chunk_ids, scores = asyncio.run(ids_and_chunk_ids())
    assert len(set(scores)) == 1, "the fixture must produce equal scores"
    assert chunk_ids == sorted(chunk_ids)


def test_a_better_match_ranks_first_and_k_limits_the_result(index_path: Path) -> None:
    rows = {
        1: "retention retention retention of logs",
        2: "a single mention of retention among many other unrelated words here today",
        3: "nothing relevant",
    }
    asyncio.run(_load(index_path, rows))
    assert asyncio.run(_ids(index_path, "retention")) == [1, 2]
    assert asyncio.run(_ids(index_path, "retention", k=1)) == [1]


# ----- edges and safety -------------------------------------------------------------------------


def test_an_empty_match_returns_nothing_and_a_bad_k_is_refused(index_path: Path) -> None:  # [NEG]
    asyncio.run(_load(index_path, IDS))

    async def scenario() -> None:
        async with opened(index_path) as repo:
            assert await repo.lexical_search("", 5) == []
            for bad in (0, -1, 10**6):
                with pytest.raises(ValueError, match="out of range"):
                    await repo.lexical_search('"x"', bad)

    asyncio.run(scenario())


def test_a_raw_bad_match_string_is_a_clean_domain_error(index_path: Path) -> None:  # [NEG]
    # The builder never produces this. If something else ever passes raw text, the repository must
    # fail with a fixed-message domain error that carries none of the text.
    asyncio.run(_load(index_path, IDS))
    secret = "CONFIDENTIAL-QUERY-TEXT"

    async def scenario() -> IndexStoreError:
        async with opened(index_path) as repo:
            with pytest.raises(IndexStoreError) as info:
                await repo.lexical_search(f'"{secret}', 5)  # unterminated string
        return info.value

    assert_no_leak(asyncio.run(scenario()), secret)


def test_user_text_cannot_reach_sql(index_path: Path) -> None:  # [NEG]
    asyncio.run(_load(index_path, IDS))
    before = table_counts(index_path)
    for text in ["'; DROP TABLE chunks; --", '" OR 1=1 --', "x') UNION SELECT 1 --"]:
        asyncio.run(_ids(index_path, text))
    assert table_counts(index_path) == before


def test_a_replaced_document_is_not_found_by_its_old_words(index_path: Path) -> None:  # [NEG]
    old, new = make_doc(version="v1"), make_doc(version="v2")

    async def scenario() -> tuple[list[int], list[int]]:
        async with opened(index_path) as repo:
            await repo.replace_document(old, [make_chunk(old, 5, "zebrafish rule")], [axis(0)], [])
            await repo.replace_document(new, [make_chunk(new, 5, "quokka rule")], [axis(1)], [])
            gone = await repo.lexical_search('"zebrafish"', 5)
            there = await repo.lexical_search('"quokka"', 5)
        return [h.chunk.start_line for h in gone], [h.chunk.start_line for h in there]

    assert asyncio.run(scenario()) == ([], [5])


def test_a_hit_carries_the_whole_chunk(index_path: Path) -> None:
    asyncio.run(_load(index_path, {7: "retention of security logs"}))

    async def scenario() -> None:
        async with opened(index_path) as repo:
            (hit,) = await repo.lexical_search('"retention"', 5)
        chunk = hit.chunk
        assert (chunk.source_path, chunk.start_line, chunk.end_line) == ("policies/access.md", 7, 7)
        assert chunk.text == "retention of security logs"
        assert chunk.heading_path == "Access Control"
        assert hit.bm25 < 0  # SQLite's convention: a better match is a lower (negative) number

    asyncio.run(scenario())
