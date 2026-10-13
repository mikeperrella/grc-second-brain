"""GET /reality/claims and POST /reality/check (ADR-016): authenticated, typed refusals, the claim
beside the counts, and nothing from the vault or the file in any error or log record (logins appear
only in the authenticated 200 body)."""

import logging
import shutil
from datetime import date
from pathlib import Path

import httpx
import pytest

from grc_sidecar.reality.runtime import RealityRuntime
from grc_sidecar.vault.clock import FixedClock

from .realitykit import (
    CANARY,
    FIXTURE,
    HEADERS,
    as_check_counts,
    claim_ref,
    csv_bytes,
    expected_check,
    row,
    write,
    write_policy_vault,
)
from .test_reality_routes import AUTH, client_for, run
from .vaultkit import DEMO_VAULT

CLAIMS = "/reality/claims"
CHECK = "/reality/check"
CLOCK = FixedClock(date(2026, 10, 13))


def runtime(imports: Path, vault: Path | None) -> RealityRuntime:
    return RealityRuntime(imports, vault_root=vault, clock=CLOCK)


def get_claims(rt: RealityRuntime | None, headers: dict[str, str] = AUTH) -> httpx.Response:
    async def scenario() -> httpx.Response:
        async with client_for(rt) as client:
            return await client.get(CLAIMS, headers=headers)

    return run(scenario())


def post_check(
    rt: RealityRuntime | None, body: object, headers: dict[str, str] = AUTH
) -> httpx.Response:
    async def scenario() -> httpx.Response:
        async with client_for(rt) as client:
            return await client.post(CHECK, json=body, headers=headers)

    return run(scenario())


def demo_runtime(tmp_path: Path) -> tuple[RealityRuntime, dict[str, object]]:
    vault = tmp_path / "vault"
    shutil.copytree(DEMO_VAULT, vault)
    imports = tmp_path / "imports"
    imports.mkdir()
    write(imports, FIXTURE.read_bytes(), "okta.csv")
    rt = runtime(imports, vault)
    listed = get_claims(rt).json()["candidates"]
    assert len(listed) == 1
    return rt, claim_ref(listed[0])


def body_for(chosen: dict[str, object], **fields: object) -> dict[str, object]:
    body: dict[str, object] = {
        "file": "okta.csv",
        "claim": chosen,
        "mfa_types": ["Okta Verify"],
        "phone_types": ["Phone"],
        "exported_at": "2026-10-01",
    }
    body.update(fields)
    return body


# ----- authentication ----------------------------------------------------------------------------

BAD_HEADERS = [{}, {"X-GRC-Auth": "0" * 64}, {"X-GRC-Auth": ""}]


@pytest.mark.parametrize("headers", BAD_HEADERS)
def test_both_routes_require_the_token(tmp_path: Path, headers: dict[str, str]) -> None:  # [NEG]
    rt, claim = demo_runtime(tmp_path)
    for response in (get_claims(rt, headers), post_check(rt, body_for(claim), headers)):
        assert response.status_code == 401
        assert response.content == b""  # no detail (CLAUDE.md 3.4)


# ----- not configured ----------------------------------------------------------------------------


def test_without_a_runtime_both_routes_refuse_with_a_typed_error() -> None:  # [NEG]
    claim = {"path": "p.md", "start_line": 1, "end_line": 1, "quote": "MFA is required."}
    for response in (get_claims(None), post_check(None, body_for(claim))):
        assert response.status_code == 503
        assert response.json() == {"error": "import_root_not_configured"}


def test_a_runtime_without_a_vault_refuses_both_routes(tmp_path: Path) -> None:  # [NEG]
    claim = {"path": "p.md", "start_line": 1, "end_line": 1, "quote": "MFA is required."}
    rt = runtime(tmp_path, None)
    for response in (get_claims(rt), post_check(rt, body_for(claim))):
        assert response.status_code == 503
        assert response.json() == {"error": "import_root_not_configured"}


def test_a_vault_folder_that_is_gone_is_a_typed_503(tmp_path: Path) -> None:  # [NEG]
    claim = {"path": "p.md", "start_line": 1, "end_line": 1, "quote": "MFA is required."}
    rt = runtime(tmp_path, tmp_path / "gone")
    assert get_claims(rt).json() == {"error": "import_root_not_configured"}
    assert post_check(rt, body_for(claim)).json() == {"error": "import_root_not_configured"}


# ----- claims ------------------------------------------------------------------------------------


def test_the_claims_route_lists_the_demo_sentence(tmp_path: Path) -> None:
    rt, _ = demo_runtime(tmp_path)
    body = get_claims(rt).json()
    assert set(body) == {
        "candidates",
        "candidates_not_listed",
        "too_long",
        "scanned_documents",
        "excluded_framework",
    }
    (candidate,) = body["candidates"]
    assert candidate["path"] == "access-control-policy.md"
    assert (candidate["start_line"], candidate["end_line"]) == (23, 23)
    assert candidate["mapped_to_authentication_control"] is True
    assert "hardware security key" not in candidate["quote"]


# ----- check -------------------------------------------------------------------------------------


def test_the_check_route_puts_the_claim_beside_counts_that_match_an_independent_pass(
    tmp_path: Path,
) -> None:
    rt, claim = demo_runtime(tmp_path)
    response = post_check(rt, body_for(claim))
    assert response.status_code == 200
    body = response.json()
    assert as_check_counts(body) == expected_check(FIXTURE, ["Okta Verify"], ["Phone"])
    assert body["claim"]["quote"] == claim["quote"]
    assert body["finding"] == "exceptions_found"
    assert body["statement"] == "this export, this claim, this date; not evidence the control works"
    assert body["exported_at"] == "2026-10-01"
    assert "complian" not in response.text.lower()
    assert set(body) == {
        "claim",
        "sha256",
        "exported_at",
        "finding",
        "statement",
        "staleness_note",
        "duplicate_note",
        "in_scope",
        "with_mfa",
        "without_mfa",
        "admins_without_mfa",
        "admin_unknown",
        "phone_only",
        "phone_types_named",
        "skipped",
        "skipped_not_listed",
        "malformed_rows",
        "duplicate_logins",
        "mfa_types_not_present",
        "exceptions",
        "exceptions_not_listed",
    }


def test_typed_refusals_are_422_with_a_fixed_code(tmp_path: Path) -> None:  # [NEG]
    rt, claim = demo_runtime(tmp_path)
    future = post_check(rt, body_for(claim, exported_at="2026-10-14"))
    assert (future.status_code, future.json()) == (
        422,
        {"error": "check_refused", "code": "exported_at_in_future"},
    )
    changed = {**claim, "quote": str(claim["quote"])[:-10]}
    fragment = post_check(rt, body_for(changed))
    assert (fragment.status_code, fragment.json()) == (
        422,
        {"error": "check_refused", "code": "claim_not_verbatim"},
    )
    empty_scope = post_check(rt, body_for(claim, active_statuses=["No Such Status"]))
    assert empty_scope.json() == {"error": "check_refused", "code": "no_users_in_scope"}


def test_a_refused_export_is_the_same_typed_422_as_inspect(tmp_path: Path) -> None:  # [NEG]
    rt, claim = demo_runtime(tmp_path)
    write(rt.import_root, b"\xff\xff\xff\xff not text", "bad.csv")
    response = post_check(rt, body_for(claim, file="bad.csv"))
    assert response.status_code == 422
    assert response.json() == {"error": "export_refused", "code": "encoding_not_allowed"}


def test_paths_that_leave_the_import_folder_are_typed(tmp_path: Path) -> None:  # [NEG]
    rt, claim = demo_runtime(tmp_path)
    outside = post_check(rt, body_for(claim, file="../vault/access-control-policy.md"))
    assert (outside.status_code, outside.json()) == (400, {"error": "path_not_allowed"})
    gone = post_check(rt, body_for(claim, file="nothing.csv"))
    assert (gone.status_code, gone.json()) == (404, {"error": "file_not_found"})


@pytest.mark.parametrize(
    ("change", "fields"),
    [
        ({"url": f"http://{CANARY}.test/x"}, ["url"]),
        ({"token": CANARY, "host": CANARY}, ["host", "token"]),
        ({"exported_at": CANARY}, ["exported_at"]),
        ({"exported_at": "2026-02-30"}, ["exported_at"]),
        ({"mfa_types": []}, ["mfa_types"]),
        ({"mfa_types": [CANARY * 5]}, ["mfa_types.0"]),
        ({"file": ""}, ["file"]),
        ({"active_statuses": []}, ["active_statuses"]),
        (
            {"claim": {"path": "p.md", "start_line": 0, "end_line": 1, "quote": CANARY}},
            ["claim.start_line"],
        ),
        ({"claim": {"path": "p.md", "start_line": 5, "end_line": 1, "quote": CANARY}}, ["claim"]),
        (
            {"claim": {"path": "p.md", "start_line": 1, "end_line": 1, "quote": CANARY, "x": 1}},
            ["claim.x"],
        ),
    ],
)
def test_a_malformed_body_is_a_422_that_names_fields_and_echoes_no_value(  # [NEG]
    tmp_path: Path, change: dict[str, object], fields: list[str]
) -> None:
    rt, claim = demo_runtime(tmp_path)
    response = post_check(rt, body_for(claim, **change))
    assert response.status_code == 422
    assert response.json() == {"error": "invalid_request", "fields": [f"body.{f}" for f in fields]}
    assert CANARY not in response.text


# ----- nothing from the vault or the file leaves except in the 200 body --------------------------

CANARY_LOGIN = f"{CANARY}-login@example.test"


def canary_runtime(tmp_path: Path) -> tuple[RealityRuntime, dict[str, object]]:
    vault = tmp_path / "vault"
    write_policy_vault(vault, f"MFA is required for {CANARY}-scope accounts.")
    imports = tmp_path / "imports"
    imports.mkdir()
    cells = row(login=CANARY_LOGIN, types="Password", full_name=f"{CANARY}-name")
    for column in ("user.email", "user.mobilePhone", "user.primaryPhone", "groups.name"):
        cells[HEADERS.index(column)] = f"{CANARY}-{column}"
    write(
        imports, csv_bytes([cells, cells, row("ok@example.test", types="Okta Verify")]), "okta.csv"
    )
    rt = runtime(imports, vault)
    return rt, claim_ref(get_claims(rt).json()["candidates"][0])


def with_log_level(level: int, caplog: pytest.LogCaptureFixture, call: object) -> httpx.Response:
    root_logger = logging.getLogger()
    previous = root_logger.level
    root_logger.setLevel(level)  # records below this level would never be created
    try:
        with caplog.at_level(level):
            assert callable(call)
            response: httpx.Response = call()
    finally:
        root_logger.setLevel(previous)
    return response


@pytest.mark.parametrize("level", [logging.INFO, logging.DEBUG])
def test_no_canary_reaches_any_log_record_and_logins_only_the_body(  # [NEG]
    tmp_path: Path, caplog: pytest.LogCaptureFixture, level: int
) -> None:
    rt, claim = canary_runtime(tmp_path)
    caplog.clear()
    response = with_log_level(level, caplog, lambda: post_check(rt, body_for(claim)))
    assert response.status_code == 200
    body = response.json()
    assert [item["login"] for item in body["exceptions"]] == [CANARY_LOGIN, CANARY_LOGIN]
    assert body["duplicate_logins"] == 1
    # The dropped columns (name, email, phones, groups) are nowhere in the answer.
    for dropped in ("-name", "-user.email", "-user.mobilePhone", "-user.primaryPhone", "-groups"):
        assert f"{CANARY}{dropped}" not in response.text
    assert caplog.records, "the route logged nothing, so this test would prove nothing"
    assert CANARY not in caplog.text
    for record in caplog.records:
        assert CANARY not in record.getMessage() and CANARY not in str(record.args)


@pytest.mark.parametrize("level", [logging.INFO, logging.DEBUG])
def test_no_canary_reaches_a_refusal_or_the_log(  # [NEG]
    tmp_path: Path, caplog: pytest.LogCaptureFixture, level: int
) -> None:
    rt, claim = canary_runtime(tmp_path)
    caplog.clear()
    wrong = {**claim, "quote": f"{claim['quote']} {CANARY}-extra"}
    refused = with_log_level(level, caplog, lambda: post_check(rt, body_for(wrong)))
    assert refused.json() == {"error": "check_refused", "code": "claim_not_verbatim"}
    assert CANARY not in refused.text and CANARY not in caplog.text


@pytest.mark.parametrize("level", [logging.INFO, logging.DEBUG])
def test_the_claims_route_never_logs_vault_text(  # [NEG]
    tmp_path: Path, caplog: pytest.LogCaptureFixture, level: int
) -> None:
    rt, _ = canary_runtime(tmp_path)
    caplog.clear()
    response = with_log_level(level, caplog, lambda: get_claims(rt))
    assert response.status_code == 200 and CANARY in response.text  # the body is for the caller
    assert CANARY not in caplog.text
