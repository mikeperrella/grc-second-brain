"""What httpx and httpcore log while the REAL generation client talks to a server (G6, F13-1b).

The other tests use `httpx.MockTransport`, which bypasses the connection layer. This one starts a
tiny HTTP server on 127.0.0.1 (the egress guard allows loopback) so that the client's real transport
and httpcore run, then reads every log record at DEBUG, from every logger. The prompt, the question,
the reply and the model's text carry canaries; none may appear anywhere.

What was measured here is recorded in ADR-012 (M5f): at INFO httpx writes one line with the method,
the URL and the status; at DEBUG httpcore writes connection and header events, with no body.
"""

import asyncio
import json
import logging
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

import pytest

from grc_sidecar.llm.generation_client import GenerationClient
from grc_sidecar.llm.model_gate import ModelGate

from .clockkit import ManualClock
from .retrievalkit import FakeLister

MODEL = "llama3.1:8b"
CANARY_SYSTEM = "CANARY-SYSTEM-1a2b"
CANARY_USER = "CANARY-USER-3c4d"
CANARY_OUT = "CANARY-OUTPUT-5e6f"
SCHEMA: dict[str, object] = {"type": "object", "properties": {}}


class Handler(BaseHTTPRequestHandler):
    bodies: ClassVar[list[bytes]] = []

    def log_message(self, format: str, *args: Any) -> None:
        """Silence the server's own stderr logging: only the client's logs are under test."""

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        Handler.bodies.append(self.rfile.read(length))
        reply = {
            "model": MODEL,
            "message": {"role": "assistant", "content": json.dumps({"answer": CANARY_OUT})},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 50,
            "eval_count": 5,
        }
        payload = json.dumps(reply).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@contextmanager
def chat_server() -> Iterator[int]:
    Handler.bodies = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def generate_once(port: int) -> str:
    async def scenario() -> str:
        gate = ModelGate(FakeLister((MODEL,)), (MODEL,), ManualClock())
        client = GenerationClient(f"http://127.0.0.1:{port}", MODEL, gate)
        try:
            result = await client.generate(
                f"instructions {CANARY_SYSTEM}", f"question {CANARY_USER}", SCHEMA
            )
        finally:
            await client.aclose()
        return result.content

    return asyncio.run(scenario())


def test_the_canaries_really_travel_to_the_server_and_back() -> None:
    """If the canaries never left the process, the tests below would pass for no reason."""
    with chat_server() as port:
        content = generate_once(port)
    assert CANARY_OUT in content
    sent = Handler.bodies[0].decode("utf-8")
    assert CANARY_SYSTEM in sent and CANARY_USER in sent
    assert '"shift":false' in sent.replace(" ", "")


def test_no_canary_appears_in_any_log_record_at_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:  # [NEG]
    caplog.set_level(logging.DEBUG)  # the root logger: every library's records are captured
    with chat_server() as port:
        generate_once(port)
    assert caplog.records, "nothing was logged at all: the capture is not working"
    text = caplog.text
    for canary in (CANARY_SYSTEM, CANARY_USER, CANARY_OUT):
        assert canary not in text, canary
    loggers = {record.name.split(".")[0] for record in caplog.records}
    assert {"httpx", "httpcore"} <= loggers  # the real transport really ran


def test_at_info_httpx_writes_one_line_with_the_method_the_url_and_the_status(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    with chat_server() as port:
        generate_once(port)
    lines = [r.getMessage() for r in caplog.records if r.name == "httpx"]
    assert len(lines) == 1
    assert re.fullmatch(
        r'HTTP Request: POST http://127\.0\.0\.1:\d+/api/chat "HTTP/1\.[01] 200 OK"', lines[0]
    )
    assert not [r for r in caplog.records if r.name.startswith("httpcore")]  # DEBUG only
    for canary in (CANARY_SYSTEM, CANARY_USER, CANARY_OUT):
        assert canary not in caplog.text


def test_at_debug_httpcore_logs_events_not_bodies(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    with chat_server() as port:
        generate_once(port)
    messages = [r.getMessage() for r in caplog.records if r.name.startswith("httpcore")]
    assert messages
    assert any(m.startswith("send_request_headers") for m in messages)
    assert any(m.startswith("receive_response_body") for m in messages)
    # The body events carry no payload: a record is an event name plus its connection arguments.
    assert all("answer" not in m and "instructions" not in m for m in messages)
