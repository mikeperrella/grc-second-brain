"""The FTS5 table: written and deleted together with the chunks, and the tokenizer of ADR-010.

M3 has no lexical query method (M4 owns query syntax and scoring), so these tests query the
table directly. Identifier queries are phrase-quoted, which is the rule M4 must follow.
"""

import asyncio
from pathlib import Path

import pytest

from grc_sidecar.repositories.base import ScanSnapshot

from .repokit import axis, make_chunk, make_doc, open_raw, rowids, table_counts
from .test_vector_repository import opened


def fts_rowids(index_path: Path, query: str) -> set[int]:
    con = open_raw(index_path)
    try:
        sql = "SELECT rowid FROM fts_chunks WHERE fts_chunks MATCH ?"
        return {int(r[0]) for r in con.execute(sql, (query,))}
    finally:
        con.close()


@pytest.fixture
def index_path(tmp_path: Path) -> Path:
    return tmp_path / "index.sqlite3"


def test_edit_leaves_no_stale_fts_rows(index_path: Path) -> None:  # [NEG] mutation row 22
    old = make_doc(version="v1")
    new = make_doc(version="v2")

    async def scenario() -> None:
        async with opened(index_path) as repo:
            await repo.replace_document(
                old,
                [make_chunk(old, 5, "zebrafish retention rule"), make_chunk(old, 9, "other")],
                [axis(0), axis(1)],
                [],
            )
            assert fts_rowids(index_path, '"zebrafish"')
            await repo.replace_document(new, [make_chunk(new, 5, "quokka")], [axis(2)], [])

    asyncio.run(scenario())
    assert fts_rowids(index_path, '"zebrafish"') == set()  # the old words are gone
    assert len(fts_rowids(index_path, '"quokka"')) == 1
    assert table_counts(index_path)["fts_chunks"] == 1
    assert rowids(index_path, "fts_chunks") == rowids(index_path, "chunks")


def test_fts_rows_are_deleted_with_the_chunks_by_begin_sync_and_remove(index_path: Path) -> None:
    edited = make_doc("a.md", doc_id="A")
    removed = make_doc("b.md", doc_id="B")

    async def scenario() -> None:
        async with opened(index_path) as repo:
            await repo.replace_document(
                edited, [make_chunk(edited, 5, "alpha words")], [axis(0)], []
            )
            await repo.replace_document(
                removed, [make_chunk(removed, 5, "beta words")], [axis(1)], []
            )
            assert fts_rowids(index_path, '"words"')
            await repo.begin_sync(
                ScanSnapshot(
                    chunker_version="1",
                    documents={
                        "a.md": make_doc("a.md", doc_id="A", version="v2").file_sha256,
                        "b.md": removed.file_sha256,
                    },
                )
            )
            assert fts_rowids(index_path, '"alpha"') == set()  # a.md is stale: no FTS row
            assert fts_rowids(index_path, '"beta"')
            await repo.remove_document("b.md")

    asyncio.run(scenario())
    assert fts_rowids(index_path, '"words"') == set()
    assert table_counts(index_path)["fts_chunks"] == 0


def test_the_heading_path_is_searchable_too(index_path: Path) -> None:
    doc = make_doc()

    async def scenario() -> None:
        async with opened(index_path) as repo:
            chunk = make_chunk(doc, 5, "Reviewed quarterly.", heading="AC-2 Account Management")
            await repo.replace_document(doc, [chunk], [axis(0)], [])

    asyncio.run(scenario())
    assert len(fts_rowids(index_path, '"AC-2"')) == 1


# The corpus and expectations behind the ADR-010 tokenizer table, for the chosen tokenizer
# (`unicode61 tokenchars '-'`). Row ids are listed in CORPUS order, starting at 1.
CORPUS = [
    "AC-2 Account Management",  # 1
    "AC-2(1) Automated System Account Management",  # 2
    "AC-20 Use of External Systems",  # 3
    "CC6.1 Logical and Physical Access Controls",  # 4
    "CC6.10 trap row with a longer id",  # 5
    "A.5.15 Access control",  # 6
    "A.5.152 trap row with a longer id",  # 7
    "IA-2 Identification and Authentication (Organizational Users)",  # 8
    "IA-2(1) Multi-factor authentication to privileged accounts",  # 9
    "3.1.1 Limit system access to authorized users",  # 10
    "3.1.10 Use session lock",  # 11
    "13.1.1 Boundary protection trap row",  # 12
    "Access reviews satisfy AC-2, CC6.1. and also A.5.15.",  # 13
    "Policy mapping (AC-2) per quarterly review; see IA-2(1).",  # 14
    "03.01.01 Account Management (SP 800-171 Rev 3 format)",  # 15
    "Per ISO 27001 5.15 the owner reviews access.",  # 16
    "We require multi factor authentication.",  # 17
    "Distractor: ship AC 2 units, ref 3 1 1, lot CC6 1, batch A 5 15, unit IA 2.",  # 18
]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ('"AC-2"', {1, 2, 13, 14}),  # AC-2(1) is a child of AC-2; AC-20 (3) and "AC 2" (18) are not
        ('"ac-2"', {1, 2, 13, 14}),  # case-insensitive
        ('"AC-2(1)"', {2}),
        ('"CC6.1"', {4, 13, 18}),  # row 13 ends the sentence with "CC6.1." and is found
        ('"A.5.15"', {6, 13, 18}),
        ('"IA-2"', {8, 9, 14}),
        ('"3.1.1"', {10, 18}),  # does NOT find 03.01.01 (row 15): M4 normalizes that
        ('"03.01.01"', {15}),
        ('"5.15"', {6, 13, 16, 18}),  # also finds A.5.15 as a side effect, never relied on
        ('"multi-factor"', {9}),  # '-' keeps the hyphenated word as one token
        ('"multi factor"', {17}),  # ...so the two spellings do not meet lexically
    ],
)
def test_identifier_queries_under_the_chosen_tokenizer(
    index_path: Path, query: str, expected: set[int]
) -> None:
    async def scenario() -> None:
        async with opened(index_path):
            pass

    asyncio.run(scenario())
    con = open_raw(index_path)
    try:
        for number, text in enumerate(CORPUS, start=1):
            con.execute(
                "INSERT INTO fts_chunks (rowid, text, heading_path) VALUES (?, ?, '')",
                (number, text),
            )
        sql = "SELECT rowid FROM fts_chunks WHERE fts_chunks MATCH ?"
        found = {int(r[0]) for r in con.execute(sql, (query,))}
    finally:
        con.close()
    assert found == expected


def test_the_table_uses_the_tokenizer_recorded_in_adr_010(index_path: Path) -> None:
    async def scenario() -> None:
        async with opened(index_path):
            pass

    asyncio.run(scenario())
    con = open_raw(index_path)
    try:
        sql = con.execute("SELECT sql FROM sqlite_master WHERE name = 'fts_chunks'").fetchone()[0]
    finally:
        con.close()
    assert "unicode61 tokenchars '-'" in sql
    assert "trigram" not in sql
