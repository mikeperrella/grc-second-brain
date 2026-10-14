"""Recall: do the lines a promise rested on still say what they said? (ADR-017 decisions 17 to 19)

`check_promise` is a pure function: a promise, the current text of its vault file (or the reason
the file could not be read), and today's date in; a result out. It reads nothing and writes
nothing, so the same function serves the recall route and the import's information count.

The states, and exactly what each means:

- `intact`: the cited lines' sha256 equals the one taken when the answer was drafted. This says the
  cited lines are unchanged since drafting. It does not say the promise is still true, and every
  result carries that sentence (`STATEMENT`) so no report can be read as a verdict.
- `moved`: the cited lines' text differs, and the quote occurs verbatim at other lines only. The
  result carries EVERY place it occurs, not the first.
- `changed`: the cited lines' text differs and the quote is not found at other lines. The detail
  says which: `cited_lines_changed` (the quote is still at the same lines but the text around it
  or beside it changed, so the promise's lines are no longer what was promised), or
  `quote_not_found`. Changed text on the cited lines is NEVER `intact`, even if the quote is
  still there.
- `missing`: no text. The detail says why: `not_found`, `unsafe_path`, `too_large`, `not_utf8`.

Flags are information, not states: `document_changed_elsewhere` (the cited lines are intact but the
rest of the file is not what it was), `review_overdue` (the current frontmatter's review date is
before today) and `frontmatter_invalid` (the current frontmatter does not parse, so the review date
is unknown). Recall watches the cited lines only (`SCOPE`).
"""

from dataclasses import dataclass
from datetime import date
from typing import Final

from grc_sidecar.errors import VaultError
from grc_sidecar.questionnaire.grounding import locate_all_quotes
from grc_sidecar.repositories.base import Promise, RecallState
from grc_sidecar.vault.clock import is_stale
from grc_sidecar.vault.frontmatter import parse_document
from grc_sidecar.vault.normalize import sha256_hex, split_lines
from grc_sidecar.vault.scan import ReadReason, VaultText

STATEMENT: Final = (
    "intact means the cited lines are unchanged since drafting; "
    "it does not mean the promise is still true."
)
SCOPE: Final = (
    "Recall watches the cited lines only; a change elsewhere in the document is not a state change."
)
FLAG_ORDER: Final = ("document_changed_elsewhere", "review_overdue", "frontmatter_invalid")


@dataclass(frozen=True)
class RecallResult:
    """What recall found for one promise. The fixed statement and scope are part of the result."""

    state: RecallState
    detail: str | None = None
    locations: tuple[tuple[int, int], ...] = ()  # (start line, end line); for `moved`, all of them
    flags: tuple[str, ...] = ()
    statement: str = STATEMENT
    scope: str = SCOPE


def _cited_lines_sha256(lines: tuple[str, ...], start: int, end: int) -> str | None:
    """The hash of lines `start..end`, joined as `Chunk.text` is; None if they are not all there."""
    if not 1 <= start <= end <= len(lines):
        return None
    return sha256_hex("\n".join(lines[start - 1 : end]))


def _frontmatter_flags(text: str, source_path: str, today: date) -> set[str]:
    try:
        document = parse_document(text, source_path)
    except VaultError:
        return {"frontmatter_invalid"}
    if is_stale(document.frontmatter.review_by, today):
        return {"review_overdue"}
    return set()


def check_promise(promise: Promise, current: VaultText | ReadReason, today: date) -> RecallResult:
    """Compare a promise with the current text of the file it cites. See the module docstring."""
    if not isinstance(current, VaultText):
        return RecallResult("missing", detail=current)
    text = current.text
    flags = _frontmatter_flags(text, promise.source_path, today)
    cited = _cited_lines_sha256(split_lines(text), promise.start_line, promise.end_line)
    if cited == promise.cited_lines_sha256:
        if sha256_hex(text) != promise.cited_file_sha256:
            flags.add("document_changed_elsewhere")
        return RecallResult("intact", flags=_ordered(flags))
    found = tuple(
        (place.start_line, place.end_line) for place in locate_all_quotes(text, promise.quote)
    )
    if (promise.start_line, promise.end_line) in found:
        return RecallResult("changed", "cited_lines_changed", flags=_ordered(flags))
    if found:
        return RecallResult("moved", locations=found, flags=_ordered(flags))
    return RecallResult("changed", "quote_not_found", flags=_ordered(flags))


def _ordered(flags: set[str]) -> tuple[str, ...]:
    return tuple(flag for flag in FLAG_ORDER if flag in flags)
