"""The end-to-end demo run, offline (M5l): a real sidecar process, real HTTP, a fake Ollama.

`test_e2e_live.py` runs the same scenario against the real Ollama and is started only by the owner.
Here the model side is a small fake server on an unused loopback port (never 11434) that answers
"no evidence" to every question, so every row ends NEEDS_REVIEW. That is enough to prove the
plumbing end to end: the index built by the existing indexer, the child process and its token, the
routes, the event stream, the retriever, the writer and the file on disk.

What this cannot show: what a real model says, a SUPPORTED or PARTIAL answer written to a cell
(`test_writer.py` and `test_questionnaire_runs.py` cover the writing), or a live stream read while a
slow run is going (the fake answers instantly; live check B reads it live).
"""

import asyncio
import secrets
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from grc_sidecar.security.net import create_async_client

from .e2ekit import (
    REAL_OLLAMA_PORT,
    TOKEN_HEADER,
    Folders,
    _refuse_real_ollama_unless_live,
    load_demo_tool,
    report_lines,
    run_demo,
    running_sidecar,
)
from .test_answer_live_wiring import FakeOllamaServer, fake_ollama


def test_the_demo_run_end_to_end_over_http(tmp_path: Path) -> None:
    with fake_ollama() as url:
        assert urlsplit(url).port != REAL_OLLAMA_PORT
        run, folders, tool = run_demo(url, tmp_path, live=False)
    assert not run.problems, "\n".join(run.problems)
    assert len(tool.DEMO_QUESTIONS) == 15
    summary = run.status["summary"]
    assert run.status["status"] == "complete" and summary["complete"] is True
    # the fake says "no evidence" every time
    assert summary["counts"] == {"SUPPORTED": 0, "PARTIAL": 0, "NEEDS_REVIEW": 15}
    assert {k: v for k, v in summary["reason_counts"].items() if v} == {"no_evidence": 15}
    assert summary["rows_written"] == 15
    assert summary["digest_before"] == summary["digest_after"]
    assert summary["digest_before"] is not None and summary["digest_before"].startswith(
        "sha256:fake"
    )
    assert run.frames[0] == ("started", {"run_id": run.run_id, "total": 15})
    progress = [data for name, data in run.frames if name == "progress"]
    assert [p["index"] for p in progress] == list(range(1, 16))
    assert all(set(p) == {"index", "total", "status", "reasons"} for p in progress)  # no text
    assert run.frames[-1][0] == "done" and run.frames[-1][1]["complete"] is True
    assert run.output is not None and run.output.parent == folders.exports
    assert run.output.name.startswith("demo-questionnaire-UNREVIEWED-DRAFT-")
    # The model side saw only the three endpoints the code is allowed to call, and every chat
    # request was exactly one system and one user message.
    assert {path for _, path in FakeOllamaServer.seen} == {"/api/tags", "/api/embed", "/api/chat"}
    assert len(FakeOllamaServer.chat_bodies) == 15
    for body in FakeOllamaServer.chat_bodies:
        assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert "ref" in "\n".join(report_lines(run, folders, tool))


def test_no_question_text_reaches_the_sidecar_log(tmp_path: Path) -> None:  # [NEG]
    with fake_ollama() as url:
        run, folders, tool = run_demo(url, tmp_path, live=False)
    assert not run.problems, "\n".join(run.problems)
    logs = [p for p in folders.logs.rglob("*") if p.is_file()]
    assert logs, "the sidecar wrote no log file at all, so this test would prove nothing"
    text = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in logs)
    for question in tool.DEMO_QUESTIONS:
        assert question.question not in text, question.ref


def test_the_real_sidecar_refuses_the_routes_without_the_token(tmp_path: Path) -> None:  # [NEG]
    folders = Folders.under(tmp_path)
    token = secrets.token_hex(32)

    async def attempts(port: int) -> list[int]:
        codes: list[int] = []
        async with create_async_client(f"http://127.0.0.1:{port}") as client:
            body = {"file": "x.xlsx"}
            codes.append((await client.post("/questionnaire/inspect", json=body)).status_code)
            codes.append((await client.get("/questionnaire/runs/" + "0" * 8)).status_code)
            codes.append(
                (await client.get("/questionnaire/runs/" + "0" * 8 + "/events")).status_code
            )
            wrong = {TOKEN_HEADER: "0" * 64}
            codes.append((await client.get("/health", headers=wrong)).status_code)
            codes.append((await client.get("/health", headers={TOKEN_HEADER: token})).status_code)
        return codes

    # An unused loopback address stands in for Ollama: the sidecar is never asked to call it here.
    with running_sidecar(folders, "http://127.0.0.1:9", token) as port:
        codes = asyncio.run(asyncio.wait_for(attempts(port), 60))
    assert codes == [401, 401, 401, 401, 200]


def test_an_offline_scenario_refuses_the_real_ollama_port() -> None:  # [NEG]
    with pytest.raises(pytest.fail.Exception):
        _refuse_real_ollama_unless_live("http://127.0.0.1:11434", live=False)
    _refuse_real_ollama_unless_live("http://127.0.0.1:11434", live=True)  # only the live test
    _refuse_real_ollama_unless_live("http://127.0.0.1:9", live=False)


def test_the_demo_workbook_is_the_questionnaire_the_engine_expects() -> None:
    tool = load_demo_tool()
    refs = [q.ref for q in tool.DEMO_QUESTIONS]
    assert refs == [f"D-{n:02d}" for n in range(1, 16)]
    assert len({q.question for q in tool.DEMO_QUESTIONS}) == 15
    assert all(len(q.question) <= 1_000 for q in tool.DEMO_QUESTIONS)
