"""Reading a questionnaire workbook (CLAUDE.md 8.4, ADR-014). The only module that calls openpyxl's
`load_workbook` (a Semgrep rule and a test enforce that).

The order matters and is the point of the module:

1. `read_input_bytes` resolves the path inside the approved questionnaire root, reads the file
   **once** (never more than the size cap plus one byte), and returns the bytes.
2. `package.scan_package` hashes those bytes and refuses what must not reach the parser.
3. `load_questionnaire` hands the *same bytes* to openpyxl through `BytesIO`, so the file that was
   hashed and scanned is the file that is parsed, even if the file on disk changes in between.

Parser failures become `WorkbookRefusedError("unreadable")` raised outside the `except` block, so
no parser message (which can quote file content) is chained to it.
"""

import warnings
import zipfile
import zlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.workbook.workbook import Workbook

from grc_sidecar.errors import WorkbookRefusedError
from grc_sidecar.questionnaire.package import DEFAULT_CAPS, Caps, PackageScan, scan_package
from grc_sidecar.security.paths import resolve_within

MAX_WARNING_CHARS = 200


@dataclass(frozen=True)
class LoadedWorkbook:
    """A workbook that passed the package scan and was parsed from the scanned bytes.

    `warnings` are openpyxl's own notices about things it could not keep (for example "Data
    Validation extension is not supported and will be removed"). They describe features, not
    cell content, and a run reports them to the user.
    """

    workbook: Workbook
    scan: PackageScan
    warnings: tuple[str, ...]

    def close(self) -> None:
        """Close what the workbook holds open. Call it when the workbook is no longer needed.

        For a macro-enabled package openpyxl keeps the project in an in-memory zip file inside the
        workbook; left to the garbage collector it can fail while closing, after the memory it
        writes to has already been released. Closing it explicitly avoids that.
        """
        archive = self.workbook.vba_archive
        if archive is not None:
            archive.close()
        self.workbook.close()


def read_input_bytes(candidate: object, root: Path, caps: Caps = DEFAULT_CAPS) -> bytes:
    """Read a questionnaire file from inside `root`, once, refusing anything over the size cap.

    Raises:
        PathNotAllowedError, PathNotFoundError, ConfigurationError: from the path guard.
        WorkbookRefusedError: not a regular file, unreadable, or larger than the size cap.
    """
    path = resolve_within(candidate, root)
    data = _read_file(path, caps.max_file_bytes)
    if data is None:
        raise WorkbookRefusedError("unreadable")
    if len(data) > caps.max_file_bytes:
        raise WorkbookRefusedError("file_too_large")
    return data


def _read_file(path: Path, limit: int) -> bytes | None:
    try:
        if not path.is_file():
            return None
        with open(path, "rb") as handle:
            return handle.read(limit + 1)  # one byte more than the cap, to tell "over" from "at"
    except OSError:
        return None


def _parse(data: bytes, keep_vba: bool) -> tuple[Workbook | None, tuple[str, ...]]:
    """Parse with openpyxl, collecting its warnings instead of printing them.

    `catch_warnings` is process-wide, not per-thread. A questionnaire run is the only caller and
    runs one at a time (ADR-014), so nothing else is reading warnings meanwhile.
    """
    workbook: Workbook | None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            workbook = load_workbook(
                BytesIO(data), read_only=False, data_only=False, keep_vba=keep_vba
            )
        except (
            InvalidFileException,
            KeyError,
            ValueError,  # includes UnicodeError
            TypeError,
            AttributeError,
            IndexError,
            OSError,
            zipfile.BadZipFile,
            zlib.error,
            SyntaxError,  # the standard library's XML ParseError derives from it
            RuntimeError,
            OverflowError,
        ):
            workbook = None
    notices = tuple(sorted({str(item.message)[:MAX_WARNING_CHARS] for item in caught}))
    return workbook, notices


def load_questionnaire(data: bytes, caps: Caps = DEFAULT_CAPS) -> LoadedWorkbook:
    """Scan the bytes, then parse those same bytes.

    Raises:
        WorkbookRefusedError: the package scan refused it, or the parser could not read it.
    """
    scan = scan_package(data, caps)
    workbook, notices = _parse(data, keep_vba=scan.macro_enabled)
    if workbook is None:
        raise WorkbookRefusedError("unreadable")
    return LoadedWorkbook(workbook=workbook, scan=scan, warnings=notices)
