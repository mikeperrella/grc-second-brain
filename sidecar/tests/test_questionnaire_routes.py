"""The questionnaire routes (ADR-014): authenticated, typed refusals, server-sent events of codes
only, and a sidecar that keeps answering while a run is going."""

import asyncio
import hashlib
import json
import logging
import threading
import zipfile
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

import httpx
import pytest

from grc_sidecar.errors import IndexNotBuiltError
from grc_sidecar.main import create_app
from grc_sidecar.questionnaire.runs import QuestionnaireRuns
from grc_sidecar.questionnaire.service import AnswerOutcome
from grc_sidecar.security.net import create_async_client

from .runskit import (
    CANARY,
    FILE_HASH,
    FILE_NAME,
    MAPPING,
    FakeGate,
    FakeVault,
    ScriptedAnswerer,
    make_workbook,
    models_unavailable,
    outcome,
    setup,
    supported,
    unavailable_outcome,
)
from .xlsxkit import rebuild, simple_workbook, to_bytes

TOKEN = hashlib.sha256(b"grc-test-token").hexdigest()
AUTH = {"X-GRC-Auth": TOKEN}
BODY = {"file": FILE_NAME, "mapping": MAPPING.model_dump()}
T = TypeVar("T")


@asynccontextmanager
async def client_for(runs: QuestionnaireRuns | None) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(TOKEN, questionnaire=runs)
    async with create_async_client(
        "http://127.0.0.1:8000", transport=httpx.ASGITransport(app=app)
    ) as client:
        yield client


SCENARIO_TIMEOUT_SECONDS = 30  # a broken control must fail a test, not hang the suite


def run(scenario: Awaitable[T]) -> T:
    return asyncio.run(asyncio.wait_for(scenario, SCENARIO_TIMEOUT_SECONDS))  # type: ignore[arg-type]


def frames(text: str) -> list[tuple[str, dict[str, object]]]:
    """Parse server-sent events: (event name, data) in order."""
    found: list[tuple[str, dict[str, object]]] = []
    for block in text.strip().split("\n\n"):
        lines = block.split("\n")
        assert lines[0].startswith("event: ") and lines[1].startswith("data: "), block
        found.append((lines[0][len("event: ") :], json.loads(lines[1][len("data: ") :])))
    return found


async def start_and_wait(client: httpx.AsyncClient) -> tuple[str, str]:
    """Start a run and read its whole event stream. Returns (run id, the raw stream text)."""
    started = await client.post("/questionnaire/runs", json=BODY, headers=AUTH)
    assert started.status_code == 202, started.text
    run_id = started.json()["run_id"]
    stream = await client.get(f"/questionnaire/runs/{run_id}/events", headers=AUTH)
    assert stream.status_code == 200
    return run_id, stream.text


# ----- every route needs the token -----------------------------------------------------------

ROUTES = [
    ("POST", "/questionnaire/inspect", {"file": FILE_NAME}),
    ("POST", "/questionnaire/runs", BODY),
    ("GET", f"/questionnaire/runs/{'0' * 32}", None),
    ("GET", f"/questionnaire/runs/{'0' * 32}/events", None),
]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
@pytest.mark.parametrize("headers", [{}, {"X-GRC-Auth": "0" * 64}, {"X-GRC-Auth": ""}])
def test_questionnaire_routes_require_token(  # [NEG]
    tmp_path: Path, method: str, path: str, body: dict[str, object] | None, headers: dict[str, str]
) -> None:
    s = setup(tmp_path)

    async def scenario() -> None:
        async with client_for(s.runs) as client:
            response = await client.request(method, path, json=body, headers=headers)
            assert response.status_code == 401
            assert response.content == b""  # no detail (CLAUDE.md 3.4)

    run(scenario())
    assert s.answerer.questions == [] and s.exports() == []


def test_the_event_stream_is_refused_without_the_token_even_for_a_real_run(tmp_path: Path) -> None:
    s = setup(tmp_path)

    async def scenario() -> None:
        async with client_for(s.runs) as client:
            run_id, _ = await start_and_wait(client)
            denied = await client.get(f"/questionnaire/runs/{run_id}/events")
            assert denied.status_code == 401 and denied.content == b""
            denied = await client.get(f"/questionnaire/runs/{run_id}")
            assert denied.status_code == 401

    run(scenario())


# ----- not configured --------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_without_configured_folders_every_route_refuses_with_a_typed_error(  # [NEG]
    method: str, path: str, body: dict[str, object] | None
) -> None:
    async def scenario() -> None:
        async with client_for(None) as client:
            response = await client.request(method, path, json=body, headers=AUTH)
            assert response.status_code == 503
            assert response.json() == {"error": "questionnaire_root_not_configured"}

    run(scenario())


# ----- inspect ---------------------------------------------------------------------------------


def test_inspect_describes_the_workbook(tmp_path: Path) -> None:
    s = setup(tmp_path, count=2)

    async def scenario() -> None:
        async with client_for(s.runs) as client:
            response = await client.post(
                "/questionnaire/inspect", json={"file": FILE_NAME}, headers=AUTH
            )
            assert response.status_code == 200
            body = response.json()
            assert body["sheets"] == [{"name": "Questions", "rows": 3, "columns": 2}]
            assert body["suggestion"] == MAPPING.model_dump()
            assert body["macro_enabled"] is False and body["parts"] == {}
            assert len(body["sha256"]) == 64

    run(scenario())


@pytest.mark.parametrize(
    ("file", "status", "error"),
    [
        ("../outside.xlsx", 400, "path_not_allowed"),
        ("missing.xlsx", 404, "file_not_found"),
    ],
)
def test_inspect_refuses_paths_it_must_not_read(  # [NEG]
    tmp_path: Path, file: str, status: int, error: str
) -> None:
    s = setup(tmp_path)
    (tmp_path / "outside.xlsx").write_bytes(make_workbook(1))

    async def scenario() -> None:
        async with client_for(s.runs) as client:
            response = await client.post(
                "/questionnaire/inspect", json={"file": file}, headers=AUTH
            )
            assert response.status_code == status
            assert response.json() == {"error": error}  # fixed word, no path echoed

    run(scenario())


def test_a_refused_workbook_is_a_422_with_its_code(tmp_path: Path) -> None:  # [NEG]
    hostile = rebuild(
        make_workbook(2),
        replace={
            "xl/worksheets/sheet1.xml": b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]><a/>'
        },
    )
    s = setup(tmp_path, data=hostile)

    async def scenario() -> None:
        async with client_for(s.runs) as client:
            response = await client.post(
                "/questionnaire/inspect", json={"file": FILE_NAME}, headers=AUTH
            )
            assert response.status_code == 422
            assert response.json() == {"error": "workbook_refused", "code": "doctype"}

    run(scenario())


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"file": ""},
        {"file": "x" * 300},
        {"file": FILE_NAME, "extra": 1},
        {"file": 5},
    ],
)
def test_a_malformed_body_is_a_422_that_never_echoes_the_values(  # [NEG]
    tmp_path: Path, body: dict[str, object]
) -> None:
    s = setup(tmp_path)

    async def scenario() -> None:
        async with client_for(s.runs) as client:
            response = await client.post("/questionnaire/inspect", json=body, headers=AUTH)
            assert response.status_code == 422
            assert response.json()["error"] == "invalid_request"
            assert "x" * 50 not in response.text and "extra" not in response.json()["fields"]

    run(scenario())


def test_a_malformed_run_request_names_fields_not_values(tmp_path: Path) -> None:  # [NEG]
    s = setup(tmp_path)
    bad = {"file": f"{CANARY}.xlsx", "mapping": {"sheet": "S", "header_row": 0, "question_col": 1}}

    async def scenario() -> None:
        async with client_for(s.runs) as client:
            response = await client.post("/questionnaire/runs", json=bad, headers=AUTH)
            assert response.status_code == 422
            assert response.json() == {
                "error": "invalid_request",
                "fields": ["body.mapping.header_row"],
            }
            assert CANARY not in response.text

    run(scenario())


# ----- a run over HTTP -------------------------------------------------------------------------


def test_a_run_streams_progress_and_ends_with_a_summary(tmp_path: Path) -> None:
    s = setup(tmp_path, count=3)

    async def scenario() -> None:
        async with client_for(s.runs) as client:
            run_id, text = await start_and_wait(client)
            parsed = frames(text)
            assert [name for name, _ in parsed] == [
                "started",
                "progress",
                "progress",
                "progress",
                "done",
            ]
            assert parsed[0][1] == {"run_id": run_id, "total": 3}
            assert parsed[1][1] == {"index": 1, "total": 3, "status": "SUPPORTED", "reasons": []}
            status = await client.get(f"/questionnaire/runs/{run_id}", headers=AUTH)
            body = status.json()
            assert body["status"] == "complete" and body["completed"] == 3
            assert body["summary"]["counts"] == {"SUPPORTED": 3, "PARTIAL": 0, "NEEDS_REVIEW": 0}
            assert body["summary"]["complete"] is True
            assert body["summary"]["file_name"] in s.exports()

    run(scenario())


def test_the_stream_is_server_sent_events_and_not_cached(tmp_path: Path) -> None:
    s = setup(tmp_path, count=1)

    async def scenario() -> None:
        async with client_for(s.runs) as client:
            started = await client.post("/questionnaire/runs", json=BODY, headers=AUTH)
            response = await client.get(
                f"/questionnaire/runs/{started.json()['run_id']}/events", headers=AUTH
            )
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["cache-control"] == "no-store"

    run(scenario())


def test_events_over_http_carry_no_text(tmp_path: Path) -> None:  # [NEG]
    rows = [(f"Q-{n}", f"{CANARY} question {n}?") for n in range(1, 4)]
    s = setup(tmp_path, data=to_bytes(simple_workbook(rows)))
    s.answerer.script = [outcome(supported(f"answer {CANARY}"))] * 3

    async def scenario() -> None:
        async with client_for(s.runs) as client:
            run_id, text = await start_and_wait(client)
            status = (await client.get(f"/questionnaire/runs/{run_id}", headers=AUTH)).text
            for wire in (text, status):
                assert CANARY not in wire
                assert "AES-256" not in wire and "encryption-policy" not in wire

    run(scenario())


def test_a_second_run_over_http_is_a_409_and_health_still_answers(tmp_path: Path) -> None:  # [NEG]
    s = setup(tmp_path)
    s.answerer.hold = hold = asyncio.Event()

    async def scenario() -> None:
        async with client_for(s.runs) as client:
            started = await client.post("/questionnaire/runs", json=BODY, headers=AUTH)
            run_id = started.json()["run_id"]
            await s.answerer.entered.wait()  # a question is being answered; the run is held still
            # The event loop is free: health answers while the run is going.
            health = await client.get("/health", headers=AUTH)
            assert health.status_code == 200 and health.json() == {"status": "ok"}
            second = await client.post("/questionnaire/runs", json=BODY, headers=AUTH)
            assert second.status_code == 409 and second.json() == {"error": "run_in_progress"}
            status = await client.get(f"/questionnaire/runs/{run_id}", headers=AUTH)
            assert status.json()["status"] == "running"
            hold.set()
            stream = await client.get(f"/questionnaire/runs/{run_id}/events", headers=AUTH)
            assert frames(stream.text)[-1][0] == "done"

    run(scenario())


def test_health_answers_during_run(tmp_path: Path) -> None:  # [NEG]
    """The workbook, file and SQLite work run off the event loop; here the model call is held on an
    event, with no sleeps, and /health must still answer in the middle of it."""
    s = setup(tmp_path)
    s.answerer.hold = hold = asyncio.Event()

    async def scenario() -> None:
        async with client_for(s.runs) as client:
            await client.post("/questionnaire/runs", json=BODY, headers=AUTH)
            await s.answerer.entered.wait()
            for _ in range(3):
                assert (await client.get("/health", headers=AUTH)).status_code == 200
            hold.set()
            await s.runs.aclose()

    run(scenario())


@dataclass
class OneAtATimeAnswerer(ScriptedAnswerer):
    """Answers a question only when the test lets one through (a semaphore, no sleeps)."""

    turnstile: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(0))

    async def answer(self, question: str) -> AnswerOutcome:
        await self.turnstile.acquire()
        return await super().answer(question)


async def settle() -> None:
    """Let the event loop run a few turns so a wrongly early update would have happened."""
    for _ in range(20):
        await asyncio.sleep(0)


def test_completed_is_exact_after_each_answer_and_the_summary_waits_for_the_file(  # [NEG]
    tmp_path: Path,
) -> None:
    """Pins the status route mid-run (M6_PLAN Check 1, F38-4): `completed` is 0, 1, 2 ... total
    after exactly that many answers, and `summary` stays null until the answers file is written.

    The last-answer write phase CAN be paused: the writer calls `current_sha256` (on a worker
    thread) for every cited row, so a threading.Event held there freezes the run after the final
    answer and before the file exists."""
    total = 4
    in_writer, let_writer_go = threading.Event(), threading.Event()

    def held_hash(_path: str) -> str:
        in_writer.set()
        assert let_writer_go.wait(20)
        return FILE_HASH

    answerer = OneAtATimeAnswerer()
    s = setup(tmp_path, count=total, answerer=answerer, current_sha256=held_hash)

    async def scenario() -> None:
        async with client_for(s.runs) as client:
            started = await client.post("/questionnaire/runs", json=BODY, headers=AUTH)
            assert started.status_code == 202 and started.json()["total"] == total
            run_id = started.json()["run_id"]

            async def status() -> dict[str, object]:
                body = (await client.get(f"/questionnaire/runs/{run_id}", headers=AUTH)).json()
                assert body["total"] == total
                return dict(body)

            await settle()
            before = await status()
            assert (before["status"], before["completed"], before["summary"]) == (
                "running",
                0,
                None,
            )
            for expected in range(1, total + 1):
                answerer.turnstile.release()  # exactly one answer goes through
                for _ in range(500):
                    if (await status())["completed"] == expected:
                        break
                    await asyncio.sleep(0.01)
                await settle()
                now = await status()
                assert now["completed"] == expected  # exact, and not one ahead after settling
                assert now["summary"] is None and now["failure"] is None
                assert len(answerer.questions) == expected
                if expected < total:
                    assert now["status"] == "running"
            # All answers are in, the file is not written yet: completed == total, summary null.
            for _ in range(500):
                if in_writer.is_set():
                    break
                await asyncio.sleep(0.01)
            assert in_writer.is_set()
            paused = await status()
            assert (paused["status"], paused["completed"], paused["summary"]) == (
                "running",
                total,
                None,
            )
            assert s.exports() == []  # no answers file yet
            let_writer_go.set()
            stream = await client.get(f"/questionnaire/runs/{run_id}/events", headers=AUTH)
            assert frames(stream.text)[-1][0] == "done"
            end = await status()
            assert (end["status"], end["completed"]) == ("complete", total)
            assert isinstance(end["summary"], dict) and end["summary"]["rows_written"] == total
            assert len(s.exports()) == 1

    run(scenario())


# ----- typed refusals over HTTP -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "status", "body"),
    [
        (
            {"gate": FakeGate(models_unavailable())},
            503,
            {"error": "models_unavailable", "models_missing": ["llama3.1:8b"]},
        ),
        ({"vault": FakeVault(withheld=("a.md",))}, 409, {"error": "vault_changed_reindex"}),
        ({"vault": FakeVault(error=IndexNotBuiltError())}, 409, {"error": "index_not_built"}),
    ],
)
def test_a_refused_run_is_a_typed_error_and_writes_no_file(  # [NEG]
    tmp_path: Path, kwargs: dict[str, object], status: int, body: dict[str, object]
) -> None:
    s = setup(tmp_path, **kwargs)  # type: ignore[arg-type]

    async def scenario() -> None:
        async with client_for(s.runs) as client:
            response = await client.post("/questionnaire/runs", json=BODY, headers=AUTH)
            assert response.status_code == status and response.json() == body

    run(scenario())
    assert s.exports() == [] and s.answerer.questions == []


def test_a_mapping_that_does_not_fit_is_a_422_with_its_code(tmp_path: Path) -> None:  # [NEG]
    s = setup(tmp_path)
    wrong = {"file": FILE_NAME, "mapping": {"sheet": "Nope", "header_row": 1, "question_col": 2}}

    async def scenario() -> None:
        async with client_for(s.runs) as client:
            response = await client.post("/questionnaire/runs", json=wrong, headers=AUTH)
            assert response.status_code == 422
            assert response.json() == {"error": "mapping_refused", "code": "sheet_not_found"}

    run(scenario())


@pytest.mark.parametrize("run_id", ["nope", "G" * 32, "0" * 7, "0" * 65, "ABCDEF01" * 4])
def test_a_malformed_run_id_is_a_422(tmp_path: Path, run_id: str) -> None:  # [NEG]
    s = setup(tmp_path)

    async def scenario() -> None:
        async with client_for(s.runs) as client:
            for path in (f"/questionnaire/runs/{run_id}", f"/questionnaire/runs/{run_id}/events"):
                response = await client.get(path, headers=AUTH)
                assert response.status_code == 422
                assert response.json()["error"] == "invalid_request"

    run(scenario())


def test_an_unknown_run_is_a_404(tmp_path: Path) -> None:  # [NEG]
    s = setup(tmp_path)

    async def scenario() -> None:
        async with client_for(s.runs) as client:
            for path in (
                f"/questionnaire/runs/{'a' * 32}",
                f"/questionnaire/runs/{'a' * 32}/events",
            ):
                response = await client.get(path, headers=AUTH)
                assert response.status_code == 404 and response.json() == {"error": "run_not_found"}

    run(scenario())


def test_a_run_that_loses_the_model_ends_stopped_over_http(tmp_path: Path) -> None:
    s = setup(tmp_path, count=3)
    s.answerer.script = [unavailable_outcome()]

    async def scenario() -> None:
        async with client_for(s.runs) as client:
            run_id, text = await start_and_wait(client)
            assert frames(text)[-1] == (
                "done",
                {
                    "complete": False,
                    "total": 3,
                    "counts": {"SUPPORTED": 0, "PARTIAL": 0, "NEEDS_REVIEW": 3},
                },
            )
            status = (await client.get(f"/questionnaire/runs/{run_id}", headers=AUTH)).json()
            assert status["status"] == "stopped" and status["summary"]["complete"] is False

    run(scenario())


# ----- logging: no text from a run reaches a log at any level -----------------------------------


class Recorder(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_no_canary_appears_in_any_log_record_for_a_full_run_through_the_routes(  # [NEG]
    tmp_path: Path,
) -> None:
    """The question, the answer and the quote carry a canary through the whole run, over HTTP, with
    every logger at DEBUG. Run-level logging holds codes only (the service's own canary test covers
    the answer service; this one covers the routes, the workbook and the writer)."""
    rows = [(f"Q-{n}", f"{CANARY} question {n}?") for n in range(1, 4)]
    s = setup(tmp_path, data=to_bytes(simple_workbook(rows)))
    hostile = outcome(supported(f"answer {CANARY}"))
    s.answerer.script = [hostile, hostile, hostile]
    recorder = Recorder()
    root = logging.getLogger()
    previous = root.level
    root.addHandler(recorder)
    root.setLevel(logging.DEBUG)

    async def scenario() -> None:
        async with client_for(s.runs) as client:
            await start_and_wait(client)

    try:
        run(scenario())
    finally:
        root.removeHandler(recorder)
        root.setLevel(previous)
    assert recorder.records, "the test recorded nothing, so it proves nothing"
    # Measured for ADR-014: during a whole run openpyxl and the zip library log nothing at INFO or
    # DEBUG (the only records are asyncio's own and the test client's httpx request lines).
    assert not [r for r in recorder.records if r.name.split(".")[0] in {"openpyxl", "zipfile"}]
    for record in recorder.records:
        assert CANARY not in record.getMessage()
        assert CANARY not in str(record.args) and CANARY not in (record.exc_text or "")
        assert record.exc_info is None or CANARY not in repr(record.exc_info[1])


def test_the_canary_reaches_the_output_file_so_the_log_test_is_not_vacuous(tmp_path: Path) -> None:
    rows = [(f"Q-{n}", f"{CANARY} question {n}?") for n in range(1, 3)]
    s = setup(tmp_path, data=to_bytes(simple_workbook(rows)))
    s.answerer.script = [outcome(supported(f"answer {CANARY}"))] * 2

    async def scenario() -> None:
        async with client_for(s.runs) as client:
            await start_and_wait(client)

    run(scenario())
    [name] = s.exports()
    with zipfile.ZipFile(s.export_root / name) as archive:
        everything = b"".join(archive.read(part) for part in archive.namelist())
    assert CANARY.encode() in everything
