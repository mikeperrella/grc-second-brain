"""The FTS5 query builder: untrusted text never changes the meaning of a query (ADR-010, M4d).

This is a security control. The tests run hostile text through the builder, run the result against a
real FTS5 table, and compare the rows with an INDEPENDENT oracle (`oracle_fts.py`).
"""

import ast
import asyncio
import sqlite3
from pathlib import Path

import pytest

from grc_sidecar.repositories.sqlite_vec_repo import _FTS_DDL
from grc_sidecar.retrieval.query import MAX_TERMS, build_lexical_query, quote_fts5

from . import oracle_fts
from .repokit import axis, make_chunk, make_doc
from .test_identifiers import POSITIVE
from .test_vector_repository import opened

# id (the chunk's start line) -> text. They contain the words the hostile queries abuse.
CORPUS: dict[int, str] = {
    1: "near the end and or not",
    2: "heading_path holds the title",
    3: "ac-2 account management",
    4: "x y z letters",
    5: "secret OR 1 injection",
    6: 'star * caret ^ colon : paren ( ) quote " end',
    7: "plain unrelated words",
    8: "x 1 y",
    9: "NEAR AND OR NOT",
    10: "path star caret x-y -x",
    11: "данные хранения",
    12: "unbalanced quote",
}

# Text that would be syntax, an error, or a different question if it reached FTS5 unquoted. None of
# it uses stop words or identifier-shaped words: the oracle has no notion of those.
HOSTILE = [
    "NEAR(x y)",
    "NEAR/3",
    "x NEAR y",
    "x*",
    "path*",
    "*",
    "^x",
    "heading_path:ac-2",
    "col:x",
    "-x",
    "x -y",
    "x OR 1",
    "x AND NOT",
    "x NOT y",
    "OR",
    "AND",
    "NOT",
    '"',
    '""',
    '"unbalanced',
    'unbalanced"',
    '"x y"',
    'x"y',
    'x""y',
    "(",
    ")",
    "(x",
    "x)",
    "{x}",
    "[y]",
    "x'y",
    "x;y",
    "'; DROP TABLE chunks; --",
    "x\x00y",
    "\x00",
    "x\ny",
    "   ",
    "",
    "данные",
    "x " * 100,
]


@pytest.fixture
def index_path(tmp_path: Path) -> Path:
    return tmp_path / "index.sqlite3"


async def _load(index_path: Path) -> None:
    doc = make_doc()
    chunks = [make_chunk(doc, start, text) for start, text in CORPUS.items()]
    async with opened(index_path) as repo:
        await repo.replace_document(doc, chunks, [axis(i % 4) for i in range(len(chunks))], [])


async def _search(index_path: Path, user_text: str) -> set[int]:
    async with opened(index_path) as repo:
        hits = await repo.lexical_search(build_lexical_query(user_text).match, 50)
    return {hit.chunk.start_line for hit in hits}


@pytest.mark.parametrize("user_text", HOSTILE)
def test_hostile_inputs_never_raise(index_path: Path, user_text: str) -> None:  # [NEG]
    asyncio.run(_load(index_path))
    asyncio.run(_search(index_path, user_text))  # must not raise


@pytest.mark.parametrize("user_text", HOSTILE)
def test_hostile_inputs_never_change_meaning(  # [NEG] mutation row 1
    index_path: Path, user_text: str
) -> None:
    # The rows found must be exactly the rows that contain a word of the text as a literal
    # phrase, as an independent reference computes them: operators are words, nothing more.
    asyncio.run(_load(index_path))
    found = asyncio.run(_search(index_path, user_text))
    assert found == oracle_fts.matching_rows(user_text, CORPUS.items())


def test_the_words_that_are_fts5_operators_are_matched_as_words(index_path: Path) -> None:  # [NEG]
    asyncio.run(_load(index_path))
    # NEAR/AND/OR/NOT appear as words in rows 1 and 9. As operators they would match differently
    # (or fail to parse); as words, each finds exactly the rows that contain it.
    assert asyncio.run(_search(index_path, "NEAR")) == {1, 9}
    assert asyncio.run(_search(index_path, "NOT")) == {1, 9}
    # Three words, ORed by us: "x" (rows 4, 8), "OR" (rows 1, 5, 9) and "1" (rows 5, 8). If the
    # user's OR were an operator it would mean x-or-1 and skip the rows that only contain "or".
    assert asyncio.run(_search(index_path, "x OR 1")) == {1, 4, 5, 8, 9}
    assert asyncio.run(_search(index_path, "heading_path:ac-2")) == oracle_fts.matching_rows(
        "heading_path:ac-2", CORPUS.items()
    )


def test_a_column_filter_is_not_a_column_filter(index_path: Path) -> None:  # [NEG]
    asyncio.run(_load(index_path))
    # As a column filter `heading_path:holds` would look only in that column and find nothing.
    # Quoted, it is the words "heading", "path" and "holds" in sequence, found in the text.
    assert asyncio.run(_search(index_path, "heading_path:holds")) == {2}


# ----- the shape of the output -----------------------------------------------------------------


def test_every_term_is_one_quoted_string_and_the_only_syntax_is_or() -> None:
    query = build_lexical_query('multi-factor "authentication" NEAR(x) 3.1.1')
    pieces = query.match.split(" OR ")
    assert all(p.startswith('"') and p.endswith('"') and len(p) >= 2 for p in pieces)
    # Remove every quoted string; nothing but the ORs we wrote may remain.
    leftover = query.match
    for piece in pieces:
        leftover = leftover.replace(piece, "", 1)
    assert leftover.replace(" OR ", "") == ""


@pytest.mark.parametrize("user_text", HOSTILE)
def test_hostile_text_never_adds_syntax_outside_quotes(user_text: str) -> None:  # [NEG]
    match = build_lexical_query(user_text).match
    outside = []
    inside = False
    i = 0
    while i < len(match):
        ch = match[i]
        if ch == '"':
            if inside and match[i + 1 : i + 2] == '"':
                i += 2  # a doubled quote inside a string is a literal quote
                continue
            inside = not inside
        elif not inside:
            outside.append(ch)
        i += 1
    assert not inside, "unbalanced quote in the generated query"
    assert "".join(outside).replace(" OR ", "") == ""


def test_quote_fts5_doubles_inner_quotes() -> None:
    assert quote_fts5('a"b') == '"a""b"'
    assert quote_fts5("plain") == '"plain"'
    assert quote_fts5('"') == '""""'


def test_an_empty_or_symbol_only_query_has_no_match_string() -> None:
    for text in ["", "   ", "***", '" " ( )', "\x00\x01", "the of is"]:
        query = build_lexical_query(text)
        assert query.match == "", text
        assert query.identifier_forms == ()
        assert not query.identifiers_only


def test_input_and_term_counts_are_bounded() -> None:
    many = " ".join(f"word{i}" for i in range(500))
    assert len(build_lexical_query(many).match.split(" OR ")) == MAX_TERMS
    huge = build_lexical_query("x" * 100_000)
    assert len(huge.match) < 2100  # cut at MAX_QUERY_CHARS, then quoted


def test_terms_are_deduplicated_ignoring_case() -> None:
    assert build_lexical_query("Policy policy POLICY").match == '"Policy"'


def test_stop_words_are_dropped_and_not_hostile_words() -> None:
    assert build_lexical_query("what is the policy").match == '"policy"'
    # and/or/not/near are NOT stop words: they are ordinary words once quoted.
    assert build_lexical_query("and or not near").match == '"and" OR "or" OR "not" OR "near"'


def test_identifiers_are_expanded_and_ordinary_words_kept() -> None:
    query = build_lexical_query("Do we enforce multi-factor for 3.1.1?")
    assert query.match == '"enforce" OR "multi-factor" OR "3.1.1" OR "03.01.01"'
    assert query.identifier_forms == (("3.1.1", "03.01.01"),)
    assert not query.identifiers_only


def test_identifier_only_queries_are_recognised() -> None:
    assert build_lexical_query("AC-2").identifiers_only
    assert build_lexical_query("what is CC6.1?").identifiers_only
    assert build_lexical_query("(AC-2(1)),").identifier_forms == (("AC-2(1)", "AC-02(1)"),)
    assert not build_lexical_query("AC-2 account").identifiers_only


def test_punctuation_around_an_identifier_is_not_part_of_it() -> None:
    for text in ["CC6.1.", "(CC6.1)", '"CC6.1"', "CC6.1,", "**CC6.1**"]:
        assert build_lexical_query(text).identifier_forms == (("CC6.1",),), text


# ----- every identifier form is found, and only itself (against real FTS5) ---------------------


def _fts(rows: list[str]) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    # The index's own table definition, so the tokenizer under test is the one in the repository.
    ddl = _FTS_DDL.replace("fts_chunks", "t").replace("text, heading_path", "text")
    con.execute(ddl)
    con.executemany("INSERT INTO t(text) VALUES (?)", [(r,) for r in rows])
    con.execute("CREATE VIRTUAL TABLE v USING fts5vocab(t, 'row')")
    return con


def test_oracle_agrees_with_sqlite() -> None:
    forms = sorted({form for rows in POSITIVE.values() for _token, fs in rows for form in fs})
    forms += ["CC6.1.", "**CC6.1**", "multi-factor", "multi factor", "x_y", "heading_path"]
    for form in forms:
        con = _fts([form])
        terms = sorted(str(r[0]) for r in con.execute("SELECT term FROM v"))
        assert terms == sorted(set(oracle_fts.tokenize(form))), form
        # and the generated phrase matches the stored text, as one phrase of those tokens
        hit = con.execute("SELECT count(*) FROM t WHERE t MATCH ?", (quote_fts5(form),))
        assert hit.fetchone()[0] == 1, form
        con.close()


def offends(source: str) -> bool:
    """True if the source imports from grc_sidecar (in any spelling) or imports relatively."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import) and any(
            a.name.startswith("grc_sidecar") for a in node.names
        ):
            return True
        if isinstance(node, ast.ImportFrom) and (
            node.level != 0
            or (node.module or "").startswith("grc_sidecar")
            or any(a.name.startswith("grc_sidecar") for a in node.names)
        ):
            return True
    return False


def test_oracle_is_independent() -> None:  # [NEG] mutation row 28
    # The oracle must not share code with the builder, or it would share its bugs.
    source = (Path(__file__).parent / "oracle_fts.py").read_text(encoding="utf-8")
    assert not offends(source)


def test_the_independence_scan_catches_every_import_shape() -> None:
    # The scan above is only worth anything if it can fail: run its rule on bad samples.
    for bad in [
        "from grc_sidecar.retrieval.query import build_lexical_query",
        "import grc_sidecar.retrieval.identifiers as ids",
        "from grc_sidecar.retrieval import query",
        "from grc_sidecar import retrieval",
        "from . import query",
        "from .. import retrieval",
    ]:
        assert offends(bad), bad
    assert not offends("import re\nfrom collections.abc import Iterable")
