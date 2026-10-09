"""Explicit links: `[[wikilinks]]` in the body and `links` in frontmatter (CLAUDE.md 8.2).

The graph is built only from links a person wrote, never from similarity. This module has two
halves:

1. Extraction reads a document and returns the links exactly as written (RawLink).
2. Resolution decides which document each link points at, using ONLY the in-memory set of
   documents that already passed the path guard. It never touches the filesystem, so a link
   such as `[[../../secret]]` or `[[C:\\Windows\\win.ini]]` cannot cause any file to be read.

A link that cannot be resolved is reported as `unresolved`, `ambiguous` or `rejected`. We never
guess or invent a target.
"""

import posixpath
import re
from collections.abc import Iterable, Sequence
from typing import Final

from grc_sidecar.vault.markdown import fenced_flags
from grc_sidecar.vault.models import ParsedDocument, RawLink, ResolvedLink

_WIKILINK: Final = re.compile(r"(!?)\[\[([^\[\]\n]+)\]\]")
_CODE_SPAN: Final = re.compile(r"(`+).+?\1")
_DRIVE: Final = re.compile(r"^[A-Za-z]:")
_MD_SUFFIX: Final = re.compile(r"\.md$", re.IGNORECASE)
_MAX_TARGET_CHARS: Final = 512


def split_link_text(inner: str) -> tuple[str, str | None, str | None]:
    """Split the inside of `[[...]]` into (target, alias, fragment).

    `[[page|alias]]` has an alias. `[[page#Heading]]` and `[[page#^block]]` have a fragment.
    A trailing backslash on the target is the escape Obsidian uses for `|` inside tables.
    """
    target_part, pipe, alias_part = inner.partition("|")
    alias = (alias_part.strip() or None) if pipe else None
    target, hash_sign, fragment_part = target_part.rstrip("\\").partition("#")
    fragment = (fragment_part.strip() or None) if hash_sign else None
    return target.strip(), alias, fragment


def extract_links(doc: ParsedDocument) -> tuple[RawLink, ...]:
    """All links in a document: frontmatter `links` first, then body wikilinks in line order.

    Links inside fenced code blocks and inline code spans are ignored.
    """
    found: list[RawLink] = []
    for item in doc.frontmatter.links:
        target, alias, fragment = split_link_text(item)
        found.append(
            RawLink(
                source_path=doc.source_path,
                source_line=doc.frontmatter_line_of_links,
                target=target,
                alias=alias,
                fragment=fragment,
                origin="frontmatter",
            )
        )
    body_index = doc.body_start_line - 1
    fenced = fenced_flags(doc.lines, body_index)
    for index in range(body_index, len(doc.lines)):
        if fenced[index]:
            continue
        visible = _CODE_SPAN.sub(lambda match: " " * len(match.group(0)), doc.lines[index])
        for match in _WIKILINK.finditer(visible):
            target, alias, fragment = split_link_text(match.group(2))
            found.append(
                RawLink(
                    source_path=doc.source_path,
                    source_line=index + 1,
                    target=target,
                    alias=alias,
                    fragment=fragment,
                    is_embed=match.group(1) == "!",
                )
            )
    return tuple(found)


def rejection_code(target: str, fragment: str | None) -> str | None:
    """Why a link target is refused outright, or None if it is acceptable to look up.

    These are the shapes that could mean "somewhere outside the vault" (absolute paths, drive
    letters, UNC, parent segments, URL-like text). They are refused before any lookup.
    """
    if not target and fragment is None:
        return "empty_target"
    if len(target) > _MAX_TARGET_CHARS:
        return "too_long"
    if "\x00" in target:
        return "nul_byte"
    if target.startswith(("/", "\\")):
        return "absolute_path"
    if "\\" in target:
        return "backslash"
    if _DRIVE.match(target):
        return "drive_letter"
    if ":" in target:
        return "scheme_or_colon"
    if ".." in target.split("/"):
        return "parent_segment"
    return None


class ResolutionIndex:
    """Lookup tables over the valid documents: by path, by doc_id and by file name."""

    def __init__(self, documents: Iterable[tuple[str, str]]) -> None:
        self.doc_id_by_path: dict[str, str] = {}
        self._by_path: dict[str, set[str]] = {}
        self._by_doc_id: dict[str, set[str]] = {}
        self._by_stem: dict[str, set[str]] = {}
        for source_path, doc_id in documents:
            self.doc_id_by_path[source_path] = doc_id
            without_suffix = _MD_SUFFIX.sub("", source_path)
            self._by_path.setdefault(without_suffix.casefold(), set()).add(doc_id)
            self._by_doc_id.setdefault(doc_id.casefold(), set()).add(doc_id)
            stem = without_suffix.rsplit("/", 1)[-1]
            self._by_stem.setdefault(stem.casefold(), set()).add(doc_id)

    def lookup(self, target: str) -> set[str]:
        """Doc IDs matching the target: by path first, then doc_id, then bare file name."""
        cleaned = posixpath.normpath(_MD_SUFFIX.sub("", target)).casefold()
        for table in (self._by_path, self._by_doc_id):
            hits = table.get(cleaned)
            if hits:
                return hits
        if "/" not in cleaned:
            return self._by_stem.get(cleaned, set())
        return set()


def resolve_link(link: RawLink, index: ResolutionIndex) -> ResolvedLink:
    code = rejection_code(link.target, link.fragment)
    if code is not None:
        return ResolvedLink(link=link, status="rejected", code=code)
    if not link.target:  # `[[#Heading]]`: a link to a heading in the same file
        return ResolvedLink(
            link=link, status="resolved", target_doc_id=index.doc_id_by_path.get(link.source_path)
        )
    hits = index.lookup(link.target)
    if not hits:
        return ResolvedLink(link=link, status="unresolved", code="no_such_document")
    if len(hits) > 1:
        return ResolvedLink(link=link, status="ambiguous", code="more_than_one_document")
    return ResolvedLink(link=link, status="resolved", target_doc_id=next(iter(hits)))


def resolve_links(links: Sequence[RawLink], index: ResolutionIndex) -> tuple[ResolvedLink, ...]:
    return tuple(resolve_link(link, index) for link in links)
