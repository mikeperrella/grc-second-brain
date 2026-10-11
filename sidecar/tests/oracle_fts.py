"""An independent reference for "which chunks does this user text match" (tests only).

It re-implements the index's tokenizer (`unicode61 tokenchars '-'`) from its definition: lower-case
the text, a token is a run of letters, digits and `-`, everything else separates tokens. It is
deliberately NOT built from `grc_sidecar.retrieval` (no import of `query` or `identifiers`): if the
query builder had a bug, an oracle made from the same code would share it and the test would
agree with the bug. `test_oracle_is_independent` enforces that with an AST scan, and
`test_oracle_agrees_with_sqlite` checks the tokenizer against real FTS5.

Meaning of a user text for this oracle: each piece (separated by whitespace or control
characters) is a phrase of the tokens inside it, and a row matches when it contains at least
one such phrase (adjacent, in order).
Pieces with no tokens are ignored. This is the plain-words case; stop words, identifier
expansion and the term cap are the builder's own, separately tested, behaviour, so the oracle's
inputs avoid them.
"""

from collections.abc import Iterable


def tokenize(text: str) -> list[str]:
    """The tokens FTS5 would index for `text`."""
    tokens: list[str] = []
    current: list[str] = []
    for ch in text.lower():
        if ch.isalnum() or ch == "-":
            current.append(ch)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens


def contains_phrase(row_tokens: list[str], phrase: list[str]) -> bool:
    size = len(phrase)
    return any(row_tokens[i : i + size] == phrase for i in range(len(row_tokens) - size + 1))


def matching_rows(user_text: str, rows: Iterable[tuple[int, str]]) -> set[int]:
    """The ids of the rows (id, text) that contain at least one piece of `user_text` as a phrase."""
    # Pieces are separated by whitespace and by ASCII control characters (NUL, escapes...), which
    # are never part of a word.
    spaced = "".join(" " if ord(ch) < 32 or ord(ch) == 127 else ch for ch in user_text)
    phrases = [tokens for piece in spaced.split() if (tokens := tokenize(piece))]
    matched: set[int] = set()
    for row_id, text in rows:
        row_tokens = tokenize(text)
        if any(contains_phrase(row_tokens, phrase) for phrase in phrases):
            matched.add(row_id)
    return matched
