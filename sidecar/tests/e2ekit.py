"""The end-to-end demo run: a real sidecar process driven over real HTTP (M5l, ADR-014).

Used twice, with the same code. `test_e2e_wiring.py` runs it offline against a small fake Ollama on
an unused loopback port. `test_e2e_live.py` (marker `ollama`, started only by the owner) runs it
against the real Ollama. The plumbing is the same, so a wiring mistake shows up offline.

What is real: the `python -m grc_sidecar` child process with the auth token in its environment, the
startup handshake, the HTTP routes, the server-sent event stream read by a real client while the
run is going, the retriever and the index built by the existing indexer, the writer and the file on
disk. What is not: the fake Ollama (offline) says no evidence to everything.

Nothing here can reach port 11434 unless `live=True` is passed, and only the live test passes it.
"""

import asyncio
import hashlib
import importlib.util
import json
import queue
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from openpyxl import load_workbook

from grc_sidecar.llm.ollama_embeddings import OllamaEmbeddingClient
from grc_sidecar.questionnaire.writer import EXPORT_HEADERS
from grc_sidecar.repositories.sqlite_vec_repo import SqliteVecRepository
from grc_sidecar.security.net import create_async_client
from grc_sidecar.vault.indexer import Indexer

from .childproc import StdoutCollector, minimal_env, running_python
from .vaultkit import DEMO_VAULT, REPO_ROOT

EMBEDDING = "nomic-embed-text"
REAL_OLLAMA_PORT = 11434
READY_TIMEOUT_SECONDS = 90
STATUSES = ("SUPPORTED", "PARTIAL", "NEEDS_REVIEW")
TOKEN_HEADER = "X-GRC-Auth"


def load_demo_tool() -> ModuleType:
    """`tools/make_demo_xlsx.py` as a module (tools/ is not a package)."""
    path = REPO_ROOT / "tools" / "make_demo_xlsx.py"
    spec = importlib.util.spec_from_file_location("make_demo_xlsx", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _refuse_real_ollama_unless_live(base_url: str, live: bool) -> None:
    if urlsplit(base_url).port == REAL_OLLAMA_PORT and not live:
        pytest.fail("an offline test was pointed at port 11434 (the owner's Ollama)")


async def build_demo_index(base_url: str, index_dir: Path, *, live: bool) -> int:
    """Index `vault-demo/vault` with the existing indexer, as live check A did. Returns chunks."""
    _refuse_real_ollama_unless_live(base_url, live)
    index_dir.mkdir(parents=True, exist_ok=True)
    embed = OllamaEmbeddingClient(base_url, EMBEDDING, expected_dimension=768)
    repo = SqliteVecRepository(index_dir / "vault.sqlite3")
    try:
        report = await Indexer(repo, embed, DEMO_VAULT).sync()
        assert report.complete
        return report.chunks_embedded
    finally:
        await repo.close()
        await embed.aclose()


@dataclass(frozen=True)
class Folders:
    work: Path
    questionnaires: Path
    exports: Path
    index: Path
    logs: Path

    @staticmethod
    def under(work: Path) -> "Folders":
        folders = Folders(
            work, work / "questionnaires", work / "exports", work / "index", work / "logs"
        )
        for folder in (folders.questionnaires, folders.exports, folders.index, folders.logs):
            folder.mkdir(parents=True, exist_ok=True)
        return folders


@contextmanager
def running_sidecar(folders: Folders, base_url: str, token: str) -> Iterator[int]:
    """Start the real sidecar on an OS-assigned loopback port; yield the port it announced."""
    stderr_path = folders.work / "sidecar-stderr.txt"
    env = minimal_env(
        {
            "GRC_AUTH_TOKEN": token,
            "GRC_LOG_DIR": str(folders.logs),
            "GRC_VAULT_DIR": str(DEMO_VAULT),
            "GRC_INDEX_DIR": str(folders.index),
            "GRC_QUESTIONNAIRE_DIR": str(folders.questionnaires),
            "GRC_EXPORT_DIR": str(folders.exports),
            "GRC_OLLAMA_BASE_URL": base_url,
        }
    )
    with running_python(
        ["-m", "grc_sidecar"], env=env, cwd=folders.work, stderr_path=stderr_path
    ) as (_proc, out):
        yield _read_port(out, stderr_path)


def _read_port(out: StdoutCollector, stderr_path: Path) -> int:
    try:
        line = out.next_line(timeout=READY_TIMEOUT_SECONDS)
    except queue.Empty:
        pytest.fail(f"no ready line; stderr: {stderr_path.read_text(encoding='utf-8')}")
    payload = json.loads(line)
    assert payload["event"] == "ready"
    port = payload["port"]
    assert isinstance(port, int)
    return port


@dataclass
class DemoRun:
    inspect: dict[str, Any]
    run_id: str
    started: dict[str, Any]
    frames: list[tuple[str, dict[str, Any]]]
    frame_times: list[float]  # seconds since the stream was opened, one per frame
    stream_text: str
    status: dict[str, Any]
    input_sha_before: str
    input_sha_after: str
    output: Path | None = None
    problems: list[str] = field(default_factory=list)


async def _read_events(
    client: httpx.AsyncClient, run_id: str, headers: dict[str, str]
) -> tuple[list[tuple[str, dict[str, Any]]], list[float], str]:
    """Read the event stream line by line as it arrives (not after the run has ended)."""
    frames: list[tuple[str, dict[str, Any]]] = []
    times: list[float] = []
    raw: list[str] = []
    event: str | None = None
    data: dict[str, Any] | None = None
    opened = time.monotonic()
    async with client.stream("GET", f"/questionnaire/runs/{run_id}/events", headers=headers) as r:
        assert r.status_code == 200
        async for line in r.aiter_lines():
            raw.append(line)
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = json.loads(line.removeprefix("data: "))
            elif line == "" and event is not None and data is not None:
                frames.append((event, data))
                times.append(time.monotonic() - opened)
                event, data = None, None
    return frames, times, "\n".join(raw)


async def drive_demo_run(port: int, token: str, folders: Folders, file_name: str) -> DemoRun:
    """inspect -> start -> read the stream to its end -> status. Over HTTP, with the token."""
    headers = {TOKEN_HEADER: token}
    source = folders.questionnaires / file_name
    before = sha256_of(source)
    timeout = httpx.Timeout(connect=10, read=3600, write=30, pool=10)  # a live run is slow
    async with create_async_client(f"http://127.0.0.1:{port}", timeout=timeout) as client:
        inspected = await client.post(
            "/questionnaire/inspect", json={"file": file_name}, headers=headers
        )
        assert inspected.status_code == 200, inspected.text
        suggestion = inspected.json()["suggestion"]
        assert suggestion is not None
        started = await client.post(
            "/questionnaire/runs", json={"file": file_name, "mapping": suggestion}, headers=headers
        )
        assert started.status_code == 202, started.text
        run_id = started.json()["run_id"]
        frames, times, text = await _read_events(client, run_id, headers)
        status = await client.get(f"/questionnaire/runs/{run_id}", headers=headers)
        assert status.status_code == 200, status.text
    return DemoRun(
        inspect=inspected.json(),
        run_id=run_id,
        started=started.json(),
        frames=frames,
        frame_times=times,
        stream_text=text,
        status=status.json(),
        input_sha_before=before,
        input_sha_after=sha256_of(source),
    )


def check_run(run: DemoRun, folders: Folders, tool: ModuleType) -> list[str]:
    """Everything that must hold however the model answers. Returns problems (empty = fine)."""
    problems: list[str] = []
    total = len(tool.DEMO_QUESTIONS)
    names = [name for name, _ in run.frames]
    if names[:1] != ["started"] or names[-1:] != ["done"]:
        problems.append(f"stream is not started ... done: {names[:2]} ... {names[-2:]}")
    if names.count("progress") != total:
        problems.append(f"{names.count('progress')} progress events, expected {total}")
    if run.status["status"] not in ("complete", "stopped"):
        problems.append(f"run status is {run.status['status']}")
    summary = run.status["summary"]
    if summary is None:
        return [*problems, "no summary"]
    if sum(summary["counts"].values()) != total:
        problems.append(f"summary counts do not add up to {total}: {summary['counts']}")
    if run.input_sha_before != run.input_sha_after:
        problems.append("the input file changed")
    if summary["input_sha256"] != run.input_sha_before:
        problems.append("the summary's input hash is not the input file's hash")
    if run.inspect["sha256"] != run.input_sha_before:
        problems.append("the inspect hash is not the input file's hash")
    for question in tool.DEMO_QUESTIONS:  # nothing readable crosses the stream
        if question.question in run.stream_text:
            problems.append(f"question text in the event stream: {question.ref}")
    return problems


def check_output(output: Path, source: Path, tool: ModuleType) -> list[str]:
    """The answers file against the original: nothing original changed, columns appended."""
    problems: list[str] = []
    if "UNREVIEWED-DRAFT" not in output.name:
        problems.append(f"output name lacks UNREVIEWED-DRAFT: {output.name}")
    before = load_workbook(source)
    after = load_workbook(output)
    try:
        if before.sheetnames != after.sheetnames:
            problems.append("sheet names or order changed")
        old = before[tool.SHEET_NAME]
        new = after[tool.SHEET_NAME]
        if new.max_row != old.max_row:
            problems.append(f"row count changed: {old.max_row} -> {new.max_row}")
        for row in range(1, old.max_row + 1):
            for col in range(1, old.max_column + 1):
                if old.cell(row=row, column=col).value != new.cell(row=row, column=col).value:
                    problems.append(f"original cell changed at row {row}, column {col}")
        if sorted(map(str, old.merged_cells.ranges)) != sorted(map(str, new.merged_cells.ranges)):
            problems.append("merged ranges changed")
        if len(old.data_validations.dataValidation) != len(new.data_validations.dataValidation):
            problems.append("data validations changed")
        problems.extend(_check_new_columns(old.max_column, new, tool))
    finally:
        before.close()
        after.close()
    return problems


def _check_new_columns(first_old_column: int, sheet: Any, tool: ModuleType) -> list[str]:
    problems: list[str] = []
    headers = [sheet.cell(row=1, column=first_old_column + 1 + i).value for i in range(6)]
    for found, expected in zip(headers, EXPORT_HEADERS, strict=True):
        if found != expected:
            problems.append(f"header {found!r} is not {expected!r}")
    answer_col = first_old_column + 1
    status_col = first_old_column + 3
    asked = {q.ref for q in tool.DEMO_QUESTIONS}
    for row in range(2, sheet.max_row + 1):
        ref = sheet.cell(row=row, column=1).value
        status = sheet.cell(row=row, column=status_col).value
        answer = sheet.cell(row=row, column=answer_col).value
        if ref in asked:
            if status not in STATUSES:
                problems.append(f"{ref}: status {status!r}")
            if (status == "NEEDS_REVIEW") != (answer in (None, "")):
                problems.append(f"{ref}: a blank answer must be exactly the NEEDS_REVIEW rows")
        elif status is not None or answer is not None:
            problems.append(f"row {row} is not a question but has an answer or a status")
    return problems


def report_lines(run: DemoRun, folders: Folders, tool: ModuleType) -> list[str]:
    """What the owner reads after a live run: per-question result next to the expectation note."""
    summary = run.status["summary"]
    lines = [
        f"run {run.run_id}: {run.status['status']}  counts={summary['counts']}  "
        f"seconds={summary['seconds']}",
        f"model={summary['model']} digest before={summary['digest_before']} "
        f"after={summary['digest_after']}",
        f"events: {[name for name, _ in run.frames].count('progress')} progress; "
        f"first progress at {run.frame_times[1]:.1f}s, done at {run.frame_times[-1]:.1f}s",
        f"reason counts: {summary['reason_counts']}",
        f"dropped parts: {summary['dropped_parts']}  warnings: {summary['warnings']}",
        f"input sha256 before == after: {run.input_sha_before == run.input_sha_after}",
        "",
        f"{'ref':<5} {'status':<13} {'reasons':<34} expectation (a note, not a promise)",
    ]
    progress = [data for name, data in run.frames if name == "progress"]
    for question, data in zip(tool.DEMO_QUESTIONS, progress, strict=False):
        reasons = ",".join(data["reasons"])
        lines.append(
            f"{question.ref:<5} {data['status']:<13} {reasons:<34.34} {question.expectation}"
        )
    return lines


def place_input(questionnaires: Path, input_file: Path) -> str:
    """Copy a given .xlsx into the questionnaires folder and return its file name.

    The original is only read (once, then closed) and never written, renamed or moved. The copy
    is created exclusively, so nothing already in the folder is overwritten. The file must be the
    demo workbook saved under another name (for example by Excel): the checks that follow expect
    the demo's sheet and questions.
    """
    source = input_file.resolve(strict=True)
    if not source.is_file():
        raise ValueError(f"not a file: {input_file}")
    if source.suffix.lower() != ".xlsx":
        raise ValueError(f"not an .xlsx file: {input_file}")
    data = source.read_bytes()
    with (questionnaires / source.name).open("xb") as copy:
        copy.write(data)
    return source.name


def run_demo(
    base_url: str,
    work: Path,
    *,
    live: bool,
    build_index: bool = True,
    input_file: Path | None = None,
) -> tuple[DemoRun, Folders, ModuleType]:
    """The whole scenario. Returns the run, the folders and the tool module (for the checks).

    With `input_file`, that workbook is copied in instead of generating the demo one.
    """
    _refuse_real_ollama_unless_live(base_url, live)
    tool = load_demo_tool()
    folders = Folders.under(work)
    original_sha = sha256_of(input_file) if input_file is not None else None
    if input_file is None:
        tool.write_demo(folders.questionnaires)
        file_name = tool.FILE_NAME
    else:
        file_name = place_input(folders.questionnaires, input_file)
    if build_index:
        asyncio.run(build_demo_index(base_url, folders.index, live=live))
    token = secrets.token_hex(32)
    with running_sidecar(folders, base_url, token) as port:
        timeout = 7200 if live else 120
        run = asyncio.run(
            asyncio.wait_for(drive_demo_run(port, token, folders, file_name), timeout)
        )
    outputs = sorted(folders.exports.iterdir())
    run.problems = check_run(run, folders, tool)
    if input_file is not None and sha256_of(input_file) != original_sha:
        run.problems.append("the original input file changed")
    if len(outputs) == 1:
        run.output = outputs[0]
        run.problems += check_output(run.output, folders.questionnaires / file_name, tool)
    else:
        failure = run.status.get("failure")
        run.problems.append(f"{len(outputs)} files in the export folder; run failure={failure}")
    return run, folders, tool
