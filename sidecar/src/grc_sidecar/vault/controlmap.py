"""Which documents map to which control: the `control_mappings` lookup (ADR-016 decision 2, G7).

A `ControlMap` is built from one read-only scan of a vault folder, through the existing scanner and
frontmatter parser. It touches no index, no schema, no network and no model, and it reads the files
at build time, so it cannot be stale.

Rules:
- Only documents that would be company policy count. A document typed `framework` is excluded (and
  counted), the same rule `retrieval.service.source_kind_of` applies to a vault note.
- A mapping entry is normalized with the written forms of `retrieval.identifiers`. Entries are
  usually written with a framework prefix (`NIST-800-53:IA-2`, `ISO27001:A.8.5`); the prefix is
  dropped before the identifier is recognized and is not checked against the rule that matched.
- Matching is on the WHOLE normalized identifier (a dictionary lookup), never a substring: `AC-2`
  does not find `AC-20`, and `3.1.1` does not find `13.1.1` or `3.1.10`.
- The parent rule (ADR-010, the child rule): looking up `IA-2` also returns documents mapped to its
  enhancements, such as `IA-2(1)`. Looking up `IA-2(1)` never returns a document mapped only to
  `IA-2`. Only NIST 800-53 enhancements have this relation.
- Caps, never a silent truncation. More than `MAX_DOCUMENTS` documents is a typed refusal, because
  a partial map would answer "no document maps to X" wrongly. More than `MAX_MAPPING_ENTRIES`
  entries in one document leaves that document out of the map and reports it. An entry that is not
  a recognizable identifier is not mapped and is reported.

Results are in a deterministic order (sorted by vault-relative path, then by entry text).
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict

from grc_sidecar.errors import ControlMapTooLargeError
from grc_sidecar.retrieval.identifiers import identify
from grc_sidecar.vault.models import IndexIssue, ParsedDocument
from grc_sidecar.vault.scan import scan_vault

# Starting values, chosen by the assistant and flagged in ADR-016 decision 2.
MAX_DOCUMENTS: Final = 2000
MAX_MAPPING_ENTRIES: Final = 64

TOO_MANY_ENTRIES_CODE: Final = "control_mappings_too_many"
UNRECOGNIZED_ENTRY_CODE: Final = "control_mapping_unrecognized"

_NIST_800_53_RULE: Final = "nist-800-53"
_FRAMEWORK_PREFIX: Final = re.compile(r"[A-Za-z0-9-]+:")
_ENHANCEMENT: Final = re.compile(r"(.+)\(\d+\)")

# (identifier rule, canonical written form): the whole identifier, never a part of one.
_Key = tuple[str, str]


@dataclass(frozen=True)
class _Entry:
    written: str  # the entry as it stands in the file
    key: _Key
    parent: _Key | None  # the control this one is an enhancement of, if any


class MappedDocument(BaseModel):
    """One document that maps to the looked-up control, and the entries that matched."""

    model_config = ConfigDict(frozen=True)

    source_path: str  # vault-relative, forward slashes
    doc_id: str
    matched_ids: tuple[str, ...]  # the mapping entries as written in the document, sorted


class ControlMapReport(BaseModel):
    """What the build saw. Counts and issue codes only, never document text."""

    model_config = ConfigDict(frozen=True)

    scanned_documents: int
    mapped_documents: int
    excluded_framework: int
    documents_over_entry_cap: int
    unrecognized_entries: int
    issues: tuple[IndexIssue, ...]


def _normalize(entry: str) -> tuple[_Key, _Key | None] | None:
    """The key of a control identifier (and its parent key), or None if it is not one."""
    text = entry.strip()
    prefix = _FRAMEWORK_PREFIX.match(text)
    if prefix is not None and prefix.end() < len(text):
        text = text[prefix.end() :]
    found = identify(text)
    if found is None:
        return None
    canonical = found.forms[0]
    if found.rule == "generic-hyphenated":
        canonical = canonical.upper()
    key = (found.rule, canonical)
    parent: _Key | None = None
    if found.rule == _NIST_800_53_RULE:
        enhancement = _ENHANCEMENT.fullmatch(canonical)
        if enhancement is not None:
            parent = (found.rule, enhancement.group(1))
    return key, parent


def _issue(code: str, document: ParsedDocument) -> IndexIssue:
    return IndexIssue(
        severity="warning",
        code=code,
        source_path=document.source_path,
        field="control_mappings",
    )


class ControlMap:
    """A read-only lookup built from one scan of a vault folder. Use `ControlMap.build`."""

    def __init__(
        self,
        entries: dict[str, tuple[_Entry, ...]],
        doc_ids: dict[str, str],
        report: ControlMapReport,
    ) -> None:
        self._entries = entries
        self._doc_ids = doc_ids
        self.report = report

    @classmethod
    def build(cls, vault_root: Path) -> "ControlMap":
        """Scan the vault once. Raises ControlMapTooLargeError above MAX_DOCUMENTS documents."""
        scan = scan_vault(vault_root)
        if len(scan.documents) > MAX_DOCUMENTS:
            raise ControlMapTooLargeError
        entries: dict[str, tuple[_Entry, ...]] = {}
        doc_ids: dict[str, str] = {}
        issues: list[IndexIssue] = list(scan.issues)
        excluded_framework = 0
        over_cap = 0
        unrecognized = 0
        for document in scan.documents:
            if document.frontmatter.type == "framework":
                excluded_framework += 1
                continue
            written = document.frontmatter.control_mappings
            if len(written) > MAX_MAPPING_ENTRIES:
                over_cap += 1
                issues.append(_issue(TOO_MANY_ENTRIES_CODE, document))
                continue
            kept: list[_Entry] = []
            skipped = 0
            for text in written:
                normalized = _normalize(text)
                if normalized is None:
                    skipped += 1
                else:
                    kept.append(_Entry(written=text, key=normalized[0], parent=normalized[1]))
            if skipped:
                unrecognized += skipped
                issues.append(_issue(UNRECOGNIZED_ENTRY_CODE, document))
            if kept:
                entries[document.source_path] = tuple(kept)
                doc_ids[document.source_path] = document.frontmatter.doc_id
        issues.sort(key=lambda issue: (issue.source_path or "", issue.code, issue.line or 0))
        report = ControlMapReport(
            scanned_documents=len(scan.documents),
            mapped_documents=len(entries),
            excluded_framework=excluded_framework,
            documents_over_entry_cap=over_cap,
            unrecognized_entries=unrecognized,
            issues=tuple(issues),
        )
        return cls(entries, doc_ids, report)

    def documents_for(self, control_id: str) -> tuple[MappedDocument, ...]:
        """The documents mapped to this control (or to one of its enhancements), by path."""
        normalized = _normalize(control_id)
        if normalized is None:
            return ()
        wanted = normalized[0]
        found: list[MappedDocument] = []
        for source_path in sorted(self._entries):
            matched = sorted(
                {
                    entry.written
                    for entry in self._entries[source_path]
                    if entry.key == wanted or entry.parent == wanted
                }
            )
            if matched:
                found.append(
                    MappedDocument(
                        source_path=source_path,
                        doc_id=self._doc_ids[source_path],
                        matched_ids=tuple(matched),
                    )
                )
        return tuple(found)

    def controls_for(self, source_path: str) -> tuple[str, ...]:
        """The normalized control IDs of one document, sorted. Empty if it is not in the map."""
        return tuple(sorted({entry.key[1] for entry in self._entries.get(source_path, ())}))
