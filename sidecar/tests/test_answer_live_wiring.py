"""The live test's wiring, run offline against a tiny fake Ollama on loopback.

`test_answer_live.py` needs a real Ollama and is run by hand. Everything in it that is not "the
model said something" is plumbing: building the merged vault, indexing it over HTTP, the gate, the
retriever, the generation client, the clock and the report. This runs that plumbing end to end
against a stand-in server that speaks the three endpoints, so a wiring mistake is found here and
not during a live check that costs minutes of the owner's time.

What it cannot show: what a real model says, what the real server does with `shift: false`, or the
real response shapes beyond what ADR-012 read from the source.
"""

import asyncio
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

from grc_sidecar.questionnaire.service import AnswerOutcome

from .answerlivekit import Row, format_report, run_eval
from .evalkit import load_eval_set
from .fakes import vector_for
from .servicekit import NO_EVIDENCE_REPLY
from .test_answer_live import GENERATION, EvalClock, live_service
from .vaultkit import EVAL_SET


class FakeOllamaServer(BaseHTTPRequestHandler):
    seen: ClassVar[list[tuple[str, str]]] = []
    chat_bodies: ClassVar[list[dict[str, Any]]] = []

    def log_message(self, format: str, *args: Any) -> None:
        """Silence the server's own stderr logging."""

    def _send(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        FakeOllamaServer.seen.append(("GET", self.path))
        models = [
            {"name": name, "model": name, "digest": f"sha256:fake-{name}"}
            for name in ("nomic-embed-text:latest", GENERATION)
        ]
        self._send({"models": models})

    def do_POST(self) -> None:
        FakeOllamaServer.seen.append(("POST", self.path))
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        if self.path == "/api/embed":
            vectors = [vector_for(text) for text in body["input"]]
            self._send({"embeddings": vectors, "prompt_eval_count": len(vectors)})
        elif self.path == "/api/chat":
            FakeOllamaServer.chat_bodies.append(body)
            self._send(
                {
                    "model": GENERATION,
                    "message": {"role": "assistant", "content": NO_EVIDENCE_REPLY},
                    "done": True,
                    "done_reason": "stop",
                    "prompt_eval_count": 900,
                    "eval_count": 20,
                }
            )
        else:
            self.send_response(404)
            self.end_headers()


@contextmanager
def fake_ollama() -> Iterator[str]:
    FakeOllamaServer.seen = []
    FakeOllamaServer.chat_bodies = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllamaServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_the_eval_clock_reads_the_pinned_date_from_the_eval_file() -> None:
    assert EvalClock().today() == load_eval_set(EVAL_SET).clock_today == date(2026, 10, 10)


def test_the_live_wiring_indexes_retrieves_asks_and_reports_over_real_http(tmp_path: Path) -> None:
    evalset = load_eval_set(EVAL_SET)
    items = [i for i in evalset.items if i.id in ("EV-011", "EV-031")]  # the bug-bounty-free pair
    assert len(items) == 2

    async def scenario() -> tuple[list[Row], str, AnswerOutcome]:
        with fake_ollama() as url:
            async with live_service(
                work=tmp_path, build=True, with_packs=False, base_url=url
            ) as parts:
                entry = await parts.generator.read_model_entry()
                rows = await run_eval(items, parts.service)
                single = await parts.service.answer("How many employees does the company have?")
            return rows, entry.digest or "", single

    rows, digest, single = asyncio.run(scenario())
    assert digest == f"sha256:fake-{GENERATION}"
    paths = FakeOllamaServer.seen
    assert ("GET", "/api/tags") in paths and ("POST", "/api/embed") in paths
    assert ("POST", "/api/chat") in paths
    assert {p for _, p in paths} == {"/api/tags", "/api/embed", "/api/chat"}  # nothing else
    for body in FakeOllamaServer.chat_bodies:  # the real client's request, as the server saw it
        assert [m["role"] for m in body["messages"]] == ["system", "user"]
        assert body["shift"] is False and body["stream"] is False
    assert all(row.outcome.result.status == "NEEDS_REVIEW" for row in rows)
    assert all(row.problems == () for row in rows)  # both blank-labeled items ended blank
    assert single.result.reasons == ("no_evidence",) and single.attempts == 1
    report = format_report(rows)
    assert "ACCEPTANCE (every blank-labeled item ends blank): PASS" in report
    assert (
        "ended blank by the model's own no_evidence: 2, by a code check: 0, answered: 0" in report
    )
    assert "none in this split" in report  # no identifier-only item was asked
    assert (tmp_path / "vault" / "acceptable-use-policy.md").is_file()  # the merged vault
    assert (tmp_path / "vault" / "printer-note.md").is_file()  # hostile notes are in the eval run
