"""The demo spreadsheet end to end against a real local Ollama (marker `ollama`, live check B).

Deselected by default (`-m 'not ollama'` in pyproject). Only the owner starts it. It needs
`ollama serve` running with `nomic-embed-text` and `llama3.1:8b` installed (nothing here pulls a
model). From `sidecar/`:

    .venv/Scripts/python -m pytest -m ollama -s -rN tests/test_e2e_live.py

What it does, in order: writes the 15-question demo workbook with `tools/make_demo_xlsx.py` into a
fresh folder under your temp directory; builds the `vault-demo/vault` index with the existing
indexer and the real embedding model (about a dozen short notes); starts the real sidecar as a
child process on a loopback port with a random token; asks it over HTTP to inspect the workbook and
start a run; reads the server-sent event stream WHILE the run is going (the line "progress at"
below shows when each event arrived, so you can see it is not delivered all at the end); and then
checks the answers file against the original. The checks hold however the model answers (nothing
original changed, no new row, new columns present, a blank answer exactly on the NEEDS_REVIEW rows,
no question text in the stream, the input file's hash unchanged). The report prints each question's
status and reasons next to a note of what was expected. Those notes are not asserted: a model's
answers vary (ADR-013, 2a), and this is not the held-out evaluation.

Only the vault-demo notes are indexed (no framework packs), so the framework-text questions have no
framework text to find. After the run, open the output file in Excel and in LibreOffice (live check
B lists what to look for) and keep the folder that is printed at the top.
"""

import tempfile
import uuid
from pathlib import Path

import pytest

from .e2ekit import report_lines, run_demo

pytestmark = pytest.mark.ollama

BASE_URL = "http://127.0.0.1:11434"
WORK = Path(tempfile.gettempdir()) / "grc-live-e2e"


def test_the_demo_spreadsheet_end_to_end_with_the_real_models() -> None:
    work = WORK / uuid.uuid4().hex[:8]  # a fresh folder: exactly one output file per run
    print(f"\nwork folder: {work}", flush=True)
    run, folders, tool = run_demo(BASE_URL, work, live=True)
    print("\n".join(report_lines(run, folders, tool)))
    print(f"\noutput: {run.output}")
    print("event arrival (seconds after the stream opened):")
    print(" ".join(f"{t:.1f}" for t in run.frame_times))
    assert not run.problems, "\n".join(run.problems)
