"""Workbook fixtures built by code (ADR-014). No binary file is committed.

`simple_workbook` makes a small questionnaire; `rebuild` rewrites a saved package with parts added,
replaced or dropped, which is how the tests make packages that openpyxl itself would never save
(a part with a DOCTYPE, a binary macro part, a different content type).
"""

import zipfile
from collections.abc import Iterable, Mapping
from io import BytesIO

from openpyxl import Workbook

WORKBOOK_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
MACRO_TYPE = "application/vnd.ms-excel.sheet.macroEnabled.main+xml"


def to_bytes(workbook: Workbook) -> bytes:
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def simple_workbook(
    rows: Iterable[tuple[object, ...]],
    headers: tuple[str, ...] = ("ID", "Question"),
    title: str = "Questions",
) -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = title
    sheet.append(list(headers))
    for row in rows:
        sheet.append(list(row))
    return workbook


def simple_bytes(count: int = 3) -> bytes:
    rows = [(f"Q-{n}", f"Do you encrypt data at rest, question {n}?") for n in range(1, count + 1)]
    return to_bytes(simple_workbook(rows))


def part_names(data: bytes) -> list[str]:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        return archive.namelist()


def read_part(data: bytes, name: str) -> bytes:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        return archive.read(name)


def rebuild(
    data: bytes,
    *,
    replace: Mapping[str, bytes] | None = None,
    add: Mapping[str, bytes] | None = None,
    drop: Iterable[str] = (),
) -> bytes:
    """The same package with some parts replaced, added or dropped."""
    replace = replace or {}
    dropped = set(drop)
    out = BytesIO()
    with (
        zipfile.ZipFile(BytesIO(data)) as source,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for info in source.infolist():
            if info.filename in dropped:
                continue
            target.writestr(info.filename, replace.get(info.filename, source.read(info)))
        for name, content in (add or {}).items():
            target.writestr(name, content)
    return out.getvalue()


def with_content_type(data: bytes, new_type: str) -> bytes:
    """The package with the workbook part declared as a different content type."""
    types = read_part(data, "[Content_Types].xml").decode("utf-8")
    assert WORKBOOK_TYPE in types
    return rebuild(
        data, replace={"[Content_Types].xml": types.replace(WORKBOOK_TYPE, new_type).encode()}
    )


def with_sheet_text(data: bytes, new_sheet: bytes) -> bytes:
    return rebuild(data, replace={"xl/worksheets/sheet1.xml": new_sheet})
