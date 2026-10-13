"""Inspecting an export: counts only (ADR-016 decision 5, first half).

The owner confirms the MFA set from what this returns, before any count of exceptions exists. The
result holds file facts and counts, and nothing that identifies a person: no login, name, email or
phone. The reader hands over one record at a time; only aggregates are kept here. Duplicate logins
are found by remembering a hash of each login, never the login itself.

Status values and authenticator type strings are text from the file, so they are untrusted: each
is shortened to `MAX_VALUE_CHARS` for display and only the `MAX_LISTED_VALUES` most common are
listed, so a hostile file cannot make the answer large. Both numbers are starting values chosen by
the assistant, flagged for review.
"""

import hashlib
import logging
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from grc_sidecar.reality.export_reader import DEFAULT_CAPS, Caps, read_export

MAX_VALUE_CHARS = 64
MAX_LISTED_VALUES = 200

_log = logging.getLogger(__name__)


class ValueCount(BaseModel):
    """One distinct value from the file and how many rows have it. `value` is untrusted text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: str
    count: int
    truncated: bool


class ExportInspection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sha256: str
    size_bytes: int
    data_rows: int  # well-formed rows only
    malformed_rows: int  # cell count differs from the header; excluded from every other count
    statuses: list[ValueCount]
    statuses_not_listed: int
    authenticator_types: list[ValueCount]
    authenticator_types_not_listed: int
    duplicate_logins: int


def listed_counts(counter: Counter[str]) -> tuple[list[ValueCount], int]:
    """The most common values, shortened for display, and how many more were not listed."""
    ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    listed = [
        ValueCount(
            value=value[:MAX_VALUE_CHARS], count=count, truncated=len(value) > MAX_VALUE_CHARS
        )
        for value, count in ranked[:MAX_LISTED_VALUES]
    ]
    return listed, max(0, len(ranked) - MAX_LISTED_VALUES)


def _split_types(cell: str) -> set[str]:
    return {part.strip() for part in cell.split(",") if part.strip()}


def inspect_export(name: object, root: Path, caps: Caps = DEFAULT_CAPS) -> ExportInspection:
    """Read the export once and return counts. Writes nothing.

    Raises:
        PathNotAllowedError, PathNotFoundError, ConfigurationError: from the path guard.
        ExportRefusedError: the file was refused (a fixed code, never text from the file).
    """
    statuses: Counter[str] = Counter()
    types: Counter[str] = Counter()
    seen_logins: set[bytes] = set()
    duplicates = 0
    rows = 0
    with read_export(name, root, caps) as reader:
        for record in reader.records():
            rows += 1
            statuses[record.status.strip()] += 1
            types.update(_split_types(record.authenticator_types))
            digest = hashlib.sha256(record.login.strip().encode("utf-8")).digest()
            if digest in seen_logins:
                duplicates += 1
            else:
                seen_logins.add(digest)
        result_hash = reader.sha256
        size = reader.size_bytes
        malformed = reader.malformed_rows
    listed_statuses, statuses_not_listed = listed_counts(statuses)
    listed_types, types_not_listed = listed_counts(types)
    _log.info("export inspected: rows=%d malformed=%d duplicates=%d", rows, malformed, duplicates)
    return ExportInspection(
        sha256=result_hash,
        size_bytes=size,
        data_rows=rows,
        malformed_rows=malformed,
        statuses=listed_statuses,
        statuses_not_listed=statuses_not_listed,
        authenticator_types=listed_types,
        authenticator_types_not_listed=types_not_listed,
        duplicate_logins=duplicates,
    )
