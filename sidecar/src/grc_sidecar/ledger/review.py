"""The `Review` column vocabulary (ADR-017 decision 4), one definition.

A reviewer approves an answer by typing `approved` in the Review cell. This is the first approval
path in ALIBI, and it is a human act on a file, so it is read narrowly: ASCII spaces and tabs around
the word are forgiven, and nothing else is. A look-alike letter, a non-breaking space, a zero-width
character or an extra word is never `approved`.
"""

from typing import Final, Literal

ReviewMark = Literal["approved", "rejected", "unreviewed", "other"]

MAX_OTHER_CHARS: Final = 64

_MARKS: Final[dict[str, ReviewMark]] = {
    "approved": "approved",
    "rejected": "rejected",
    "unreviewed": "unreviewed",
}
_ASCII_WHITESPACE: Final = frozenset(" \t\n\r\x0b\x0c")


def classify_review(value: object) -> ReviewMark:
    """What a Review cell says.

    Strip ASCII spaces and tabs from both ends (only those). A blank cell says nothing was decided,
    so it counts as `unreviewed`, the text the writer puts there itself. Any non-ASCII character or
    any whitespace left inside makes the value `other`. Otherwise it is compared with the three
    words, ignoring ASCII case. A cell that is not text (a number, a date) is `other`.
    """
    if value is None:
        return "unreviewed"
    if not isinstance(value, str):
        return "other"
    text = value.strip(" \t")
    if not text:
        return "unreviewed"
    if not text.isascii() or any(char in _ASCII_WHITESPACE for char in text):
        return "other"
    return _MARKS.get(text.lower(), "other")


def shorten_other(value: object) -> tuple[str, bool]:
    """An unrecognized Review value as it may be shown: at most 64 characters, and if it was cut."""
    text = value if isinstance(value, str) else str(value)
    return text[:MAX_OTHER_CHARS], len(text) > MAX_OTHER_CHARS
