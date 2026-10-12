"""Safety checks on an .xlsx package, run on the raw bytes before openpyxl sees any of them.

A workbook is a zip file of XML parts, and it is untrusted input (CLAUDE.md 8.6, ADR-014). This
module reads the bytes once, hashes them, and decides whether the file may be handed to the parser:

1. size caps, counted on the bytes actually decompressed (a zip directory can lie about sizes);
2. a search of *every* part, whatever its type, for `<!DOCTYPE` and `<!ENTITY` in the three byte
   layouts the XML parser can read (ASCII or UTF-8, UTF-16 little-endian, UTF-16 big-endian). Binary
   parts (images, `vbaProject.bin`) are searched like any other and never refused for holding NUL
   bytes: they are refused only if they contain the literal text;
3. UTF-32 byte-order marks and declared encodings outside a short list are refused (defence in
   depth for an encoding another build of the parser might accept);
4. the workbook's content type decides whether it is a macro-enabled package (the file extension
   does not);
5. an inventory of the parts the writer must not silently lose (charts, images, macros ...).

Nothing here parses XML. openpyxl uses the standard library's parser (lxml is not installed, and a
test says so), which in turn expands entities unless a DOCTYPE is refused first; step 2 is that
refusal. The check is a byte search, so it also refuses a harmless part that merely contains the
text `<!DOCTYPE`: a known false positive, accepted.

Errors carry a fixed code and never text from the file.
"""

import hashlib
import re
import zipfile
import zlib
from dataclasses import dataclass
from io import BytesIO
from typing import IO, Final

from grc_sidecar.errors import WorkbookRefusal, WorkbookRefusedError

OLE_HEADER: Final = bytes.fromhex("d0cf11e0a1b11ae1")
ZIP_MAGIC: Final = b"PK\x03\x04"
UTF32_BOMS: Final = (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")

CHUNK_BYTES: Final = 64 * 1024
# The ratio checks only apply above this size: a small part can compress very well and be harmless.
RATIO_FLOOR_BYTES: Final = 1024 * 1024

WORKBOOK_PART: Final = "xl/workbook.xml"
CONTENT_TYPES_PART: Final = "[Content_Types].xml"
WORKBOOK_CONTENT_TYPE: Final = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
)
MACRO_WORKBOOK_CONTENT_TYPE: Final = "application/vnd.ms-excel.sheet.macroEnabled.main+xml"

# The encodings a part may declare. Anything else is refused even if some parser would accept it.
ALLOWED_DECLARED_ENCODINGS: Final = frozenset(
    {"utf-8", "utf-16", "us-ascii", "iso-8859-1", "latin-1", "windows-1252", "cp1252"}
)

# The data-validation extension (dropdowns stored in a sheet's extLst), which openpyxl drops.
DATA_VALIDATION_EXTENSION_URI: Final = "{CCE6A557-97BC-4b89-ADB6-D9C93CAAB3DF}"


@dataclass(frozen=True)
class Caps:
    """Size limits. Starting values, approved with the plan; measured on real files (ADR-014)."""

    max_file_bytes: int = 20 * 1024 * 1024
    max_uncompressed_bytes: int = 100 * 1024 * 1024
    max_parts: int = 2_000
    max_ratio: int = 100


DEFAULT_CAPS: Final = Caps()


def _ci_bytes(text: str, encoding: str) -> re.Pattern[bytes]:
    """The text in one byte layout, matched without regard to ASCII letter case."""
    return re.compile(re.escape(text.encode(encoding)), re.IGNORECASE)


_LAYOUTS: Final = ("ascii", "utf-16-le", "utf-16-be")
_DOCTYPE: Final = tuple(_ci_bytes("<!DOCTYPE", layout) for layout in _LAYOUTS)
_ENTITY: Final = tuple(_ci_bytes("<!ENTITY", layout) for layout in _LAYOUTS)
_VALIDATION_EXTENSION: Final = tuple(
    _ci_bytes(DATA_VALIDATION_EXTENSION_URI, layout) for layout in _LAYOUTS
)

_DECLARED_ENCODING: Final = re.compile(
    r"\A\s*<\?xml\s[^>]{0,300}?encoding\s*=\s*[\"']([^\"']{1,40})[\"']", re.IGNORECASE
)
_OVERRIDE_TAG: Final = re.compile(r"<Override\b[^>]*>", re.IGNORECASE)
_PART_NAME: Final = re.compile(r"PartName\s*=\s*[\"']([^\"']*)[\"']", re.IGNORECASE)
_CONTENT_TYPE: Final = re.compile(r"ContentType\s*=\s*[\"']([^\"']*)[\"']", re.IGNORECASE)


@dataclass(frozen=True)
class PartInventory:
    """How many parts of each kind the package holds (sorted pairs, so it compares and prints)."""

    counts: tuple[tuple[str, int], ...] = ()

    def as_dict(self) -> dict[str, int]:
        return dict(self.counts)

    def count(self, category: str) -> int:
        return self.as_dict().get(category, 0)


@dataclass(frozen=True)
class InventoryChange:
    """A kind of part that is present in the input and absent or reduced in the output."""

    category: str
    before: int
    after: int


@dataclass(frozen=True)
class PackageScan:
    """What the scan learned; everything here is derived from the bytes, none of it from text."""

    sha256: str
    macro_enabled: bool
    inventory: PartInventory
    part_count: int
    compressed_bytes: int
    uncompressed_bytes: int
    largest_part_ratio: float


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _refuse(code: WorkbookRefusal) -> WorkbookRefusedError:
    return WorkbookRefusedError(code)


def read_capped(stream: IO[bytes], limit: int) -> bytes | None:
    """Read a stream to its end, counting the bytes as they arrive.

    Returns None as soon as more than `limit` bytes have come out of it. The count is of bytes
    actually produced, never of a size the container claims, so a zip entry that lies about its
    own size cannot get more than `limit` bytes into memory.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(CHUNK_BYTES)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)


def _open_archive(data: bytes) -> zipfile.ZipFile | None:
    try:
        return zipfile.ZipFile(BytesIO(data))
    except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError, EOFError):
        return None


def _read_part(
    archive: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int
) -> tuple[bytes | None, bool]:
    """(bytes, readable). bytes is None when the part is over the limit; readable is False when
    the zip machinery failed (bad CRC, unsupported compression, an encrypted entry)."""
    try:
        with archive.open(info) as stream:
            return read_capped(stream, limit), True
    except (
        zipfile.BadZipFile,
        zlib.error,
        RuntimeError,  # an encrypted entry
        NotImplementedError,  # a compression method the standard library does not have
        EOFError,
        OSError,
        ValueError,
    ):
        return None, False


def _head_text(head: bytes) -> str:
    """The start of a part as text, decoded the way its byte-order mark or first bytes say."""
    if head.startswith(b"\xff\xfe"):
        return head[2:].decode("utf-16-le", errors="replace")
    if head.startswith(b"\xfe\xff"):
        return head[2:].decode("utf-16-be", errors="replace")
    if head.startswith(b"\xef\xbb\xbf"):
        return head[3:].decode("utf-8", errors="replace")
    if head.startswith(b"<\x00"):
        return head.decode("utf-16-le", errors="replace")
    if head.startswith(b"\x00<"):
        return head.decode("utf-16-be", errors="replace")
    return head.decode("latin-1")


def _declared_encoding(part: bytes) -> str | None:
    match = _DECLARED_ENCODING.match(_head_text(part[:1024]))
    return match.group(1).casefold() if match else None


def _first_hit(patterns: tuple[re.Pattern[bytes], ...], part: bytes) -> bool:
    return any(pattern.search(part) for pattern in patterns)


def check_part_bytes(part: bytes) -> None:
    """Refuse a part that holds a DOCTYPE or ENTITY, a UTF-32 mark or a disallowed encoding."""
    if _first_hit(_DOCTYPE, part):
        raise _refuse("doctype")
    if _first_hit(_ENTITY, part):
        raise _refuse("entity")
    if part[:4] in UTF32_BOMS:
        raise _refuse("utf32")
    declared = _declared_encoding(part)
    if declared is not None and declared not in ALLOWED_DECLARED_ENCODINGS:
        raise _refuse("encoding_not_allowed")


def part_category(name: str) -> str | None:
    """The kind of part a name stands for, for the inventory (None: not tracked)."""
    lowered = name.casefold()
    if lowered.endswith(".rels"):
        return None
    if lowered == "xl/vbaproject.bin":
        return "vba_project"
    prefixes = (
        ("xl/charts/", "charts"),
        ("xl/drawings/", "drawings"),
        ("xl/media/", "media"),
        ("xl/comments", "comments"),
        ("xl/threadedcomments/", "comments"),
        ("xl/externallinks/", "external_links"),
        ("xl/pivottables/", "pivot_tables"),
        ("xl/pivotcache/", "pivot_tables"),
        ("xl/embeddings/", "embedded_objects"),
        ("xl/activex/", "form_controls"),
        ("xl/ctrlprops/", "form_controls"),
        ("xl/slicers/", "slicers"),
        ("xl/slicercaches/", "slicers"),
        ("xl/printersettings/", "printer_settings"),
    )
    for prefix, category in prefixes:
        if lowered.startswith(prefix):
            return category
    return None


def _is_sheet_part(name: str) -> bool:
    lowered = name.casefold()
    return lowered.startswith("xl/worksheets/") and lowered.endswith(".xml")


def _workbook_content_type(content_types: bytes) -> str | None:
    """The content type declared for /xl/workbook.xml, read with a pattern (nothing is parsed)."""
    text = _head_text(content_types)
    for tag in _OVERRIDE_TAG.findall(text):
        name = _PART_NAME.search(tag)
        if name is not None and name.group(1).casefold() == "/" + WORKBOOK_PART:
            declared = _CONTENT_TYPE.search(tag)
            return declared.group(1) if declared else None
    return None


def _macro_enabled(content_types: bytes) -> bool:
    declared = _workbook_content_type(content_types)
    if declared == WORKBOOK_CONTENT_TYPE:
        return False
    if declared == MACRO_WORKBOOK_CONTENT_TYPE:
        return True
    raise _refuse("unsupported_type")


def compare_inventories(before: PartInventory, after: PartInventory) -> tuple[InventoryChange, ...]:
    """Every kind of part the output holds fewer of than the input did (what a save dropped)."""
    changes = [
        InventoryChange(category, had, after.count(category))
        for category, had in before.counts
        if after.count(category) < had
    ]
    return tuple(changes)


def inventory_of_names(names: list[str], validation_extension_sheets: int = 0) -> PartInventory:
    """The inventory for a list of part names (plus the sheets that hold the dropdown extension)."""
    counts: dict[str, int] = {}
    for name in names:
        category = part_category(name)
        if category is not None:
            counts[category] = counts.get(category, 0) + 1
    if validation_extension_sheets:
        counts["extension_data_validations"] = validation_extension_sheets
    return PartInventory(tuple(sorted(counts.items())))


def scan_package(data: bytes, caps: Caps = DEFAULT_CAPS) -> PackageScan:
    """Run every check on the raw bytes of a workbook and return what was learned.

    Raises:
        WorkbookRefusedError: a cap was exceeded, the file is not a readable .xlsx or .xlsm
            package, or a part holds content that is refused for safety.
    """
    if len(data) > caps.max_file_bytes:
        raise _refuse("file_too_large")
    if data.startswith(OLE_HEADER):
        raise _refuse("legacy_or_encrypted")
    if not data.startswith(ZIP_MAGIC):
        raise _refuse("not_a_zip")
    archive = _open_archive(data)
    if archive is None:
        raise _refuse("unreadable")
    with archive:
        return _scan_archive(archive, data, caps)


def _scan_archive(archive: zipfile.ZipFile, data: bytes, caps: Caps) -> PackageScan:
    infos = archive.infolist()
    if len(infos) > caps.max_parts:
        raise _refuse("too_many_parts")
    names = [info.filename for info in infos]
    if len(set(names)) != len(names) or len({n.casefold() for n in names}) != len(names):
        raise _refuse("duplicate_part")
    if sum(info.file_size for info in infos) > caps.max_uncompressed_bytes:
        raise _refuse("uncompressed_too_large")  # the cheap early refusal; the count below decides

    remaining = caps.max_uncompressed_bytes
    uncompressed = 0
    largest_ratio = 0.0
    content_types: bytes | None = None
    validation_sheets = 0
    for info in infos:
        part, readable = _read_part(archive, info, remaining)
        if not readable:
            raise _refuse("unreadable")
        if part is None:
            raise _refuse("uncompressed_too_large")
        remaining -= len(part)
        uncompressed += len(part)
        ratio = len(part) / max(info.compress_size, 1)
        largest_ratio = max(largest_ratio, ratio)
        if len(part) > RATIO_FLOOR_BYTES and ratio > caps.max_ratio:
            raise _refuse("ratio_too_high")
        check_part_bytes(part)
        if info.filename == CONTENT_TYPES_PART:
            content_types = part
        if _is_sheet_part(info.filename) and _first_hit(_VALIDATION_EXTENSION, part):
            validation_sheets += 1
    if uncompressed > RATIO_FLOOR_BYTES and uncompressed / max(len(data), 1) > caps.max_ratio:
        raise _refuse("ratio_too_high")

    if WORKBOOK_PART not in names:
        raise _refuse("no_workbook_part")
    if content_types is None:
        raise _refuse("unsupported_type")
    return PackageScan(
        sha256=sha256_hex(data),
        macro_enabled=_macro_enabled(content_types),
        inventory=inventory_of_names(names, validation_sheets),
        part_count=len(names),
        compressed_bytes=len(data),
        uncompressed_bytes=uncompressed,
        largest_part_ratio=largest_ratio,
    )
