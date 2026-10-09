"""Deterministic chunking of a parsed document (CLAUDE.md 8.1, 8.2).

A chunk is a contiguous run of lines of one file. Line numbers are 1-based, inclusive, and
counted in the original file as an editor shows it, so the frontmatter lines count even
though they are not chunked. Each chunk keeps its exact text (grounding checks quote against
it), the heading path above it, and two hashes:

- `chunk_id` = sha256 of (source_path, doc_id, file_sha256, start_line, end_line). This is
  the contract in CLAUDE.md 8.1. It changes whenever the file changes.
- `text_sha256` = sha256 of the chunk's own text. It changes only when that text changes,
  which lets a later "was the cited passage edited?" check ignore edits elsewhere.

Strategy: split at headings (outside code fences); split a section that is too long at blank
lines between blocks; never split inside a fenced code block unless that block alone is too
big for the embedding model; give a heading with no body to the section that follows it.
Same input always gives the same chunks.
"""

import hashlib
import json
from collections.abc import Sequence
from itertools import accumulate
from typing import Final

from grc_sidecar.vault.markdown import fenced_flags, heading_of
from grc_sidecar.vault.models import Chunk, ParsedDocument
from grc_sidecar.vault.normalize import sha256_hex

# Bump when anything in this module changes what chunks come out. The indexer re-chunks every
# document when the stored version differs.
CHUNKER_VERSION: Final = "1"

# A section longer than this (in characters of chunk text) is split at blank lines.
MAX_CHUNK_CHARS: Final = 2000
# The embedded string (prefix + heading path + text) must stay under this. It is provisional:
# nomic-embed-text's context is about 2K tokens, and identifier-dense text can approach one
# token per character. The M3c live test measures it, and the cap is lowered if it fails.
HARD_MAX_EMBED_CHARS: Final = 6000
EMBED_DOCUMENT_PREFIX: Final = "search_document: "

FLAG_SPLIT_AT_LINE: Final = "split_at_line"
FLAG_EMBEDDING_TRUNCATED: Final = "embedding_truncated"


def compute_chunk_id(
    source_path: str, doc_id: str, file_sha256: str, start_line: int, end_line: int
) -> str:
    """The CLAUDE.md 8.1 chunk ID: a hash of a canonical JSON encoding of the five inputs."""
    payload = json.dumps(
        [source_path, doc_id, file_sha256, start_line, end_line],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def embedding_input(heading_path: str, text: str) -> tuple[str, bool]:
    """The exact string sent to the embedding model, and whether it had to be cut to fit.

    Only this string is shortened. The stored chunk text is never cut, so citations and quote
    checks still see the whole passage. A cut is rare (one enormous line) and is flagged.
    """
    full = f"{EMBED_DOCUMENT_PREFIX}{heading_path}\n{text}"
    if len(full) <= HARD_MAX_EMBED_CHARS:
        return full, False
    return full[:HARD_MAX_EMBED_CHARS], True


class _Span:
    """A run of 0-based line indexes [first, last] plus the heading path that applies to it."""

    __slots__ = ("first", "has_body", "heading_path", "last")

    def __init__(self, first: int, last: int, heading_path: str, has_body: bool) -> None:
        self.first = first
        self.last = last
        self.heading_path = heading_path
        self.has_body = has_body


def _is_blank(line: str) -> bool:
    return not line.strip()


def _trim(lines: Sequence[str], first: int, last: int) -> tuple[int, int] | None:
    while first <= last and _is_blank(lines[first]):
        first += 1
    while last >= first and _is_blank(lines[last]):
        last -= 1
    return (first, last) if first <= last else None


def _sections(lines: Sequence[str], body_index: int, fenced: Sequence[bool]) -> list[_Span]:
    """Cut the body into sections at headings, tracking the heading path."""
    # Each boundary is (first line index, heading path, starts_with_heading). The first one is
    # the "preamble": whatever sits between the frontmatter and the first heading.
    boundaries: list[tuple[int, str, bool]] = [(body_index, "", False)]
    stack: list[tuple[int, str]] = []
    for index in range(body_index, len(lines)):
        if fenced[index]:
            continue
        found = heading_of(lines[index])
        if found is None:
            continue
        level, text = found
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, text))
        path = " > ".join(title for _, title in stack if title)
        if boundaries[-1][0] == index:  # the file starts with a heading: no preamble section
            boundaries[-1] = (index, path, True)
        else:
            boundaries.append((index, path, True))
    sections: list[_Span] = []
    for position, (start, path, is_heading) in enumerate(boundaries):
        end = boundaries[position + 1][0] - 1 if position + 1 < len(boundaries) else len(lines) - 1
        trimmed = _trim(lines, start, end)
        if trimmed is None:
            continue
        first, last = trimmed
        body_first = first + 1 if is_heading else first
        has_body = any(not _is_blank(lines[i]) for i in range(body_first, last + 1))
        sections.append(_Span(first, last, path, has_body))
    return sections


def _merge_bodyless_headings(sections: list[_Span]) -> list[_Span]:
    """A heading with nothing under it joins the section that follows (never a chunk alone)."""
    merged: list[_Span] = []
    carry: _Span | None = None
    for section in sections:
        if carry is not None:
            section = _Span(carry.first, section.last, section.heading_path, section.has_body)
            carry = None
        if not section.has_body:
            carry = section
            continue
        merged.append(section)
    if carry is not None:
        merged.append(carry)  # a bare heading at the very end of the file has nothing to join
    return merged


def _split_section(
    lines: Sequence[str], fenced: Sequence[bool], cumulative: Sequence[int], section: _Span
) -> list[tuple[int, int, tuple[str, ...]]]:
    """Split one section into (first, last, flags) pieces that respect the size limits.

    cumulative[i] is the number of characters in lines[0:i], counting one newline per line, so
    the length of the text of lines[a..b] is cumulative[b + 1] - cumulative[a] - 1.
    """

    def text_len(first: int, last: int) -> int:
        return cumulative[last + 1] - cumulative[first] - 1

    def embed_len(first: int, last: int) -> int:
        return len(EMBED_DOCUMENT_PREFIX) + len(section.heading_path) + 1 + text_len(first, last)

    if text_len(section.first, section.last) <= MAX_CHUNK_CHARS:
        return [(section.first, section.last, ())]

    blocks: list[tuple[int, int]] = []
    start: int | None = None
    for index in range(section.first, section.last + 1):
        blank = _is_blank(lines[index]) and not fenced[index]
        if blank and start is not None:
            blocks.append((start, index - 1))
            start = None
        elif not blank and start is None:
            start = index
    if start is not None:
        blocks.append((start, section.last))

    def heading_only(first: int, last: int) -> bool:
        """True if every non-blank line in the range is a heading."""
        return all(
            _is_blank(lines[i]) or (not fenced[i] and heading_of(lines[i]) is not None)
            for i in range(first, last + 1)
        )

    packed: list[tuple[int, int]] = []
    for first, last in blocks:
        # A heading is never left alone: it always joins the block after it, however big.
        if packed and (
            heading_only(*packed[-1]) or text_len(packed[-1][0], last) <= MAX_CHUNK_CHARS
        ):
            packed[-1] = (packed[-1][0], last)
        else:
            packed.append((first, last))

    pieces: list[tuple[int, int, tuple[str, ...]]] = []
    for first, last in packed:
        if embed_len(first, last) <= HARD_MAX_EMBED_CHARS:
            pieces.append((first, last, ()))
            continue
        group_first = first
        for index in range(first, last + 1):
            if index > group_first and text_len(group_first, index) > MAX_CHUNK_CHARS:
                trimmed = _trim(lines, group_first, index - 1)
                if trimmed is not None and heading_only(*trimmed):
                    continue  # a heading never ends a group alone: let the group keep growing
                if trimmed is not None:
                    pieces.append((*trimmed, (FLAG_SPLIT_AT_LINE,)))
                group_first = index
        trimmed = _trim(lines, group_first, last)
        if trimmed is not None:
            pieces.append((*trimmed, (FLAG_SPLIT_AT_LINE,)))
    return pieces


def chunk_document(doc: ParsedDocument) -> tuple[Chunk, ...]:
    """Chunk a parsed document. Pure function: same document in, same chunks out."""
    lines = doc.lines
    body_index = doc.body_start_line - 1
    fenced = fenced_flags(lines, body_index)
    sections = _merge_bodyless_headings(_sections(lines, body_index, fenced))
    cumulative = [0, *accumulate(len(line) + 1 for line in lines)]
    chunks: list[Chunk] = []
    for section in sections:
        for first, last, flags in _split_section(lines, fenced, cumulative, section):
            text = "\n".join(lines[first : last + 1])
            chunk_flags = flags
            if embedding_input(section.heading_path, text)[1]:
                chunk_flags = (*flags, FLAG_EMBEDDING_TRUNCATED)
            start_line, end_line = first + 1, last + 1
            chunks.append(
                Chunk(
                    chunk_id=compute_chunk_id(
                        doc.source_path,
                        doc.frontmatter.doc_id,
                        doc.file_sha256,
                        start_line,
                        end_line,
                    ),
                    source_path=doc.source_path,
                    doc_id=doc.frontmatter.doc_id,
                    start_line=start_line,
                    end_line=end_line,
                    heading_path=section.heading_path,
                    text=text,
                    text_sha256=sha256_hex(text),
                    flags=chunk_flags,
                )
            )
    return tuple(chunks)
