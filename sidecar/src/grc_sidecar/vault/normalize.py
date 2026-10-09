"""Turn file bytes into the one canonical text the whole indexer works on.

Why this exists: chunk IDs hash the file and its line numbers (CLAUDE.md 8.1). If Windows
(CRLF, maybe a BOM) and Linux (LF) saw different text, the same file would get different
IDs. So decoding and line-ending rules live here, once.

Rules:
- UTF-8 only. A leading BOM is dropped. Anything else that is not valid UTF-8 is an error;
  there is no latin-1 fallback, because that would silently turn bytes into the wrong text.
- A NUL byte is an error (binary file, or an attempt to confuse later string handling).
- CRLF and lone CR become LF. Lines are split on "\\n" only, never with str.splitlines(),
  which also splits on U+2028, U+0085, \\x0b and \\x0c and would make our line numbers
  disagree with the numbers an editor shows.
- No Unicode normalization: exact quotes must match the source character for character.
"""

import hashlib

from grc_sidecar.errors import FileTooLargeError, InvalidEncodingError

MAX_FILE_BYTES = 2 * 1024 * 1024
_BOM = chr(0xFEFF)  # the byte-order mark some Windows editors put at the start of a file


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_text(text: str, source_path: str | None = None) -> str:
    """Apply the line-ending and BOM rules to text that is already decoded."""
    if "\x00" in text:
        raise InvalidEncodingError(source_path=source_path)
    return text.removeprefix(_BOM).replace("\r\n", "\n").replace("\r", "\n")


def decode_and_normalize(raw: bytes, source_path: str | None = None) -> str:
    """Decode file bytes as UTF-8 and normalize. Raises typed errors naming the file."""
    if len(raw) > MAX_FILE_BYTES:
        raise FileTooLargeError(source_path=source_path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    # Raised outside the `except` so the decode error (which holds the bytes) is not chained.
    if text is None:
        raise InvalidEncodingError(source_path=source_path)
    return normalize_text(text, source_path)


def split_lines(text: str) -> tuple[str, ...]:
    """Split normalized text into lines. A trailing newline does not add an empty line."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return tuple(lines)
