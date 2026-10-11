"""The grounding checks on a quote (ADR-013 decisions 3, 6, 7, 8): pure functions, no model.

Each negative test pins a fact about what is NOT accepted. The big rule is that whitespace is the
only leniency: no case folding, no Markdown stripping, no punctuation folding.
"""

import itertools

import pytest

from grc_sidecar.questionnaire.grounding import (
    MIN_QUOTE_CHARS,
    MIN_QUOTE_TOKENS,
    contains_identifier,
    locate_quote,
    long_enough,
    normalize,
    normalize_with_map,
)
from grc_sidecar.retrieval.models import RetrievedChunk
from grc_sidecar.vault.chunker import chunk_document

from .answerkit import chunk
from .evalkit import load_eval_documents, load_eval_set
from .vaultkit import EVAL_SET, REPO_ROOT

# Characters that are easy to confuse with ordinary ones are named here, never typed in.
NBSP = chr(0xA0)  # no-break space: whitespace to str.split()
EM_SPACE = chr(0x2003)  # whitespace too
CURLY_OPEN = chr(0x201C)
CURLY_CLOSE = chr(0x201D)
E_ACUTE = chr(0xE9)  # one code point
COMBINING_ACUTE = chr(0x301)  # "e" plus this is the same letter written as two code points

# ----- normalization: whitespace only ------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ""),
        ("   ", ""),
        ("a", "a"),
        ("  a  ", "a"),
        ("a b", "a b"),
        ("a   b", "a b"),
        ("a\nb", "a b"),
        ("a\r\nb", "a b"),
        ("a\t\tb", "a b"),
        ("\n\n a \n b \n\n", "a b"),
        (f"a{NBSP}b", "a b"),
        (f"a{EM_SPACE}b", "a b"),
        ("A  b", "A b"),  # case is kept
        ("**a**  _b_", "**a** _b_"),  # Markdown is kept
        (f"{CURLY_OPEN}quoted{CURLY_CLOSE}", f"{CURLY_OPEN}quoted{CURLY_CLOSE}"),  # so are quotes
    ],
)
def test_normalize_collapses_whitespace_and_nothing_else(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


def test_the_offset_map_agrees_with_normalize_on_every_short_string() -> None:
    """Every string of up to five characters over an alphabet of letters, whitespace of several
    kinds and punctuation (about 37,000 strings): the map's text equals `normalize`, and each
    character points at the right place in the raw text."""
    alphabet = ["a", "B", "7", ".", "*", " ", "\n", "\t", NBSP, EM_SPACE]
    count = 0
    for length in range(6):
        for combo in itertools.product(alphabet, repeat=length):
            raw = "".join(combo)
            normalized, origin = normalize_with_map(raw)
            assert normalized == normalize(raw) == " ".join(raw.split())
            assert len(origin) == len(normalized)
            for char, offset in zip(normalized, origin, strict=True):
                if char != " ":
                    assert raw[offset] == char  # a real character maps to itself
                else:
                    assert raw[offset].isspace()  # a collapsed run maps to its first character
            assert origin == sorted(origin)
            count += 1
    assert count == sum(len(alphabet) ** n for n in range(6))


# ----- verbatim: found, and where ----------------------------------------------------------------


def located(text: str, quote: str, *, start_line: int = 12) -> tuple[int, int] | None:
    found = locate_quote(chunk(text, start_line=start_line), quote)
    return None if found is None else (found.start_line, found.end_line)


def test_an_exact_quote_is_found_on_the_right_line() -> None:
    text = "## Screens\n\nLaptops lock after 10 minutes of inactivity.\n"
    assert located(text, "lock after 10 minutes") == (14, 14)  # line 12 + 2 newlines before it


@pytest.mark.parametrize(
    ("quote", "lines"),
    [
        ("line one", (12, 12)),
        ("line two", (13, 13)),
        ("line three", (14, 14)),
        ("one\nline two", (12, 13)),  # a quote that spans two lines
        ("one line two line three", (12, 14)),  # the same, with the newlines as spaces
        ("two\n\nline", (13, 14)),  # blank lines in the QUOTE collapse like any whitespace
    ],
)
def test_the_lines_of_a_multi_line_quote_are_exact(quote: str, lines: tuple[int, int]) -> None:
    assert located("line one\nline two\nline three", quote) == lines


def test_the_line_offset_follows_the_chunks_first_line() -> None:  # [NEG] off-by-one guard
    text = "alpha\nbeta\ngamma"
    assert located(text, "gamma", start_line=1) == (3, 3)
    assert located(text, "gamma", start_line=100) == (102, 102)
    assert located(text, "alpha", start_line=100) == (100, 100)


def test_whitespace_differences_do_not_matter() -> None:
    text = "Staff   must\n\tuse  MFA\non all systems."
    for quote in (
        "Staff must use MFA on all systems.",
        "  Staff must use MFA on all systems.  ",
        "Staff\nmust\nuse\nMFA\non\nall\nsystems.",
        f"Staff   must use{NBSP}MFA on all systems.",
    ):
        assert located(text, quote) is not None, quote


def test_a_no_break_space_in_the_document_matches_a_plain_space_in_the_quote() -> None:
    assert located(f"Staff must{NBSP}use MFA now.", "Staff must use MFA now.") is not None


@pytest.mark.parametrize(
    "quote",
    [
        "staff must use mfa on all systems.",  # case
        "STAFF MUST USE MFA ON ALL SYSTEMS.",
        "Staff must use MFA on every system.",  # a different word
        "Staff must use MFA, on all systems.",  # an added comma
        "Staff must use MFA on all systems. Always.",  # extra text after the end
        "Staff must use MFA on all system.",  # a different ending
        "Staff must not use MFA on all systems.",  # an inserted word
        "",
        "   ",
    ],
)
def test_anything_beyond_whitespace_is_not_verbatim(quote: str) -> None:  # [NEG]
    assert located("Staff must use MFA on all systems.", quote) is None


def test_a_fragment_of_the_text_is_verbatim_even_without_its_final_punctuation() -> None:
    """The rule is a substring match: a quote that leaves off the final period is still a part of
    the text. (How much a quote must contain is the length floor's job, not this check's.)"""
    assert located("Staff must use MFA on all systems.", "Staff must use MFA on all systems")


def test_markdown_markup_is_not_stripped() -> None:  # [NEG] ADR-013, decision 3
    text = "Administrators **must** use a *hardware* security key.\n"
    assert located(text, "Administrators **must** use a *hardware* security key.") is not None
    assert located(text, "Administrators must use a hardware security key.") is None


def test_curly_quotes_and_straight_quotes_are_different_characters() -> None:  # [NEG]
    curly = f"It said {CURLY_OPEN}no{CURLY_CLOSE}."
    assert located('It said "no".', curly) is None
    assert located(curly, 'It said "no".') is None


def test_unicode_forms_are_not_folded() -> None:  # [NEG]
    composed = f"caf{E_ACUTE} policy applies"
    decomposed = f"cafe{COMBINING_ACUTE} policy applies"
    assert located(composed, composed) is not None
    assert located(composed, decomposed) is None


def test_a_quote_may_start_or_end_inside_a_word() -> None:
    """The rule is a substring match, not a word match: the model is held to the characters."""
    assert located("encryption at rest", "cryption at re") is not None


def test_the_first_occurrence_decides_the_lines() -> None:
    assert located("same words here\nother\nsame words here", "same words here") == (12, 12)


def test_a_table_row_is_found_in_the_raw_text() -> None:
    text = "| Data | Retention |\n|---|---|\n| Security logs | 90 days |\n"
    assert located(text, "| Security logs | 90 days |") == (14, 14)
    assert located(text, "Security logs | 90 days") == (14, 14)
    assert located(text, "Security logs 90 days") is None  # [NEG] the pipe is a character


def test_a_heading_only_quote_is_flagged() -> None:
    text = "## Multi-factor authentication required here\n\nBody sentence goes on and on today.\n"
    heading = locate_quote(chunk(text), "Multi-factor authentication required here")
    body = locate_quote(chunk(text), "Body sentence goes on and on today.")
    spans = locate_quote(chunk(text), "required here Body sentence goes on")
    assert heading is not None and heading.heading_only is True
    assert body is not None and body.heading_only is False
    assert spans is not None and spans.heading_only is False  # heading plus body: not heading-only


def test_only_atx_heading_lines_count_as_headings() -> None:
    for line, expected in [
        ("# Title words go here now", True),
        ("###### deep heading here ok", True),
        ("   ## indented up to three spaces", True),
        ("    ## four spaces is code, not a heading", False),
        ("#hashtag is not a heading", False),
        ("####### seven hashes is not one", False),
        ("not # a heading", False),
    ]:
        found = locate_quote(chunk(f"{line}\n"), line.strip())
        assert found is not None and found.heading_only is expected, line


# ----- the minimum length ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("quote", "ok"),
    [
        ("MFA", False),
        ("HR-POL-004", False),
        ("Data Retention Policy", False),  # 21 characters but 3 tokens
        ("All staff must use MFA.", True),  # 23 characters, 5 tokens
        ("a" * 20, False),  # 20 characters, 1 token
        ("aaaaaaa bbbbbbb cccccc", False),  # 22 characters, 3 tokens
        ("aaaa bbbb cccc ddd", False),  # 18 characters, 4 tokens
        ("aaaa bbbb cccc dddd", False),  # 19 characters, 4 tokens
        ("aaaa bbbb cccc ddddd", True),  # exactly 20 characters, 4 tokens
        ("a b c d e f g h i j k", True),  # 21 characters, 11 tokens
        ("a   b   c   d   e   f", False),  # 11 characters once collapsed, whatever the spacing
        ("", False),
    ],
)
def test_the_minimum_quote_length(quote: str, ok: bool) -> None:  # [NEG]
    assert long_enough(quote) is ok


def test_the_thresholds_are_the_documented_ones() -> None:
    assert (MIN_QUOTE_CHARS, MIN_QUOTE_TOKENS) == (20, 4)


# ----- identifiers: whole, not substrings ------------------------------------------------------


@pytest.mark.parametrize(
    ("form", "text"),
    [
        ("AC-2", "Accounts are managed under AC-2 and reviewed."),
        ("AC-2", "See AC-2(1) for automated actions."),  # a control's enhancement counts
        ("AC-2", "See AC-2(1)(a) too."),
        ("AC-2", "ends with AC-2."),  # a sentence-final dot
        ("AC-2", "(AC-2)"),
        ("AC-2", "SOC2:AC-2 mapped"),
        ("CC6.1", "Mapped to CC6.1."),
        ("CC6.1", "CC6.1, CC6.2"),
        ("A.5.15", "Control A.5.15 applies."),
        ("3.1.1", "Requirement 3.1.1 applies."),
        ("ac-2", "AC-2 is a control"),  # case-insensitive, both ways
        ("AC-2", "ac-2 is a control"),
        ("HR-POL-005", "see hr-pol-005 for details"),
        ("5.15", "ISO 5.15 says so"),
    ],
)
def test_a_whole_identifier_is_found(form: str, text: str) -> None:
    assert contains_identifier(text, (form,)) is True


@pytest.mark.parametrize(
    ("form", "text"),
    [
        ("AC-2", "The control AC-20 is something else."),  # the traps ADR-010 built
        ("CC6.1", "CC6.10 is something else."),
        ("A.5.15", "A.5.152 is something else."),
        ("3.1.1", "Requirement 3.1.10 is something else."),
        ("3.1.1", "Requirement 13.1.1 is something else."),
        ("AC-2", "XAC-2 is not it"),  # a letter before
        ("AC-2", "1AC-2"),  # a digit before
        ("AC-2", "x.AC-2"),  # a dot before
        ("AC-2", "x-AC-2"),  # a hyphen before
        ("AC-2", "AC-2x is not it"),  # a letter after
        ("AC-2", "AC-2-1 is a longer identifier"),  # a hyphen after
        ("AC-2", "AC-2_extra"),  # an underscore after
        ("CC6.1", "CC6.1.5 is longer"),  # a dot that continues into more text
        ("3.1.1", "version 3.1.1.2 of the spec"),
        ("5.15", "A.5.15 is the full form"),  # a dot before: the "A.5.15" form is for this text
        ("AC-2", "no identifier here at all"),
        ("AC-2", ""),
    ],
)
def test_a_longer_or_glued_identifier_is_not_a_match(form: str, text: str) -> None:  # [NEG]
    assert contains_identifier(text, (form,)) is False


def test_any_form_of_the_identifier_will_do() -> None:
    assert contains_identifier("Requirement 03.01.01 applies.", ("3.1.1", "03.01.01")) is True
    assert contains_identifier("Requirement 3.1.1 applies.", ("3.1.1", "03.01.01")) is True
    assert contains_identifier("Requirement 03.01.02 applies.", ("3.1.1", "03.01.01")) is False


def test_regex_metacharacters_in_a_form_are_literal() -> None:  # [NEG]
    assert contains_identifier("AC-2(1) here", ("AC-2(1)",)) is True
    assert contains_identifier("ACX2 here", ("AC.2",)) is False  # a dot is a dot, not any character
    assert contains_identifier("AC-2", ("AC-2|",)) is False
    assert contains_identifier("anything", (".*",)) is False


# ----- the labels of M5b point where this code says ----------------------------------------------


def _retrieved_chunks() -> list[tuple[str, RetrievedChunk]]:
    """Every chunk of the evaluation vaults, as the status code would see it."""
    evalset = load_eval_set(EVAL_SET)
    docs = load_eval_documents(REPO_ROOT, evalset.vault_roots)
    found = []
    for doc in docs.values():
        for part in chunk_document(doc):
            piece = chunk(
                part.text,
                start_line=part.start_line,
                doc_id=doc.frontmatter.doc_id,
                source_path=doc.source_path,
            )
            found.append((doc.frontmatter.doc_id, piece))
    return found


def test_every_label_phrase_is_located_on_its_labeled_lines() -> None:  # [NEG]
    """The M5b eval set says where each answer is; the quote mapping must say the same."""
    evalset = load_eval_set(EVAL_SET)
    chunks = _retrieved_chunks()
    checked = 0
    for item in evalset.items:
        for ref in item.acceptable_evidence:
            hits = [
                found
                for doc_id, piece in chunks
                if doc_id == ref.doc_id and (found := locate_quote(piece, ref.phrase)) is not None
            ]
            assert hits, f"{item.id}: phrase not found in any chunk of {ref.doc_id}"
            for hit in hits:
                assert ref.start_line <= hit.start_line <= hit.end_line <= ref.end_line, item.id
                assert hit.heading_only is False, item.id
            checked += 1
    assert checked == sum(len(i.acceptable_evidence) for i in evalset.items) > 20
