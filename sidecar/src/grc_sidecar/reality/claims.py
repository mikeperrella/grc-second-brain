"""Finding the policy claim "MFA is required" in the vault (ADR-016 decision 10).

Code, not the model: a sentence matches when it holds an MFA term (`MFA`, `multi-factor`,
`multifactor`, `two-factor`, `2FA`) and a requirement term (`must`, `shall`, `required`,
`require`), both as whole words, case-insensitive. A model would add a way for the result to be
wrong or to be steered by vault text; a pattern match is simple enough to read and test.

Rules:
- Read-only. One scan of the vault through the existing scanner, so its path guard, symlink refusal
  and file caps apply. Documents typed `framework` are left out (counted): framework text is never
  a claim about this company.
- Every match is listed with its vault-relative path, exact lines and quote. None is chosen
  silently. A sentence with an exception or scope word is shown with those words named
  (`qualifier_words`), never interpreted: they change nothing in any count.
- `ControlMap` cross-checks and ranks, never hides: a candidate in a document that maps to none of
  the authentication controls in `auth_controls.json` is listed with a note saying so.
- The quote is the sentence with whitespace runs collapsed to one space and the ends trimmed (the
  one change `grounding.normalize` allows). A sentence may span lines; `start_line` and `end_line`
  are the first and last line it touches. Sentences are cut after `.`, `!` or `?` followed by
  whitespace, so a sentence that contains "e.g." is kept whole, but an unusual abbreviation can
  still cut a sentence short; the owner reads the quote before choosing it.
- Caps (starting values chosen by the assistant, flagged in ADR-016): a sentence longer than
  `MAX_QUOTE_CHARS` is not listed but counted (`too_long`); at most `MAX_LISTED_CANDIDATES` are
  listed, the rest counted (`not_listed`).
"""

import re
from bisect import bisect_right
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict

from grc_sidecar.errors import CheckRefusedError
from grc_sidecar.questionnaire.grounding import normalize
from grc_sidecar.reality.auth_controls import auth_control_ids
from grc_sidecar.vault.controlmap import ControlMap
from grc_sidecar.vault.models import ParsedDocument
from grc_sidecar.vault.scan import scan_vault

MAX_QUOTE_CHARS: Final = 1000
MAX_LISTED_CANDIDATES: Final = 100

MFA_TERM: Final = re.compile(r"\b(?:MFA|multi-?factor|two-?factor|2FA)\b", re.IGNORECASE)
REQUIREMENT_TERM: Final = re.compile(r"\b(?:must|shall|required|require)\b", re.IGNORECASE)

# Words shown beside a claim so the owner sees what it may exclude or narrow. Display only.
QUALIFIER_WORDS: Final = (
    "except",
    "exception",
    "exceptions",
    "exempt",
    "exempted",
    "excluding",
    "excluded",
    "unless",
    "only",
    "waiver",
    "staff",
    "employee",
    "employees",
    "contractor",
    "contractors",
    "administrator",
    "administrators",
    "admin",
    "admins",
    "remote",
    "production",
    "customer",
)
_QUALIFIER: Final = re.compile(r"\b(?:" + "|".join(QUALIFIER_WORDS) + r")\b", re.IGNORECASE)

MAPPED_NOTE: Final = "This document maps to an authentication control."
UNMAPPED_NOTE: Final = (
    "This document does not map to any authentication control in the list this check uses; "
    "the claim is listed anyway."
)

_FENCE: Final = re.compile(r"^\s{0,3}(?:```|~~~)")
_HEADING: Final = re.compile(r"^\s{0,3}#{1,6}(?:\s|$)")
_TABLE_ROW: Final = re.compile(r"^\s*\|")
_LIST_MARKER: Final = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_QUOTE_MARKER: Final = re.compile(r"^\s*>+\s?")
_BOUNDARY: Final = re.compile(r"(?<=[.!?])\s+")
_NO_SPLIT_AFTER: Final = ("e.g.", "i.e.", "vs.")


class ClaimCandidate(BaseModel):
    """One sentence that reads like "MFA is required", and where it is."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str  # vault-relative, forward slashes
    doc_id: str
    start_line: int
    end_line: int
    quote: str
    qualifier_words: tuple[str, ...]
    mapped_to_authentication_control: bool
    matched_control_ids: tuple[str, ...]  # the document's own mapping entries that matched
    control_note: str


class ClaimReport(BaseModel):
    """What the finder saw. Candidates are vault text, so this goes only to the authenticated
    caller and never to a log."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidates: tuple[ClaimCandidate, ...]
    candidates_not_listed: int
    too_long: int
    scanned_documents: int
    excluded_framework: int


@dataclass(frozen=True)
class _Block:
    lines: tuple[tuple[int, str], ...]  # (1-based line number, text)
    atomic: bool  # a heading or table row is one unit, never cut into sentences


def _blocks(lines: tuple[str, ...], first_index: int) -> list[_Block]:
    """Group body lines into paragraphs, list items, headings and table rows. Code is skipped."""
    found: list[_Block] = []
    current: list[tuple[int, str]] = []
    in_fence = False

    def flush() -> None:
        if current:
            found.append(_Block(tuple(current), atomic=False))
            current.clear()

    for index in range(first_index, len(lines)):
        number, text = index + 1, lines[index]
        if _FENCE.match(text):
            flush()
            in_fence = not in_fence
        elif in_fence:
            continue
        elif not text.strip():
            flush()
        elif _HEADING.match(text) or _TABLE_ROW.match(text):
            flush()
            found.append(_Block(((number, text),), atomic=True))
        elif _LIST_MARKER.match(text):
            flush()
            current.append((number, _LIST_MARKER.sub("", text, count=1)))
        else:
            current.append((number, _QUOTE_MARKER.sub("", text, count=1)))
    flush()
    return found


def _units(block: _Block) -> Iterator[tuple[int, int, str]]:
    """The sentences of a block as (start line, end line, normalized text)."""
    text = "\n".join(line for _, line in block.lines)
    starts: list[int] = []
    position = 0
    for _, line in block.lines:
        starts.append(position)
        position += len(line) + 1
    cuts = [0]
    if not block.atomic:
        for match in _BOUNDARY.finditer(text):
            if not text[: match.start()].lower().endswith(_NO_SPLIT_AFTER):
                cuts.append(match.end())
    cuts.append(len(text))
    for begin, end in pairwise(cuts):
        piece = text[begin:end]
        if not piece.strip():
            continue
        first = begin + len(piece) - len(piece.lstrip())
        last = begin + len(piece.rstrip()) - 1
        start_line = block.lines[bisect_right(starts, first) - 1][0]
        end_line = block.lines[bisect_right(starts, last) - 1][0]
        yield start_line, end_line, normalize(piece)


def _qualifiers(quote: str) -> tuple[str, ...]:
    found = {match.group(0).lower() for match in _QUALIFIER.finditer(quote)}
    return tuple(word for word in QUALIFIER_WORDS if word in found)


@dataclass(frozen=True)
class _Found:
    candidates: tuple[ClaimCandidate, ...]  # every candidate, sorted, no list cap
    too_long: int
    scanned_documents: int
    excluded_framework: int


def _mapped_documents(vault_root: Path) -> dict[str, set[str]]:
    """Document path to the mapping entries that matched an authentication control."""
    control_map = ControlMap.build(vault_root)
    mapped: dict[str, set[str]] = {}
    for control_id in auth_control_ids():
        for document in control_map.documents_for(control_id):
            mapped.setdefault(document.source_path, set()).update(document.matched_ids)
    return mapped


def _candidates_of(
    document: ParsedDocument, mapped: dict[str, set[str]]
) -> tuple[list[ClaimCandidate], int]:
    matched_ids = tuple(sorted(mapped.get(document.source_path, ())))
    found: list[ClaimCandidate] = []
    too_long = 0
    for block in _blocks(document.lines, document.body_start_line - 1):
        for start_line, end_line, quote in _units(block):
            if not (MFA_TERM.search(quote) and REQUIREMENT_TERM.search(quote)):
                continue
            if len(quote) > MAX_QUOTE_CHARS:
                too_long += 1
                continue
            is_mapped = document.source_path in mapped
            found.append(
                ClaimCandidate(
                    path=document.source_path,
                    doc_id=document.frontmatter.doc_id,
                    start_line=start_line,
                    end_line=end_line,
                    quote=quote,
                    qualifier_words=_qualifiers(quote),
                    mapped_to_authentication_control=is_mapped,
                    matched_control_ids=matched_ids,
                    control_note=MAPPED_NOTE if is_mapped else UNMAPPED_NOTE,
                )
            )
    return found, too_long


def _find_all(vault_root: Path) -> _Found:
    """Scan the vault once and collect every candidate (ranking only: mapped first)."""
    scan = scan_vault(vault_root)
    mapped = _mapped_documents(vault_root)
    candidates: list[ClaimCandidate] = []
    too_long = 0
    excluded = 0
    for document in scan.documents:
        if document.frontmatter.type == "framework":
            excluded += 1
            continue
        found, long_ones = _candidates_of(document, mapped)
        candidates.extend(found)
        too_long += long_ones
    candidates.sort(
        key=lambda c: (not c.mapped_to_authentication_control, c.path, c.start_line, c.end_line)
    )
    return _Found(tuple(candidates), too_long, len(scan.documents), excluded)


def find_claims(vault_root: Path) -> ClaimReport:
    """Every candidate claim in the vault's company-policy documents, up to the list cap.

    Raises:
        ConfigurationError: the vault folder is unusable.
        ControlMapTooLargeError: the vault has too many documents for the control map.
        AuthControlsError: the packaged control list is malformed.
    """
    found = _find_all(vault_root)
    listed = found.candidates[:MAX_LISTED_CANDIDATES]
    return ClaimReport(
        candidates=listed,
        candidates_not_listed=len(found.candidates) - len(listed),
        too_long=found.too_long,
        scanned_documents=found.scanned_documents,
        excluded_framework=found.excluded_framework,
    )


def verify_claim(
    vault_root: Path, *, path: str, start_line: int, end_line: int, quote: str
) -> ClaimCandidate:
    """The current candidate that equals the submitted claim, or a typed refusal.

    Equal means the same path and lines and the same quote as a WHOLE after `normalize` on both
    sides. A fragment of a candidate (the sentence with its qualifier words cut off) is refused.
    The path is only ever compared with the paths `scan_vault` returned, never opened, so a path
    outside the vault or through a link matches nothing.

    Raises:
        CheckRefusedError: `claim_not_verbatim`.
    """
    wanted = normalize(quote)
    for candidate in _find_all(vault_root).candidates:
        if (
            candidate.path == path
            and candidate.start_line == start_line
            and candidate.end_line == end_line
            and normalize(candidate.quote) == wanted
        ):
            return candidate
    raise CheckRefusedError("claim_not_verbatim")
