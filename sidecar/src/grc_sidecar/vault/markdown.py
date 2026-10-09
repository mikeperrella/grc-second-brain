"""The two Markdown facts both the chunker and the link parser need: code fences and headings.

This is a small line scanner, not a full Markdown parser. It recognizes fenced code blocks
(backtick or tilde, closed by a fence at least as long as the opener, an unclosed fence runs
to the end of the file) and ATX headings (`# Title`). It does not recognize setext headings
(underlined with === or ---) or indented code blocks; that is a documented tradeoff
(ADR-009), chosen because it is easy to explain and to test.
"""

import re
from collections.abc import Sequence
from typing import Final

_FENCE_OPEN: Final = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_HEADING: Final = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
_CLOSING_HASHES: Final = re.compile(r"(?:^|[ \t]+)#+$")


def fenced_flags(lines: Sequence[str], start_index: int) -> list[bool]:
    """For every line, True if it is inside a fenced code block (fence lines included).

    Lines before `start_index` (the frontmatter) are never scanned and are always False.
    """
    flags = [False] * len(lines)
    fence_char = ""
    fence_len = 0
    closing: re.Pattern[str] | None = None
    for index in range(start_index, len(lines)):
        line = lines[index]
        if closing is None:
            match = _FENCE_OPEN.match(line)
            if match is None:
                continue
            fence, info = match.group(1), match.group(2)
            if fence[0] == "`" and "`" in info:
                continue  # backticks in the info string: this is inline code, not a fence
            fence_char, fence_len = fence[0], len(fence)
            closing = re.compile(rf" {{0,3}}{re.escape(fence_char)}{{{fence_len},}}[ \t]*")
            flags[index] = True
        else:
            flags[index] = True
            if closing.fullmatch(line):
                closing = None
    return flags


def heading_of(line: str) -> tuple[int, str] | None:
    """Return (level, text) if the line is an ATX heading, else None. Call only outside fences."""
    match = _HEADING.match(line)
    if match is None:
        return None
    text = (match.group(2) or "").strip()
    text = _CLOSING_HASHES.sub("", text).strip()
    return len(match.group(1)), text
