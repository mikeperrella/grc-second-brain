"""Turn untrusted user text into a safe FTS5 MATCH string (ADR-010, M4d).

FTS5 reads its query argument as a small language: `AND`, `OR`, `NOT`, `NEAR(...)`, `*`, `^`,
`column:` filters, parentheses and quotes all mean something, and a stray one is a syntax error
or, worse, a different question. A question from a questionnaire or a document is data, not
syntax, so this module never lets the user's characters reach FTS5 outside a quoted string:

* the text is cut into words on whitespace;
* every word, and every written form of an identifier (see `identifiers.py`), becomes ONE
  double-quoted FTS5 string with each inner `"` doubled, which FTS5 reads as a phrase of literal
  tokens (tokenized by the same tokenizer as the index, so the phrase matches exactly what was
  indexed);
* the only syntax in the result is the ` OR ` this module writes between those strings. No
  parentheses, `*`, `^`, `:` or `-` operators are ever generated.

The result is passed to SQLite as a bound parameter (`MATCH ?`), so SQL injection is a separate
non-issue. Words are OR-ed and ranked by BM25 afterwards: a natural-language question rarely
contains every word of the policy that answers it.

Bounds: the input is cut at `MAX_QUERY_CHARS` and at most `MAX_TERMS` terms are kept, so hostile
input cannot build a huge expression.
"""

import unicodedata
from dataclasses import dataclass
from typing import Final

from grc_sidecar.retrieval.identifiers import identify

MAX_QUERY_CHARS: Final = 2000
MAX_TERMS: Final = 32

# Words that carry no meaning for a keyword search. Deliberately NOT included: and, or, not, near
# (they are ordinary words once quoted, and the safety tests use them as hostile input).
STOP_WORDS: Final = frozenset(
    {"a", "an", "are", "as", "at", "be", "by", "do", "does", "for", "how", "in", "is", "it"}
    | {"of", "on", "that", "the", "to", "we", "what", "which", "with", "you", "your", "our"}
)

_OPENERS: Final = "([{"
_CLOSERS: Final = ")]}"
# `*` and `_` are Markdown emphasis around a word (`**CC6.1**`); they are only trimmed at the edges.
_LEADING_PUNCTUATION: Final = "\"'*_" + _OPENERS
_TRAILING_PUNCTUATION: Final = "\"'*_.,;:!?"


@dataclass(frozen=True)
class LexicalQuery:
    """What the builder produced. `match` is the FTS5 string; it is `""` for an empty query."""

    match: str
    # Every identifier found, as the written forms that are searched (for example "3.1.1" and
    # "03.01.01"), in query order.
    identifier_forms: tuple[tuple[str, ...], ...]
    # True when every kept word of the query is an identifier ("AC-2", "what is CC6.1?").
    identifiers_only: bool


def quote_fts5(text: str) -> str:
    """One FTS5 string literal: double-quoted, inner double quotes doubled."""
    return '"' + text.replace('"', '""') + '"'


def _clean(piece: str) -> str:
    """Drop punctuation that surrounds a word without being part of it: `(AC-2),` -> `AC-2`.

    A closing bracket is dropped only when it has no opener in the word, so `AC-2(1)` keeps its
    `(1)`.
    """
    word = piece.lstrip(_LEADING_PUNCTUATION).rstrip(_TRAILING_PUNCTUATION)
    while word.endswith(tuple(_CLOSERS)) and _has_unmatched_closer(word):
        word = word[:-1].rstrip(_TRAILING_PUNCTUATION)
    return word


def _has_unmatched_closer(word: str) -> bool:
    return sum(word.count(ch) for ch in _CLOSERS) > sum(word.count(ch) for ch in _OPENERS)


def _printable(text: str) -> str:
    """Control and format characters (NUL, escapes, direction marks...) become spaces."""
    return "".join(" " if unicodedata.category(ch) in ("Cc", "Cf", "Cs") else ch for ch in text)


def build_lexical_query(text: str) -> LexicalQuery:
    """The FTS5 MATCH string for a user's text. Never raises for any input."""
    words = _printable(text[:MAX_QUERY_CHARS]).split()
    terms: list[str] = []
    seen: set[str] = set()
    identifiers: list[tuple[str, ...]] = []
    only_identifiers = True

    def add(phrase_text: str) -> None:
        key = phrase_text.casefold()
        if key not in seen:
            seen.add(key)
            terms.append(quote_fts5(phrase_text))

    for piece in words:
        word = _clean(piece)
        if not any(ch.isalnum() for ch in word):
            continue  # nothing the tokenizer would index: a bare "***" or "(" is not a term
        found = identify(word)
        if found is None:
            if word.casefold() in STOP_WORDS:
                continue
            only_identifiers = False
            if len(terms) < MAX_TERMS:
                add(word)
            continue
        identifiers.append(found.forms)
        for form in found.forms:
            if len(terms) < MAX_TERMS:
                add(form)

    return LexicalQuery(
        match=" OR ".join(terms),
        identifier_forms=tuple(identifiers),
        identifiers_only=bool(identifiers) and only_identifiers,
    )
