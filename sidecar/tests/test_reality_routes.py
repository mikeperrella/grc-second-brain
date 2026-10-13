"""POST /reality/inspect (ADR-016): authenticated, typed refusals, counts only, and nothing from the
file in any answer, error or log record."""

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TypeVar

import httpx
import pytest

from grc_sidecar.main import create_app
from grc_sidecar.reality.export_reader import Caps
from grc_sidecar.reality.runtime import RealityRuntime
from grc_sidecar.security.net import create_async_client

from .realitykit import (
    CANARY,
    FIXTURE,
    HEADERS,
    as_counts,
    csv_bytes,
    expected_counts,
    row,
    write,
)

TOKEN = hashlib.sha256(b"grc-test-token").hexdigest()
AUTH = {"X-GRC-Auth": TOKEN}
INSPECT = "/reality/inspect"
SCENARIO_TIMEOUT_SECONDS = 30  # a broken control must fail a test, not hang the suite
T = TypeVar("T")


@asynccontextmanager
async def client_for(runtime: RealityRuntime | None) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(TOKEN, reality=runtime)
    async with create_async_client(
        "http://127.0.0.1:8000", transport=httpx.ASGITransport(app=app)
    ) as client:
        yield client


def run(scenario: Awaitable[T]) -> T:
    return asyncio.run(asyncio.wait_for(scenario, SCENARIO_TIMEOUT_SECONDS))  # type: ignore[arg-type]


def post(
    runtime: RealityRuntime | None, body: object, headers: dict[str, str] = AUTH
) -> httpx.Response:
    async def scenario() -> httpx.Response:
        async with client_for(runtime) as client:
            return await client.post(INSPECT, json=body, headers=headers)

    return run(scenario())


# ----- authentication ----------------------------------------------------------------------------


@pytest.mark.parametrize("headers", [{}, {"X-GRC-Auth": "0" * 64}, {"X-GRC-Auth": ""}])
def test_the_inspect_route_requires_the_token(  # [NEG]
    tmp_path: Path, headers: dict[str, str]
) -> None:
    name = write(tmp_path, csv_bytes([row()]))
    response = post(RealityRuntime(tmp_path), {"file": name}, headers)
    assert response.status_code == 401
    assert response.content == b""  # no detail (CLAUDE.md 3.4)


# ----- not configured ----------------------------------------------------------------------------


def test_without_a_runtime_the_route_refuses_with_a_typed_error() -> None:  # [NEG]
    response = post(None, {"file": "export.csv"})
    assert response.status_code == 503
    assert response.json() == {"error": "import_root_not_configured"}


def test_an_import_folder_that_is_gone_is_a_typed_503(tmp_path: Path) -> None:  # [NEG]
    response = post(RealityRuntime(tmp_path / "gone"), {"file": "export.csv"})
    assert response.status_code == 503
    assert response.json() == {"error": "import_root_not_configured"}


# ----- inspect -----------------------------------------------------------------------------------


def test_inspect_returns_the_counts_for_the_fixture(tmp_path: Path) -> None:
    write(tmp_path, FIXTURE.read_bytes(), "okta.csv")
    response = post(RealityRuntime(tmp_path), {"file": "okta.csv"})
    assert response.status_code == 200
    body = response.json()
    assert as_counts(body) == expected_counts(FIXTURE)
    assert set(body) == {
        "sha256",
        "size_bytes",
        "data_rows",
        "malformed_rows",
        "statuses",
        "statuses_not_listed",
        "authenticator_types",
        "authenticator_types_not_listed",
        "duplicate_logins",
    }
    assert "example.test" not in response.text  # no login, email or name


def test_a_refused_file_is_a_typed_422_with_a_fixed_word(tmp_path: Path) -> None:  # [NEG]
    write(tmp_path, b"\xff\xff\xff\xff not text")
    response = post(RealityRuntime(tmp_path), {"file": "export.csv"})
    assert response.status_code == 422
    assert response.json() == {"error": "export_refused", "code": "encoding_not_allowed"}


def test_a_missing_column_names_the_header(tmp_path: Path) -> None:  # [NEG]
    headers = [h for h in HEADERS if h != "user.isAdmin"]
    cells = [c for h, c in zip(HEADERS, row(), strict=True) if h != "user.isAdmin"]
    write(tmp_path, csv_bytes([cells], headers))
    response = post(RealityRuntime(tmp_path), {"file": "export.csv"})
    assert response.status_code == 422
    assert response.json() == {
        "error": "export_refused",
        "code": "missing_column",
        "column": "user.isAdmin",
    }


def test_a_cap_is_a_typed_422(tmp_path: Path) -> None:  # [NEG]
    write(tmp_path, csv_bytes([row(), row()]))
    response = post(RealityRuntime(tmp_path, Caps(max_rows=1)), {"file": "export.csv"})
    assert response.status_code == 422
    assert response.json() == {"error": "export_refused", "code": "too_many_rows"}


def test_paths_that_leave_the_folder_or_do_not_exist_are_typed(tmp_path: Path) -> None:  # [NEG]
    root = tmp_path / "imports"
    root.mkdir()
    (tmp_path / "outside.csv").write_bytes(csv_bytes([row()]))
    runtime = RealityRuntime(root)
    for name in ("../outside.csv", str(tmp_path / "outside.csv")):
        response = post(runtime, {"file": name})
        assert (response.status_code, response.json()) == (400, {"error": "path_not_allowed"})
    gone = post(runtime, {"file": "nothing.csv"})
    assert (gone.status_code, gone.json()) == (404, {"error": "file_not_found"})


# ----- malformed bodies --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        ({"file": 123}, ["file"]),
        ({"file": ""}, ["file"]),
        ({"file": "x" * 300}, ["file"]),
        ({}, ["file"]),
        ({"file": "a.csv", "url": f"http://{CANARY}.test/x"}, ["url"]),
        ({"file": "a.csv", "token": CANARY, "host": CANARY}, ["host", "token"]),
    ],
)
def test_a_malformed_body_is_a_422_that_names_fields_and_echoes_no_value(  # [NEG]
    tmp_path: Path, body: dict[str, object], fields: list[str]
) -> None:
    response = post(RealityRuntime(tmp_path), body)
    assert response.status_code == 422
    assert response.json() == {"error": "invalid_request", "fields": [f"body.{f}" for f in fields]}
    assert CANARY not in response.text and "x" * 20 not in response.text


# ----- nothing from the file leaves ------------------------------------------------------------


def _canary_file(root: Path, *, extra: bytes = b"") -> None:
    cells = row(login=f"{CANARY}-login", full_name=f"{CANARY}-name")
    for column in ("user.email", "user.mobilePhone", "user.primaryPhone", "groups.name"):
        cells[HEADERS.index(column)] = f"{CANARY}-{column}"
    write(root, csv_bytes([cells, cells]) + extra)


@pytest.mark.parametrize("level", [logging.INFO, logging.DEBUG])
def test_no_canary_reaches_the_answer_or_any_log_record(  # [NEG]
    tmp_path: Path, caplog: pytest.LogCaptureFixture, level: int
) -> None:
    _canary_file(tmp_path)
    root_logger = logging.getLogger()
    previous = root_logger.level
    root_logger.setLevel(level)  # records below this level would never be created
    try:
        with caplog.at_level(level):
            response = post(RealityRuntime(tmp_path), {"file": "export.csv"})
    finally:
        root_logger.setLevel(previous)
    assert response.status_code == 200
    assert response.json()["duplicate_logins"] == 1
    assert CANARY not in response.text
    assert caplog.records, "the route logged nothing, so this test would prove nothing"
    for record in caplog.records:
        assert CANARY not in record.getMessage() and CANARY not in str(record.args)
    assert CANARY not in caplog.text


@pytest.mark.parametrize("level", [logging.INFO, logging.DEBUG])
def test_no_canary_reaches_a_refusal_or_the_log(  # [NEG]
    tmp_path: Path, caplog: pytest.LogCaptureFixture, level: int
) -> None:
    _canary_file(tmp_path, extra=b"\x80")  # invalid UTF-8 after the canary rows
    root_logger = logging.getLogger()
    previous = root_logger.level
    root_logger.setLevel(level)
    try:
        with caplog.at_level(level):
            response = post(RealityRuntime(tmp_path), {"file": "export.csv"})
    finally:
        root_logger.setLevel(previous)
    assert response.status_code == 422
    assert CANARY not in response.text and CANARY not in caplog.text
