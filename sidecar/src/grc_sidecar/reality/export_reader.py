"""Reading an offline IT export: an Okta "MFA Enrollment by User" CSV (ADR-016 decisions 4, 8, 9).

The only module that imports `csv` (a Semgrep rule and a test enforce that). The file is untrusted
and holds personal data, so the reader is built around three promises:

1. **One open, one pass.** `resolve_within` puts the file inside the approved import folder, the
   file is opened once and read in 64 KiB chunks, and every byte read is hashed and decoded
   incrementally. The SHA-256 therefore covers exactly the bytes that were parsed, and a refusal
   raises instead of returning a partial result.
2. **Four fields per row, kept for that row only.** The record holds the four required columns;
   the other nine cells are never stored in any object, return value, error, log line or repr.
   The honest limit: no parser can avoid seeing a row's other cells for an instant (ADR-016
   decision 8). The reader yields one record at a time and never builds a list of rows.
3. **Refuse, never repair.** An encoding outside the allowlist, a NUL character, a cap exceeded or
   a missing or repeated required header is a typed refusal with a fixed code. A data row whose
   cell count differs from the header is counted as malformed and is never reshaped.

Cells are text. A cell that begins with `=`, `+`, `-` or `@` is data and is never evaluated.
Nothing is written to a file. Error messages never carry text from the file: failures from the
decoder and the csv module are turned into a fixed code after their `except` block, so no
chained exception can quote file bytes.
"""

import codecs
import csv
import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Final, Self

from grc_sidecar.errors import ExportRefusal, ExportRefusedError
from grc_sidecar.security.paths import resolve_within

CHUNK_BYTES: Final = 64 * 1024
LOGIN_COLUMN: Final = "user.login"
STATUS_COLUMN: Final = "user.status"
TYPES_COLUMN: Final = "authenticators.type"
ADMIN_COLUMN: Final = "user.isAdmin"
REQUIRED_COLUMNS: Final = (LOGIN_COLUMN, STATUS_COLUMN, TYPES_COLUMN, ADMIN_COLUMN)

_UTF8_BOM: Final = codecs.BOM_UTF8
_UTF16_LE_BOM: Final = codecs.BOM_UTF16_LE
_UTF16_BE_BOM: Final = codecs.BOM_UTF16_BE
# A UTF-32 mark begins with the UTF-16 one (FF FE 00 00), so it is checked first and refused.
_UTF32_BOMS: Final = (codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)
_FIELD_LIMIT_MESSAGE: Final = "field larger than field limit"


@dataclass(frozen=True)
class Caps:
    """Starting values chosen by the assistant and flagged for review (ADR-016 decision 9)."""

    max_file_bytes: int = 10 * 1024 * 1024
    max_rows: int = 100_000
    max_field_chars: int = 4_096


DEFAULT_CAPS: Final = Caps()


@dataclass(frozen=True, slots=True, repr=False)
class ExportRecord:
    """The four fields of one data row. Its repr shows no value, so a log line or a failed test
    cannot print a login."""

    login: str
    status: str
    authenticator_types: str
    is_admin: str

    def __repr__(self) -> str:
        return "ExportRecord(<four fields not shown>)"


def _decode(decoder: codecs.IncrementalDecoder, data: bytes, *, final: bool) -> str | None:
    """Decode strictly. None means the bytes are not valid in the encoding."""
    try:
        return decoder.decode(data, final=final)
    except UnicodeDecodeError:
        return None


def _next_row(reader: Iterator[list[str]]) -> list[str] | None:
    """The next parsed row, or None at the end. A csv failure becomes a fixed refusal code."""
    code: ExportRefusal | None = None
    row: list[str] | None = None
    try:
        row = next(reader, None)
    except csv.Error as exc:
        # The csv module's own limit is much larger than ours; a field over it is still "too long".
        code = "field_too_long" if str(exc).startswith(_FIELD_LIMIT_MESSAGE) else "unreadable"
    if code is not None:
        raise ExportRefusedError(code)
    return row


class ExportReader:
    """An open export. Use as a context manager, then iterate `records()` exactly once.

    `sha256`, `size_bytes` and `malformed_rows` are complete only after `records()` has been
    iterated to the end; asking sooner is a programming error.
    """

    def __init__(self, path: Path, caps: Caps = DEFAULT_CAPS) -> None:
        self._path = path
        self._caps = caps
        self._handle: BinaryIO | None = None
        self._hash = hashlib.sha256()
        self._size = 0
        self._malformed = 0
        self._started = False
        self._finished = False

    def __enter__(self) -> Self:
        handle: BinaryIO | None = None
        try:
            if self._path.is_file():
                handle = open(self._path, "rb")
        except OSError:
            handle = None
        if handle is None:
            raise ExportRefusedError("unreadable")
        self._handle = handle
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    @property
    def sha256(self) -> str:
        self._require_finished()
        return self._hash.hexdigest()

    @property
    def size_bytes(self) -> int:
        self._require_finished()
        return self._size

    @property
    def malformed_rows(self) -> int:
        """Data rows whose cell count differs from the header. Counted, never reshaped."""
        self._require_finished()
        return self._malformed

    def _require_finished(self) -> None:
        if not self._finished:
            raise RuntimeError("the export has not been read to the end")

    def records(self) -> Iterator[ExportRecord]:
        """Yield one record per well-formed data row, in file order, in a single pass."""
        if self._handle is None or self._started:
            raise RuntimeError("an export can be read once, inside its context manager")
        self._started = True
        reader = csv.reader(self._lines())
        header = _next_row(reader) or []
        self._check_cells(header)
        positions = _required_positions(header)
        login_at, status_at, types_at, admin_at = positions
        width = len(header)
        data_rows = 0
        while (row := _next_row(reader)) is not None:
            if not row:
                continue  # a blank line carries no data
            data_rows += 1
            if data_rows > self._caps.max_rows:
                raise ExportRefusedError("too_many_rows")
            self._check_cells(row)
            if len(row) != width:
                self._malformed += 1
                continue
            yield ExportRecord(
                login=row[login_at],
                status=row[status_at],
                authenticator_types=row[types_at],
                is_admin=row[admin_at],
            )
        self._finished = True

    def _check_cells(self, row: list[str]) -> None:
        """Every cell is checked, including the nine that are dropped."""
        limit = self._caps.max_field_chars
        if any(len(cell) > limit for cell in row):
            raise ExportRefusedError("field_too_long")

    def _lines(self) -> Iterator[str]:
        """Decoded text split only on "\\n", each line keeping its ending.

        Never `str.splitlines`: it also splits on U+2028, U+2029, \\x0b, \\x0c, \\x1c to \\x1e and
        \\x85, which would cut a quoted field in two. The csv module reads "\\r\\n" and newlines
        inside quotes itself.
        """
        handle = self._handle
        if handle is None:
            raise RuntimeError("the export is not open")
        decoder: codecs.IncrementalDecoder | None = None
        parts: list[str] = []  # the unfinished last line, kept in pieces so joining stays linear
        while chunk := handle.read(CHUNK_BYTES):
            self._size += len(chunk)
            if self._size > self._caps.max_file_bytes:
                raise ExportRefusedError("file_too_large")
            self._hash.update(chunk)
            if decoder is None:
                decoder, chunk = _choose_decoder(chunk)
            text = _decode(decoder, chunk, final=False)
            if text is None:
                raise ExportRefusedError("encoding_not_allowed")
            if "\x00" in text:
                raise ExportRefusedError("nul_character")
            if "\n" not in text:
                parts.append(text)
                continue
            *complete, last = ("".join(parts) + text).split("\n")
            parts = [last]
            for line in complete:
                yield line + "\n"
        if decoder is not None:
            tail = _decode(decoder, b"", final=True)  # a cut-off multibyte sequence fails here
            if tail is None:
                raise ExportRefusedError("encoding_not_allowed")
            parts.append(tail)
        pending = "".join(parts)
        if pending:
            yield pending


def _choose_decoder(first_chunk: bytes) -> tuple[codecs.IncrementalDecoder, bytes]:
    """The decoder for the file's encoding and the chunk without its byte-order mark.

    UTF-8 (with or without a mark) and UTF-16 with a mark are accepted. Everything else is
    refused, a UTF-32 mark included; UTF-16 without a mark is read as UTF-8 and fails there.
    """
    if first_chunk.startswith(_UTF32_BOMS):
        raise ExportRefusedError("encoding_not_allowed")
    if first_chunk.startswith(_UTF8_BOM):
        return codecs.getincrementaldecoder("utf-8")("strict"), first_chunk[len(_UTF8_BOM) :]
    if first_chunk.startswith(_UTF16_LE_BOM):
        return codecs.getincrementaldecoder("utf-16-le")("strict"), first_chunk[2:]
    if first_chunk.startswith(_UTF16_BE_BOM):
        return codecs.getincrementaldecoder("utf-16-be")("strict"), first_chunk[2:]
    return codecs.getincrementaldecoder("utf-8")("strict"), first_chunk


def _required_positions(header: list[str]) -> tuple[int, int, int, int]:
    """Where each required column is. Each must appear exactly once."""
    found: list[int] = []
    for column in REQUIRED_COLUMNS:
        at = [i for i, name in enumerate(header) if name == column]
        if not at:
            raise ExportRefusedError("missing_column", column)
        if len(at) > 1:
            raise ExportRefusedError("duplicate_column", column)
        found.append(at[0])
    return found[0], found[1], found[2], found[3]


def read_export(name: object, root: Path, caps: Caps = DEFAULT_CAPS) -> ExportReader:
    """A reader for an export file inside `root`. Open it with `with`.

    Raises:
        PathNotAllowedError, PathNotFoundError, ConfigurationError: from the path guard.
    """
    return ExportReader(resolve_within(name, root), caps)
