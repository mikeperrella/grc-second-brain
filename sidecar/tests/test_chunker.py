import hashlib

import pytest

from grc_sidecar.vault.chunker import (
    CHUNKER_VERSION,
    EMBED_DOCUMENT_PREFIX,
    FLAG_EMBEDDING_TRUNCATED,
    FLAG_SPLIT_AT_LINE,
    HARD_MAX_EMBED_CHARS,
    MAX_CHUNK_CHARS,
    chunk_document,
    compute_chunk_id,
    embedding_input,
)
from grc_sidecar.vault.frontmatter import parse_document
from grc_sidecar.vault.models import Chunk, ParsedDocument
from grc_sidecar.vault.scan import scan_vault

from .vaultkit import ADVERSARIAL_VAULT, DEFAULT_BODY_START, SAMPLE_VAULT, doc_text, make_doc

B = DEFAULT_BODY_START  # line number of the first body line
BOM = chr(0xFEFF)
LS, PS, NEL = chr(0x2028), chr(0x2029), chr(0x85)  # Unicode line and paragraph separators, NEL


def _spans(chunks: tuple[Chunk, ...]) -> list[tuple[int, int]]:
    return [(c.start_line, c.end_line) for c in chunks]


def test_headings_split_the_body_with_exact_line_numbers() -> None:
    doc = make_doc("# One\n\nalpha\n\n## Two\n\nbeta\n\n# Three\n\ngamma")
    chunks = chunk_document(doc)
    assert _spans(chunks) == [(B, B + 2), (B + 4, B + 6), (B + 8, B + 10)]
    assert [c.heading_path for c in chunks] == ["One", "One > Two", "Three"]
    assert chunks[0].text == "# One\n\nalpha"


def test_line_numbers_count_the_frontmatter() -> None:
    doc = make_doc("only line")
    (chunk,) = chunk_document(doc)
    assert (chunk.start_line, chunk.end_line) == (B, B)
    assert doc.lines[chunk.start_line - 1] == "only line"
    assert B == 7  # 4 frontmatter lines + 2 fences + 1


def test_chunk_text_is_exactly_the_files_lines() -> None:
    doc = make_doc("# A\n\n  indented  \n\ttabbed\n\n## B\ntext")
    for chunk in chunk_document(doc):
        assert chunk.text == "\n".join(doc.lines[chunk.start_line - 1 : chunk.end_line])


def test_text_before_the_first_heading_is_its_own_chunk() -> None:
    chunks = chunk_document(make_doc("> banner\n\n# Title\n\nbody"))
    assert [c.heading_path for c in chunks] == ["", "Title"]
    assert chunks[0].text == "> banner"


def test_a_heading_with_no_body_joins_the_next_section() -> None:
    chunks = chunk_document(make_doc("# A\n## B\n\n### C\ntext"))
    assert len(chunks) == 1
    assert chunks[0].start_line == B  # starts at "# A"
    assert chunks[0].heading_path == "A > B > C"
    assert chunks[0].text.startswith("# A\n## B")


def test_a_bare_heading_at_the_end_is_kept() -> None:
    chunks = chunk_document(make_doc("# A\n\ntext\n\n# Last"))
    assert [c.text for c in chunks] == ["# A\n\ntext", "# Last"]


def test_a_heading_inside_a_code_fence_is_not_a_heading() -> None:
    body = "# Real\n\n```\n# not a heading\n\n## nor this\n```\n\nafter"
    chunks = chunk_document(make_doc(body))
    assert len(chunks) == 1
    assert chunks[0].heading_path == "Real"


@pytest.mark.parametrize(
    "fence_open, fence_close",
    [("```python", "```"), ("~~~", "~~~"), ("````", "````"), ("```", "`````")],
)
def test_fenced_blocks_of_every_style_hide_headings(fence_open: str, fence_close: str) -> None:
    body = f"# Real\n\n{fence_open}\n# hidden\n{fence_close}\n\n# Next\n\ntext"
    assert [c.heading_path for c in chunk_document(make_doc(body))] == ["Real", "Next"]


def test_a_longer_fence_can_contain_a_shorter_one() -> None:
    body = "# Real\n\n````\n```\n# hidden\n```\n````\n\n# Next\n\nx"
    assert [c.heading_path for c in chunk_document(make_doc(body))] == ["Real", "Next"]


def test_an_unclosed_fence_runs_to_the_end_of_the_file() -> None:
    body = "# Real\n\n```\n# hidden\n\n# also hidden\n"
    chunks = chunk_document(make_doc(body))
    assert len(chunks) == 1
    assert chunks[0].heading_path == "Real"


@pytest.mark.parametrize("line", ["#hashtag", "####### seven", "text # not heading", "\\# escaped"])
def test_lines_that_are_not_atx_headings(line: str) -> None:
    chunks = chunk_document(make_doc(f"# Real\n\n{line}\n\nmore"))
    assert len(chunks) == 1


def test_setext_underlines_are_not_headings_a_documented_limit() -> None:
    chunks = chunk_document(make_doc("Title\n=====\n\ntext\n\nOther\n-----\n\nmore"))
    assert len(chunks) == 1


def test_closing_hashes_are_removed_from_the_heading_text() -> None:
    chunks = chunk_document(make_doc("## Title ##\n\ntext"))
    assert chunks[0].heading_path == "Title"


def test_an_empty_body_gives_no_chunks() -> None:
    assert chunk_document(make_doc("")) == ()
    assert chunk_document(make_doc("\n\n   \n")) == ()


def test_blank_lines_around_a_chunk_are_trimmed() -> None:
    chunks = chunk_document(make_doc("\n\n# A\n\ntext\n\n\n"))
    assert _spans(chunks) == [(B + 2, B + 4)]


def test_a_long_section_splits_at_blank_lines_and_keeps_its_heading() -> None:
    paragraphs = [f"paragraph {i} " + "word " * 80 for i in range(12)]
    doc = make_doc("# Long\n\n" + "\n\n".join(paragraphs))
    chunks = chunk_document(doc)
    assert len(chunks) > 1
    assert all(c.heading_path == "Long" for c in chunks)
    assert all(len(c.text) <= MAX_CHUNK_CHARS for c in chunks)
    # Whole paragraphs only: no chunk ends in the middle of one.
    assert all(c.text.rstrip().endswith("word") for c in chunks)


def test_a_fenced_block_is_never_split_while_it_fits() -> None:
    # About 2.1k characters: more than MAX_CHUNK_CHARS, but it fits under HARD_MAX_EMBED_CHARS.
    code = "\n".join(f"line {i:04d}" + "x" * 60 for i in range(30))
    doc = make_doc(f"# Code\n\n```\n{code}\n```")
    chunks = chunk_document(doc)
    assert len(chunks) == 1
    assert FLAG_SPLIT_AT_LINE not in chunks[0].flags
    assert len(chunks[0].text) > MAX_CHUNK_CHARS


def test_a_fenced_block_too_big_for_the_embedding_model_splits_at_line_boundaries() -> None:
    code = "\n".join(f"line {i:05d}" + "y" * 50 for i in range(200))  # about 12k characters
    doc = make_doc(f"# Code\n\n```\n{code}\n```")
    chunks = chunk_document(doc)
    assert len(chunks) > 1
    assert all(FLAG_SPLIT_AT_LINE in c.flags for c in chunks)
    # Line boundaries only: consecutive chunks are adjacent, nothing lost or duplicated.
    covered = [n for c in chunks for n in range(c.start_line, c.end_line + 1)]
    assert covered == sorted(set(covered))
    for chunk in chunks:
        assert chunk.text == "\n".join(doc.lines[chunk.start_line - 1 : chunk.end_line])


def test_one_enormous_line_keeps_its_full_text_but_a_cut_embedding_input() -> None:
    line = "z" * (HARD_MAX_EMBED_CHARS * 2)
    (chunk,) = chunk_document(make_doc(f"# Big\n\n{line}"))
    assert chunk.text.endswith(line)
    assert FLAG_EMBEDDING_TRUNCATED in chunk.flags
    sent, cut = embedding_input(chunk.heading_path, chunk.text)
    assert cut and len(sent) == HARD_MAX_EMBED_CHARS


MEASURED_DENSE_LIMIT = 2820  # characters of identifier-dense text Ollama accepted (M3c, live)


def test_the_embedding_cap_keeps_headroom_under_the_measured_limit() -> None:
    # The live check (`pytest -m ollama`) measured the longest dense string nomic-embed-text
    # accepts through Ollama with truncation off. The cap must stay well below it, and must still
    # hold a full-size chunk with its prefix and an ordinary heading.
    assert HARD_MAX_EMBED_CHARS <= MEASURED_DENSE_LIMIT * 0.85
    assert MAX_CHUNK_CHARS + len(EMBED_DOCUMENT_PREFIX) + 200 <= HARD_MAX_EMBED_CHARS
    # Lowering the cap changed where long sections split, so old indexes must be rebuilt.
    assert CHUNKER_VERSION == "2"


def test_embedding_input_never_exceeds_the_hard_cap() -> None:
    long_heading = "# " + "H" * 3000
    dense = " ".join(f"AC-{n}({m})" for n in range(1, 60) for m in range(1, 6))
    docs = [
        make_doc(f"{long_heading}\n\n{dense}"),
        make_doc("# T\n\n" + "\n\n".join(["q" * 1900] * 8)),
        make_doc("# T\n\n```\n" + "\n".join("k" * 90 for _ in range(300)) + "\n```"),
    ]
    docs += [d for d in _all_documents()]
    for doc in docs:
        for chunk in chunk_document(doc):
            sent, _ = embedding_input(chunk.heading_path, chunk.text)
            assert len(sent) <= HARD_MAX_EMBED_CHARS
            assert sent.startswith(EMBED_DOCUMENT_PREFIX)


def _all_documents() -> list[ParsedDocument]:
    return list(scan_vault(SAMPLE_VAULT).documents) + list(scan_vault(ADVERSARIAL_VAULT).documents)


def test_chunks_cover_every_non_blank_body_line_exactly_once() -> None:
    for doc in _all_documents():
        chunks = chunk_document(doc)
        owner: dict[int, str] = {}
        for chunk in chunks:
            for number in range(chunk.start_line, chunk.end_line + 1):
                assert number not in owner, f"line {number} is in two chunks"
                owner[number] = chunk.chunk_id
        for number in range(doc.body_start_line, len(doc.lines) + 1):
            if doc.lines[number - 1].strip():
                assert number in owner, f"line {number} is in no chunk"
        assert all(n >= doc.body_start_line for n in owner)  # frontmatter is never chunked


def test_chunking_is_deterministic() -> None:
    for doc in _all_documents():
        assert chunk_document(doc) == chunk_document(doc)
        assert [c.chunk_id for c in chunk_document(doc)] == [
            c.chunk_id for c in chunk_document(doc)
        ]


def test_chunk_id_matches_independent_derivation() -> None:
    doc = make_doc("# A\n\ntext", path="folder/note.md")
    (chunk,) = chunk_document(doc)
    # Rebuilt by hand from CLAUDE.md 8.1: (source_path, doc_id, sha256(file), start, end).
    payload = '["{}","{}","{}",{},{}]'.format(
        "folder/note.md",
        "T-001",
        doc.file_sha256,
        chunk.start_line,
        chunk.end_line,
    )
    assert chunk.chunk_id == hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert chunk.chunk_id == compute_chunk_id("folder/note.md", "T-001", doc.file_sha256, B, B + 2)


def test_every_input_of_the_chunk_id_matters() -> None:
    base = ("a.md", "ID", "f" * 64, 3, 9)
    ids = {compute_chunk_id(*base)}
    for index, other in enumerate(["b.md", "ID2", "e" * 64, 4, 10]):
        changed = list(base)
        changed[index] = other
        ids.add(compute_chunk_id(*changed))
    assert len(ids) == 6


def test_text_sha256_is_the_hash_of_the_chunk_text() -> None:
    (chunk,) = chunk_document(make_doc("# A\n\ntext"))
    assert chunk.text_sha256 == hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()


def test_editing_one_chunk_changes_every_id_but_only_that_chunks_text_hash() -> None:
    before = chunk_document(make_doc("# A\n\nalpha\n\n# B\n\nbeta"))
    after = chunk_document(make_doc("# A\n\nalpha\n\n# B\n\nbeta CHANGED"))
    assert {c.chunk_id for c in before}.isdisjoint({c.chunk_id for c in after})
    assert before[0].text_sha256 == after[0].text_sha256  # the untouched passage
    assert before[1].text_sha256 != after[1].text_sha256


def test_crlf_lf_and_bom_produce_same_ids() -> None:
    lf = doc_text("# A\n\nalpha\n\n## B\n\nbeta\n")
    variants = [
        lf,
        lf.replace("\n", "\r\n"),
        BOM + lf.replace("\n", "\r\n"),
        lf.replace("\n", "\r"),
    ]
    results = [chunk_document(parse_document(text, "a.md")) for text in variants]
    assert all(r == results[0] for r in results)
    assert [c.chunk_id for c in results[0]] == [c.chunk_id for c in results[1]]


def test_a_unicode_line_separator_does_not_shift_line_numbers() -> None:
    body = f"# A\n\nfirst{LS}still first{PS}still{NEL}first\n\n# B\n\nlast"
    chunks = chunk_document(make_doc(body))
    assert _spans(chunks) == [(B, B + 2), (B + 4, B + 6)]
    assert LS in chunks[0].text


def test_the_source_path_is_part_of_the_id_but_the_same_file_elsewhere_differs() -> None:
    first = chunk_document(make_doc("# A\n\nx", path="one.md"))
    second = chunk_document(make_doc("# A\n\nx", path="sub/one.md"))
    assert first[0].chunk_id != second[0].chunk_id
    assert first[0].text_sha256 == second[0].text_sha256
