"""Generate the demo questionnaire spreadsheet for ALIBI (M5l).

Development-only. It needs the sidecar's pinned `openpyxl` (run it with the sidecar virtualenv) and
nothing else: no network, no model. The 15 questions are our own wording, written against the
synthetic `vault-demo/` vault (the fictional "Marlowe Quay Analytics, Ltd."), not copied from SIG,
CAIQ or any other questionnaire. They are synthetic demo data like everything else in the repo.

The workbook has the features the engine must leave alone: a header row, merged and bold section
banner rows (skipped as section headers), a drop-down list on the response column, column widths,
and a second sheet that is not a questionnaire. The response column is left empty on purpose: the
engine writes to new columns on the right and never touches an existing cell.

The expectation notes below are for the person reading the live run's report. They are NOT written
into the workbook, and no test treats them as a promise: a model's answers vary (ADR-013, 2a).

Usage (from the repo root, writing into the gitignored `fixtures-out/` folder):

    sidecar/.venv/Scripts/python -I tools/make_demo_xlsx.py

It refuses to overwrite a file that already exists.
"""

import argparse
import sys
from io import BytesIO
from pathlib import Path
from typing import Final, NamedTuple

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.worksheet.datavalidation import DataValidation

DEFAULT_OUT: Final = Path(__file__).resolve().parents[1] / "fixtures-out"
FILE_NAME: Final = "demo-questionnaire.xlsx"
SHEET_NAME: Final = "Questions"
HEADERS: Final = ("Ref", "Question", "Vendor response")
RESPONSE_CHOICES: Final = '"Yes,No,Partial,Not applicable"'


class DemoQuestion(NamedTuple):
    ref: str
    question: str
    expectation: str  # "answer", "partial: <why>" or "blank: <why>"; a note for the reader only


# (section title, questions). Every question is original wording.
DEMO_SECTIONS: Final[tuple[tuple[str, tuple[DemoQuestion, ...]], ...]] = (
    (
        "Section 1: Access to systems",
        (
            DemoQuestion(
                "D-01",
                "Must staff use a second sign-in factor when they work from outside the office?",
                "answer",
            ),
            DemoQuestion(
                "D-02",
                "Which kind of one-time code is not accepted as a second factor?",
                "answer",
            ),
            DemoQuestion(
                "D-03",
                "How frequently is every person's system access checked again?",
                "answer",
            ),
            DemoQuestion(
                "D-04",
                "How soon is a leaver's access taken away?",
                "answer",
            ),
        ),
    ),
    (
        "Section 2: Data and recovery",
        (
            DemoQuestion(
                "D-05",
                "How long after a customer leaves does their data stay in production?",
                "answer",
            ),
            DemoQuestion(
                "D-06",
                "For how long are security logs kept?",
                "blank: two notes disagree (90 vs 365 days)",
            ),
            DemoQuestion(
                "D-07",
                "How much recent data could be lost after a disaster, measured in time?",
                "answer",
            ),
            DemoQuestion(
                "D-08",
                "Which cloud provider runs your production servers?",
                "blank: absent from the vault",
            ),
        ),
    ),
    (
        "Section 3: Operations and suppliers",
        (
            DemoQuestion(
                "D-09",
                "How soon must the most serious software flaws be repaired?",
                "answer",
            ),
            DemoQuestion(
                "D-10",
                "How often must employees repeat security awareness training?",
                "answer",
            ),
            DemoQuestion(
                "D-11",
                "How often are your most important suppliers formally reviewed?",
                "partial: the vendor policy's review date is overdue",
            ),
            DemoQuestion(
                "D-12",
                "How quickly are customers told when their data is involved in an incident?",
                "answer",
            ),
            DemoQuestion(
                "D-13",
                "Does an outside firm test your systems for weaknesses every year?",
                "blank: only framework text, no company statement",
            ),
            DemoQuestion(
                "D-14",
                "Do you hold an ISO 27001 certificate?",
                "blank: absent from the vault",
            ),
            DemoQuestion(
                "D-15",
                "Describe how you govern the use of machine-learning models in your products.",
                "blank: absent from the vault",
            ),
        ),
    ),
)

DEMO_QUESTIONS: Final[tuple[DemoQuestion, ...]] = tuple(
    q for _title, questions in DEMO_SECTIONS for q in questions
)


def build_workbook() -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    if sheet is None:  # a new workbook always has an active sheet; this satisfies the type checker
        raise RuntimeError("no active sheet")
    sheet.title = SHEET_NAME
    sheet.append(list(HEADERS))
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    validation = DataValidation(type="list", formula1=RESPONSE_CHOICES, allow_blank=True)
    sheet.add_data_validation(validation)
    for title, questions in DEMO_SECTIONS:
        sheet.append([None, title])  # the title sits in the question column, merged across B:C
        banner = sheet.max_row
        sheet.merge_cells(start_row=banner, start_column=2, end_row=banner, end_column=3)
        sheet.cell(row=banner, column=2).font = Font(bold=True)
        for item in questions:
            sheet.append([item.ref, item.question])
            row = sheet.max_row
            sheet.cell(row=row, column=2).alignment = Alignment(wrap_text=True, vertical="top")
            validation.add(sheet.cell(row=row, column=3))
    sheet.column_dimensions["A"].width = 8
    sheet.column_dimensions["B"].width = 70
    sheet.column_dimensions["C"].width = 18
    about = workbook.create_sheet("About")
    about.append(["SYNTHETIC DEMO DATA. Fictional company; not a real questionnaire."])
    return workbook


def workbook_bytes() -> bytes:
    buffer = BytesIO()
    build_workbook().save(buffer)
    return buffer.getvalue()


def write_demo(out_dir: Path) -> Path:
    """Write the workbook into `out_dir` (created if needed); never overwrite an existing file."""
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / FILE_NAME
    with target.open("xb") as handle:  # exclusive create: FileExistsError if it is there
        handle.write(workbook_bytes())
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="folder to write into")
    args = parser.parse_args(argv)
    try:
        target = write_demo(args.out)
    except FileExistsError:
        print(f"refusing to overwrite an existing {FILE_NAME} in {args.out}", file=sys.stderr)
        return 1
    print(f"wrote {target} ({len(DEMO_QUESTIONS)} questions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
