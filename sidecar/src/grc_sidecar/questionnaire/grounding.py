"""The grounding checks on a quote (CLAUDE.md 8.4, ADR-013 decisions 3, 6, 7 and 8).

Pure functions: text in, a yes or a place out. The status decision that uses them is in `status.py`.

**Verbatim means verbatim.** The quote must appear in the RAW text of the chunk the model cited,
after one change to both sides: every run of whitespace becomes a single space and the ends are
trimmed. Nothing else is folded: not letter case, not Markdown markup, not punctuation, not Unicode
forms. A model that drops the `**` around a word, or turns a straight quote into a curly one, has
not quoted the document, and the check says so. Loosening this needs the owner's approval and an
amendment to ADR-013 (the live evaluation measures how often it bites before anyone decides).

**Exact lines.** The match is mapped back to offsets in the raw text, and a raw offset becomes a
line number by counting newlines from the chunk's first line, so a citation names exact lines.
"""

import re
from dataclasses import dataclass
from typing import Final

from grc_sidecar.retrieval.models import RetrievedChunk

MIN_QUOTE_CHARS: Final = 20  # of the normalized quote
MIN_QUOTE_TOKENS: Final = 4  # whitespace-separated words of the normalized quote

_ATX_HEADING: Final = re.compile(r"^\s{0,3}#{1,6}(\s|$)")


def normalize(text: str) -> str:
    """The one allowed change: whitespace runs become one space, the ends are trimmed."""
    return " ".join(text.split())


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """`normalize(text)` plus, for each character of the result, its offset in `text`.

    A collapsed run of whitespace maps to the offset of its first character. The result always
    equals `normalize(text)` (a test checks that on many strings).
    """
    out: list[str] = []
    origin: list[int] = []
    pending_space_at: int | None = None
    for offset, char in enumerate(text):
        if char.isspace():
            if out and pending_space_at is None:
                pending_space_at = offset
            continue
        if pending_space_at is not None:
            out.append(" ")
            origin.append(pending_space_at)
            pending_space_at = None
        out.append(char)
        origin.append(offset)
    return "".join(out), origin


@dataclass(frozen=True)
class QuoteLocation:
    """Where a verified quote sits: 1-based inclusive lines in the original file."""

    start_line: int
    end_line: int
    heading_only: bool  # the whole quote lies inside one Markdown heading line


def locate_quote(chunk: RetrievedChunk, quote: str) -> QuoteLocation | None:
    """Find `quote` verbatim in the chunk's raw text, or None. Whitespace is the only leniency."""
    needle = normalize(quote)
    if not needle:
        return None
    haystack, origin = normalize_with_map(chunk.text)
    at = haystack.find(needle)
    if at < 0:
        return None
    raw_first = origin[at]
    raw_last = origin[at + len(needle) - 1]  # the needle ends in a non-space, so a real character
    first = chunk.text.count("\n", 0, raw_first)
    last = chunk.text.count("\n", 0, raw_last)
    heading = first == last and _ATX_HEADING.match(chunk.text.split("\n")[first]) is not None
    return QuoteLocation(
        start_line=chunk.start_line + first,
        end_line=chunk.start_line + last,
        heading_only=heading,
    )


def long_enough(quote: str) -> bool:
    """A one-word, identifier-only or heading-only quote proves nothing (ADR-013, decision 6)."""
    normalized = normalize(quote)
    return len(normalized) >= MIN_QUOTE_CHARS and len(normalized.split(" ")) >= MIN_QUOTE_TOKENS


def contains_identifier(text: str, forms: tuple[str, ...]) -> bool:
    """True if `text` contains a WHOLE written form of an identifier (ADR-013, decision 7).

    A plain substring check would accept `AC-2` inside `AC-20` and `3.1.1` inside `13.1.1`. So a
    form must not be preceded by a letter, digit, underscore, dot or hyphen, and must not be
    followed by a letter, digit, underscore or hyphen, nor by a dot that continues into more text.
    A control's enhancements count (`AC-2(1)` contains `AC-2`), and a sentence-final dot is fine.
    Matching ignores case, as the index does.
    """
    for form in forms:
        pattern = (
            rf"(?<![A-Za-z0-9_.-]){re.escape(form)}(?:\(\d{{1,2}}\))*"
            r"(?![A-Za-z0-9_-])(?!\.[A-Za-z0-9])"
        )
        if re.search(pattern, text, re.IGNORECASE) is not None:
            return True
    return False
