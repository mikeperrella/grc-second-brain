"""Find, validate and parse the files of a vault (CLAUDE.md 3.6, 8.1, 8.6).

Safety rules, applied to every file:
- Only `*.md` files are considered. Any file or folder whose name starts with "." (.obsidian,
  .git, .trash, hidden files) is skipped silently, and so is every non-Markdown file.
- Each file goes through the shared path guard (`resolve_within`), and the resolved path must
  equal the plain "vault root + relative path" we walked. So a symlink or Windows junction is
  refused even when it points somewhere inside the vault: indirection could make one file
  appear under two paths, and a link target can change after the check.
- Symlinked folders are not entered.
- A file over the size cap is reported, not read in full.

One bad file never stops the scan. Every problem becomes an IndexIssue naming the file, and
the file is left out of `documents` (no silent skips). If two files declare the same doc_id,
ALL of them are left out, because "first one wins" would depend on directory order.
"""

import os
from pathlib import Path

from grc_sidecar.errors import (
    ConfigurationError,
    DuplicateDocIdError,
    GrcError,
    PathNotAllowedError,
    PathNotFoundError,
    UnsafePathError,
    VaultError,
)
from grc_sidecar.security.paths import resolve_within
from grc_sidecar.vault.frontmatter import error_to_issue, parse_document
from grc_sidecar.vault.models import IndexIssue, ParsedDocument, ScanResult
from grc_sidecar.vault.normalize import MAX_FILE_BYTES, decode_and_normalize


def _is_markdown(name: str) -> bool:
    return name.lower().endswith(".md")


def _walk(root: Path, issues: list[IndexIssue]) -> list[str]:
    """Return vault-relative POSIX paths of candidate Markdown files, in sorted order."""
    found: list[str] = []
    stack: list[tuple[Path, str]] = [(root, "")]
    while stack:
        directory, prefix = stack.pop()
        try:
            with os.scandir(directory) as entries:
                ordered = sorted(entries, key=lambda entry: entry.name)
        except OSError:
            ordered = None
        # Recorded outside the `except` so the OS error (which contains a path) is dropped.
        if ordered is None:
            issues.append(
                IndexIssue(
                    severity="error",
                    code="unreadable_directory",
                    source_path=prefix.rstrip("/") or ".",
                )
            )
            continue
        for entry in ordered:
            if entry.name.startswith("."):
                continue
            relative = f"{prefix}{entry.name}"
            if entry.is_symlink():
                issues.append(_unsafe(relative))
            elif entry.is_dir(follow_symlinks=False):
                resolved = Path(entry.path).resolve()
                if resolved != Path(entry.path):
                    issues.append(_unsafe(relative))  # junction or other redirected folder
                else:
                    stack.append((Path(entry.path), f"{relative}/"))
            elif entry.is_file(follow_symlinks=False) and _is_markdown(entry.name):
                found.append(relative)
    return sorted(found)


def _unsafe(relative: str) -> IndexIssue:
    return error_to_issue(UnsafePathError(source_path=relative))


def _safe_file(root: Path, relative: str) -> Path | None:
    """The canonical path of a vault file, or None if the path guard refuses it."""
    try:
        resolved = resolve_within(relative, root)
    except (PathNotAllowedError, PathNotFoundError):
        return None
    return resolved if resolved == root.joinpath(*relative.split("/")) else None


def _read_limited(path: Path) -> bytes | None:
    """Read at most MAX_FILE_BYTES + 1 bytes (the extra byte tells the caller it was too big).

    Returns None if the file cannot be read.
    """
    try:
        with path.open("rb") as handle:
            return handle.read(MAX_FILE_BYTES + 1)
    except OSError:
        return None


def _parse_file(root: Path, relative: str) -> ParsedDocument | IndexIssue:
    path = _safe_file(root, relative)
    if path is None:
        return _unsafe(relative)
    raw = _read_limited(path)
    if raw is None:
        return IndexIssue(severity="error", code="unreadable_file", source_path=relative)
    try:
        return parse_document(decode_and_normalize(raw, relative), relative)
    except VaultError as error:
        return error_to_issue(error, relative)


def scan_vault(vault_root: Path) -> ScanResult:
    """Scan a vault directory. Raises ConfigurationError only if the root itself is unusable."""
    try:
        root = resolve_within(".", vault_root)
    except GrcError:
        root = None
    if root is None:
        raise ConfigurationError
    issues: list[IndexIssue] = []
    parsed: list[ParsedDocument] = []
    for relative in _walk(root, issues):
        outcome = _parse_file(root, relative)
        if isinstance(outcome, IndexIssue):
            issues.append(outcome)
        else:
            parsed.append(outcome)
            issues.extend(outcome.notes)

    by_doc_id: dict[str, list[ParsedDocument]] = {}
    for doc in parsed:
        by_doc_id.setdefault(doc.frontmatter.doc_id, []).append(doc)
    kept: list[ParsedDocument] = []
    for group in by_doc_id.values():
        if len(group) == 1:
            kept.append(group[0])
            continue
        for doc in group:
            issues.append(
                error_to_issue(DuplicateDocIdError(source_path=doc.source_path, field="doc_id"))
            )
    kept.sort(key=lambda doc: doc.source_path)
    issues.sort(key=lambda issue: (issue.source_path or "", issue.code, issue.line or 0))
    return ScanResult(documents=tuple(kept), issues=tuple(issues))
