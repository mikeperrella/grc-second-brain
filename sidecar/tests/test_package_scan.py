"""The package scan refuses what must not reach the parser (ADR-014, threat model F22).

Workbooks are built by code (`xlsxkit`). A control is only proven by a test that fails without it,
so each refusal has a case that nothing else would refuse, and the scan has cases it must accept
(binary parts, macro packages) so that refusing too much is a failing test too.
"""

import os
import pyexpat
import struct
import warnings
import zipfile
from io import BytesIO

import pytest

from grc_sidecar.errors import WorkbookRefusedError
from grc_sidecar.questionnaire import package
from grc_sidecar.questionnaire.package import (
    CHUNK_BYTES,
    DEFAULT_CAPS,
    Caps,
    PartInventory,
    compare_inventories,
    inventory_of_names,
    read_capped,
    scan_package,
)

from .xlsxkit import (
    MACRO_TYPE,
    rebuild,
    simple_bytes,
    with_content_type,
)

BOMB = (
    '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]><lolz>&b;</lolz>'
)
SHEET = "xl/worksheets/sheet1.xml"


def refusal(data: bytes, caps: Caps = DEFAULT_CAPS) -> str:
    with pytest.raises(WorkbookRefusedError) as caught:
        scan_package(data, caps)
    return caught.value.code


def with_part(name: str, content: bytes) -> bytes:
    return rebuild(simple_bytes(), add={name: content})


def with_sheet(content: bytes) -> bytes:
    return rebuild(simple_bytes(), replace={SHEET: content})


# ----- accepting a normal workbook ----------------------------------------------------------


def test_a_plain_workbook_is_accepted_and_hashed() -> None:
    import hashlib

    data = simple_bytes()
    scan = scan_package(data)
    assert scan.sha256 == hashlib.sha256(data).hexdigest()
    assert scan.macro_enabled is False
    assert scan.inventory == PartInventory()
    assert scan.compressed_bytes == len(data)
    assert scan.part_count == len(zipfile.ZipFile(BytesIO(data)).namelist())


def test_binary_parts_are_accepted_not_refused_for_holding_nul_bytes() -> None:
    # An image and a macro project are binary. A control that refused NUL bytes would refuse every
    # workbook with a picture or a macro, so this must pass.
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64 + os.urandom(512)
    vba = package.OLE_HEADER + b"\x00" * 512 + os.urandom(512)
    data = rebuild(simple_bytes(), add={"xl/media/image1.png": png, "xl/vbaProject.bin": vba})
    scan = scan_package(data)
    assert scan.inventory.as_dict() == {"media": 1, "vba_project": 1}


def test_a_large_incompressible_part_is_accepted() -> None:
    noise = os.urandom(1_500_000)  # incompressible, so its ratio is about 1
    assert scan_package(with_part("xl/media/photo.bin", noise)).uncompressed_bytes > 1_500_000


# ----- what is not a workbook ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"", "not_a_zip"),
        (b"just text", "not_a_zip"),
        (package.OLE_HEADER + b"\x00" * 600, "legacy_or_encrypted"),
        (b"PK\x03\x04" + b"\x00" * 40, "unreadable"),
    ],
)
def test_files_that_are_not_xlsx_packages_are_refused(data: bytes, code: str) -> None:  # [NEG]
    assert refusal(data) == code


def test_a_package_without_the_workbook_part_is_refused() -> None:
    assert refusal(rebuild(simple_bytes(), drop=["xl/workbook.xml"])) == "no_workbook_part"


def test_a_package_without_content_types_is_refused() -> None:
    assert refusal(rebuild(simple_bytes(), drop=["[Content_Types].xml"])) == "unsupported_type"


def test_a_template_content_type_is_refused() -> None:
    template = "application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml"
    assert refusal(with_content_type(simple_bytes(), template)) == "unsupported_type"


def test_the_macro_decision_comes_from_the_content_type_not_the_extension() -> None:
    plain = scan_package(simple_bytes())
    macro = scan_package(with_content_type(simple_bytes(), MACRO_TYPE))
    assert (plain.macro_enabled, macro.macro_enabled) == (False, True)


def test_an_encrypted_zip_entry_is_unreadable() -> None:  # [NEG]
    # Flag bit 0 of a zip entry means "encrypted". The standard library cannot read it.
    data = bytearray(simple_bytes())
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        position = data.find(signature)
        while position != -1:
            data[position + offset] |= 0x01
            position = data.find(signature, position + 4)
    assert refusal(bytes(data)) == "unreadable"


# ----- caps ----------------------------------------------------------------------------------


def test_the_file_size_cap() -> None:  # [NEG]
    data = simple_bytes()
    assert refusal(data, Caps(max_file_bytes=len(data) - 1)) == "file_too_large"
    assert scan_package(data, Caps(max_file_bytes=len(data))).sha256


def test_the_part_count_cap() -> None:  # [NEG]
    data = simple_bytes()
    count = len(zipfile.ZipFile(BytesIO(data)).namelist())
    assert refusal(data, Caps(max_parts=count - 1)) == "too_many_parts"
    assert scan_package(data, Caps(max_parts=count)).part_count == count


def test_the_uncompressed_size_cap_counts_every_part() -> None:  # [NEG]
    data = with_part("xl/media/a.bin", os.urandom(200_000))
    assert refusal(data, Caps(max_uncompressed_bytes=100_000)) == "uncompressed_too_large"
    assert scan_package(data, Caps(max_uncompressed_bytes=10_000_000)).sha256


def test_the_part_count_cap_applies_to_tiny_parts() -> None:  # [NEG]
    # Every part is a few bytes, far below the 1 MiB ratio floor, and the count still refuses.
    many = {f"xl/media/tiny{n}.bin": b"x" for n in range(DEFAULT_CAPS.max_parts + 1)}
    assert refusal(rebuild(simple_bytes(), add=many)) == "too_many_parts"


def test_small_parts_cannot_add_up_past_the_total_cap() -> None:  # [NEG]
    # Twenty incompressible 100 kB parts: each is far below the ratio floor and has a ratio of
    # about 1, so only the absolute total can refuse them.
    parts = {f"xl/media/part{n}.bin": os.urandom(100_000) for n in range(20)}
    data = rebuild(simple_bytes(), add=parts)
    assert refusal(data, Caps(max_uncompressed_bytes=1_000_000)) == "uncompressed_too_large"


class LyingArchive:
    """What a zip directory could say if the standard library did not police it: every part
    claims to be 10 bytes, and each really yields 400 kB. The running total is the control that
    counts what actually came out."""

    def __init__(self, parts: int) -> None:
        self._infos = []
        for number in range(parts):
            info = zipfile.ZipInfo(f"xl/media/lie{number}.bin")
            info.file_size = 10
            info.compress_size = 400_000  # a ratio of 1, so the ratio checks stay quiet
            self._infos.append(info)

    def infolist(self) -> list[zipfile.ZipInfo]:
        return self._infos

    def open(self, info: zipfile.ZipInfo) -> BytesIO:
        return BytesIO(os.urandom(400_000))


def test_the_running_total_counts_bytes_produced_not_sizes_claimed() -> None:  # [NEG]
    # Five parts of 400 kB each (2 MB) against a 1 MB total: no part is over the cap on its own,
    # none is over the ratio floor, and the claimed sizes (50 bytes) say nothing is wrong.
    caps = Caps(max_uncompressed_bytes=1_000_000)
    with pytest.raises(WorkbookRefusedError) as caught:
        package._scan_archive(LyingArchive(5), b"x", caps)  # type: ignore[arg-type]
    assert caught.value.code == "uncompressed_too_large"


def test_a_zip_bomb_is_refused_by_ratio() -> None:  # [NEG]
    bomb = with_part("xl/media/bomb.bin", b"\x00" * (3 * 1024 * 1024))
    assert refusal(bomb) == "ratio_too_high"


def test_many_parts_that_are_each_under_the_floor_are_refused_by_the_overall_ratio() -> (
    None
):  # [NEG]
    # Each part is below the per-part floor, so only the overall ratio can refuse this one.
    parts = {f"xl/media/part{n}.bin": b"\x00" * 400_000 for n in range(5)}
    assert refusal(rebuild(simple_bytes(), add=parts)) == "ratio_too_high"


def test_one_extreme_part_is_refused_even_when_the_whole_file_looks_ordinary() -> None:  # [NEG]
    # Incompressible data keeps the overall ratio near 3, so only the per-part ratio can refuse.
    parts = {
        "xl/media/noise.bin": os.urandom(1_000_000),
        "xl/media/bomb.bin": b"\x00" * (2 * 1024 * 1024),
    }
    assert refusal(rebuild(simple_bytes(), add=parts)) == "ratio_too_high"


def test_a_part_that_lies_about_its_size_is_not_trusted() -> None:  # [NEG]
    # The directory claims every part is 10 bytes. The standard library stops at the claimed size
    # and then fails the checksum, which this reports as unreadable, not as a pass.
    buffer = bytearray(with_part("xl/media/big.bin", os.urandom(50_000)))
    for signature, offset in ((b"PK\x03\x04", 22), (b"PK\x01\x02", 24)):
        position = buffer.find(signature)
        while position != -1:
            struct.pack_into("<I", buffer, position + offset, 10)
            position = buffer.find(signature, position + 4)
    assert refusal(bytes(buffer), Caps(max_uncompressed_bytes=1_000)) == "unreadable"


class EndlessStream:
    """A stream with no end, to show the counting stops it."""

    def __init__(self) -> None:
        self.served = 0

    def read(self, size: int = -1) -> bytes:
        self.served += size
        if self.served > 5_000_000:  # a counting control stops long before this
            raise AssertionError("the count never stopped the stream")
        return b"x" * size


def test_the_count_is_of_bytes_produced_and_stops_an_endless_stream() -> None:  # [NEG]
    stream = EndlessStream()
    assert read_capped(stream, 1_000) is None  # type: ignore[arg-type]
    assert stream.served <= 1_000 + CHUNK_BYTES


def test_read_capped_returns_the_bytes_when_they_fit() -> None:
    assert read_capped(BytesIO(b"abc" * 10), 30) == b"abc" * 10
    assert read_capped(BytesIO(b"abc" * 10), 29) is None


def test_duplicate_part_names_are_refused() -> None:  # [NEG]
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", b"<a/>")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # the standard library warns about the duplicate
            archive.writestr("xl/workbook.xml", b"<b/>")
    assert refusal(buffer.getvalue()) == "duplicate_part"


def test_names_that_differ_only_in_case_are_refused() -> None:  # [NEG]
    data = rebuild(simple_bytes(), add={"XL/Workbook.xml": b"<a/>"})
    assert refusal(data) == "duplicate_part"


# ----- DOCTYPE and ENTITY, in every layout the parser can read -------------------------------

LAYOUTS = {
    "utf8": BOMB.encode("utf-8"),
    "utf8-lowercase": BOMB.replace("DOCTYPE", "doctype").replace("ENTITY", "entity").encode(),
    "utf16le": BOMB.encode("utf-16-le"),
    "utf16le-bom": b"\xff\xfe" + BOMB.encode("utf-16-le"),
    "utf16be": BOMB.encode("utf-16-be"),
    "utf16be-bom": b"\xfe\xff" + BOMB.encode("utf-16-be"),
}


@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_a_doctype_is_refused_in_every_layout(layout: str) -> None:  # [NEG]
    assert refusal(with_sheet(LAYOUTS[layout])) == "doctype"


def test_utf16le_billion_laughs_part_refused() -> None:  # [NEG]
    """A UTF-16 text is its other-endian twin shifted by one byte, so inside a longer part either
    pattern finds either layout. Only at the edges does one pattern fail to see the other layout:
    little-endian text that starts the part has no byte before its `<`, so the big-endian pattern
    (which needs a NUL there) cannot match. A control that kept only the big-endian pattern would
    be blind to this bomb, which starts with its DOCTYPE.

    A small bomb, so a broken control cannot hang the suite.
    """
    bomb = BOMB[len('<?xml version="1.0"?>') :]
    assert bomb.startswith("<!DOCTYPE")
    assert refusal(with_sheet(bomb.encode("utf-16-le"))) == "doctype"


def test_utf16be_billion_laughs_part_refused() -> None:  # [NEG]
    """Big-endian text that ends the part has no byte after its last letter, so the little-endian
    pattern (which needs a NUL there) cannot match; only the big-endian pattern refuses it."""
    assert refusal(with_sheet("x<!DOCTYPE".encode("utf-16-be"))) == "doctype"
    assert refusal(with_sheet(BOMB.encode("utf-16-be"))) == "doctype"


def test_each_utf16_entity_pattern_is_needed_on_its_own() -> None:  # [NEG]
    assert refusal(with_sheet("<!ENTITY a>".encode("utf-16-le"))) == "entity"
    assert refusal(with_sheet("x<!ENTITY".encode("utf-16-be"))) == "entity"


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16-le", "utf-16-be"])
def test_an_entity_declaration_alone_is_refused(encoding: str) -> None:  # [NEG]
    part = '<?xml version="1.0"?><!ENTITY x "y"><a/>'.encode(encoding)
    assert refusal(with_sheet(part)) == "entity"


def test_a_doctype_is_refused_in_a_part_of_any_type() -> None:  # [NEG]
    # Not only XML parts: a "picture" holding the literal text is refused as well.
    assert refusal(with_part("xl/media/image1.png", b"\x89PNG\x00<!doctype x>")) == "doctype"


def test_a_doctype_in_the_content_types_part_is_refused() -> None:  # [NEG]
    types = b'<?xml version="1.0"?><!DOCTYPE t []><Types/>'
    assert refusal(rebuild(simple_bytes(), replace={"[Content_Types].xml": types})) == "doctype"


CANDIDATE_CODECS = [
    ("utf-8", "UTF-8"),
    ("utf-8-sig", None),
    ("utf-16-le", None),
    ("utf-16-be", None),
    ("utf-16", "UTF-16"),
    ("iso-8859-1", "ISO-8859-1"),
    ("ascii", "US-ASCII"),
    ("cp1252", "windows-1252"),
    ("koi8-r", "KOI8-R"),
    ("latin-1", "latin-1"),
    ("utf-32", "UTF-32"),
    ("cp037", "ebcdic-cp-us"),
    ("cp500", "cp500"),
    ("shift_jis", "Shift_JIS"),
    ("gb2312", "gb2312"),
    ("utf-7", "UTF-7"),
]


def _declaration(name: str | None) -> str:
    return f'<?xml version="1.0" encoding="{name}"?>' if name else '<?xml version="1.0"?>'


def _expat_parses(raw: bytes) -> bool:
    try:
        pyexpat.ParserCreate().Parse(raw, True)
    except (pyexpat.ExpatError, ValueError):  # ValueError: "multi-byte encodings are not supported"
        return False
    return True


def test_every_encoding_the_local_parser_reads_is_scanned() -> None:  # [NEG]
    """For each codec this build of expat accepts, a DOCTYPE document in it is refused.

    The set of codecs expat reads depends on the build (Windows and the Linux test container may
    differ), so the test asks the parser instead of assuming a list.
    """
    parsed: list[str] = []
    missed: list[str] = []
    for codec, declared in CANDIDATE_CODECS:
        benign = (_declaration(declared) + "<a>x</a>").encode(codec)
        if not _expat_parses(benign):
            continue
        parsed.append(codec)
        hostile = (_declaration(declared) + BOMB[len('<?xml version="1.0"?>') :]).encode(codec)
        try:
            scan_package(with_sheet(hostile))
        except WorkbookRefusedError:
            continue
        missed.append(codec)
    assert {"utf-8", "utf-16-le", "utf-16-be"} <= set(parsed)  # the test is not vacuous
    assert missed == []


# ----- UTF-32 and declared encodings ---------------------------------------------------------


@pytest.mark.parametrize("bom", package.UTF32_BOMS)
def test_a_utf32_byte_order_mark_is_refused(bom: bytes) -> None:  # [NEG]
    assert refusal(with_sheet(bom + b"<\x00\x00\x00a\x00\x00\x00>\x00\x00\x00")) == "utf32"


@pytest.mark.parametrize("name", ["koi8-r", "Shift_JIS", "utf-32", "ebcdic-cp-us", "UTF-7"])
def test_a_declared_encoding_outside_the_list_is_refused(name: str) -> None:  # [NEG]
    part = f'<?xml version="1.0" encoding="{name}"?><a/>'.encode("ascii")
    assert refusal(with_sheet(part)) == "encoding_not_allowed"


def test_a_declared_encoding_is_read_through_utf16_too() -> None:  # [NEG]
    part = '<?xml version="1.0" encoding="koi8-r"?><a/>'.encode("utf-16-le")
    assert refusal(with_sheet(part)) == "encoding_not_allowed"


@pytest.mark.parametrize(
    "name", ["UTF-8", "utf-8", "ISO-8859-1", "windows-1252", "us-ascii", "latin-1"]
)
def test_the_listed_encodings_are_accepted(name: str) -> None:
    part = f'<?xml version="1.0" encoding="{name}"?><a/>'.encode("ascii")
    assert scan_package(with_sheet(part)).sha256


# ----- the inventory -------------------------------------------------------------------------


def test_the_inventory_counts_what_a_save_could_lose() -> None:
    names = [
        "xl/charts/chart1.xml",
        "xl/charts/chart2.xml",
        "xl/charts/_rels/chart1.xml.rels",
        "xl/drawings/drawing1.xml",
        "xl/media/image1.png",
        "xl/comments1.xml",
        "xl/externalLinks/externalLink1.xml",
        "xl/vbaProject.bin",
        "xl/slicers/slicer1.xml",
        "xl/worksheets/sheet1.xml",
    ]
    assert inventory_of_names(names, validation_extension_sheets=2).as_dict() == {
        "charts": 2,
        "drawings": 1,
        "media": 1,
        "comments": 1,
        "external_links": 1,
        "vba_project": 1,
        "slicers": 1,
        "extension_data_validations": 2,
    }


def test_the_dropdown_extension_is_detected_in_every_layout() -> None:
    uri = package.DATA_VALIDATION_EXTENSION_URI
    for encoding in ("utf-8", "utf-16-le", "utf-16-be"):
        sheet = f'<worksheet><extLst><ext uri="{uri}"/></extLst></worksheet>'.encode(encoding)
        assert scan_package(with_sheet(sheet)).inventory.count("extension_data_validations") == 1
    assert scan_package(simple_bytes()).inventory.count("extension_data_validations") == 0


def test_a_dropped_chart_is_reported() -> None:  # [NEG]
    before = inventory_of_names(["xl/charts/chart1.xml", "xl/media/a.png"])
    after = inventory_of_names(["xl/media/a.png"])
    changes = compare_inventories(before, after)
    assert [(c.category, c.before, c.after) for c in changes] == [("charts", 1, 0)]


def test_nothing_is_reported_when_nothing_was_lost() -> None:
    before = inventory_of_names(["xl/charts/chart1.xml"])
    assert compare_inventories(before, before) == ()
    assert compare_inventories(PartInventory(), before) == ()  # a gain is not a loss
