"""Cleaning text before it goes into a workbook cell (ADR-014, CLAUDE.md 8.6).

Everything the model returns, and every quote from a vault note, is untrusted text: a hostile note
or questionnaire cell can steer what the model says. The cell is the last place that can be made
safe. Three things are done here, and one is deliberately not:

- Characters the file format cannot hold are removed (control characters, lone surrogates, the two
  non-characters). openpyxl refuses some of them with an error, and the rest would produce a file
  that Excel reports as corrupt.
- Line endings are normalized to `\\n`. An XML parser turns a carriage return into a line feed on
  reading, so the text read back would differ from the text written (and `\\r\\n` would become two
  line feeds). This is the only change to the text apart from the length cap.
- The length is capped below Excel's limit of 32,767 characters, with a visible marker.
- Text that starts with `=`, `+`, `-`, `@`, a tab, a carriage return or a line feed is **not
  altered**: it is reported as needing a quote prefix, and the writer stores such a cell as a typed
  string with the prefix style set (`needs_quote_prefix`). Excel then shows the text and keeps it
  text even if someone presses F2 and Enter. The honest trade: a visible apostrophe survives a CSV
  export and this does not, but this keeps quotes verbatim and Markdown `-` and `+` readable.

Unicode direction controls (U+202A to U+202E, U+2066 to U+2069, U+200E, U+200F, U+061C) can make
text display in a different order from the one it is stored in, so a reviewer could read something
else than what is there. They are handled by where the text comes from. In an **answer**, which is
model output with no verbatim requirement, they are removed (`remove_direction_controls`). In a
**quote**, which must stay verbatim, they are kept, and the Evidence cell is prefixed with a visible
marker (`DIRECTION_MARKER`) when the text contains any, so the reviewer knows what is displayed may
be reordered. Other look-alike tricks (homoglyphs, zero-width characters) are not handled.
"""

import re
from typing import Final

MAX_CELL_CHARS: Final = 32_000  # Excel allows 32,767
TRUNCATION_MARK: Final = " [truncated]"

_BACKSLASH: Final = chr(0x5C)


def _character_class(*ranges: tuple[int, int]) -> re.Pattern[str]:
    """A regex class of code point ranges, written with `\\uXXXX` escapes the regex engine reads.

    The ranges are given as numbers, never as the characters themselves: this file must stay plain
    ASCII, because a source file that contains bidirectional control characters is exactly what
    Bandit (B613) and Semgrep flag as a "Trojan Source" attack.
    """
    body = "".join(f"{_BACKSLASH}u{low:04x}-{_BACKSLASH}u{high:04x}" for low, high in ranges)
    return re.compile(f"[{body}]")


# U+202A to U+202E, U+2066 to U+2069, U+200E, U+200F and U+061C: the twelve direction controls.
_DIRECTION_CONTROLS: Final = _character_class(
    (0x202A, 0x202E), (0x2066, 0x2069), (0x200E, 0x200F), (0x061C, 0x061C)
)
DIRECTION_MARKER: Final = "[contains direction-control characters] "

# The control characters openpyxl rejects (0x00-0x08, 0x0b, 0x0c, 0x0e-0x1f, the same set as its
# own ILLEGAL_CHARACTERS_RE, which a test compares), plus lone surrogates and U+FFFE and U+FFFF,
# which XML 1.0 does not allow.
_UNSAFE: Final = _character_class(
    (0x00, 0x08), (0x0B, 0x0C), (0x0E, 0x1F), (0xD800, 0xDFFF), (0xFFFE, 0xFFFF)
)

# A cell that starts with one of these can be read as a formula by a spreadsheet program, or by
# the program a CSV export is opened in. A carriage return becomes a line feed in `clean_cell_text`,
# so the line feed is listed too.
FORMULA_TRIGGERS: Final = ("=", "+", "-", "@", "\t", "\r", "\n")


def clean_cell_text(text: str) -> str:
    """The text as it may be stored: unsafe characters removed, LF line endings, length capped."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = _UNSAFE.sub("", normalized)
    if len(cleaned) > MAX_CELL_CHARS:
        return cleaned[: MAX_CELL_CHARS - len(TRUNCATION_MARK)] + TRUNCATION_MARK
    return cleaned


def remove_direction_controls(text: str) -> str:
    """The text without Unicode direction controls (for model output, which is not a quote)."""
    return _DIRECTION_CONTROLS.sub("", text)


def contains_direction_controls(text: str) -> bool:
    return _DIRECTION_CONTROLS.search(text) is not None


def needs_quote_prefix(cleaned: str) -> bool:
    """True if the (already cleaned) text starts with a character a formula can start with."""
    return cleaned.startswith(FORMULA_TRIGGERS)
