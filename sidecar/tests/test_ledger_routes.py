"""The ledger routes (ADR-017): authenticated, typed refusals, no content in a refusal body, a page
size of 100, voided promises left out unless asked for, and the service wiring from the settings."""

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

import httpx
import pytest

from grc_sidecar.config import Settings
from grc_sidecar.ledger.service import PAGE_SIZE, LedgerService, build_ledger_service
from grc_sidecar.main import create_app
from grc_sidecar.repositories.base import NewPromise, NewPromiseEvent
from grc_sidecar.security.net import create_async_client
from grc_sidecar.vault.clock import FixedClock

from .ledgerkit import (
    REVIEW_COL,
    SENT_ON,
    Draft,
    approve,
    draft_for,
    edit_cells,
    run,
    supported_outcomes,
)

pytestmark = pytest.mark.usefixtures("clean_grc_env")

TOKEN = hashlib.sha256(b"grc-test-token").hexdigest()
AUTH = {"X-GRC-Auth": TOKEN}
CANARY = "ALIBI-LEDGER-ROUTE-CANARY-91c4"
CUSTOMER = {"new_customer_name": "Acme Test Co", "confirm_new": True}
SENT_DATE = date(2026, 10, 10)


@asynccontextmanager
async def client_for(service: LedgerService | None) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(TOKEN, ledger=service)
    async with create_async_client(
        "http://127.0.0.1:8000", transport=httpx.ASGITransport(app=app)
    ) as client:
        yield client


def import_body(d: Draft, **changes: object) -> dict[str, object]:
    body: dict[str, object] = {
        "run_id": d.run_id,
        "file": d.path.name,
        "sent_on": SENT_ON,
        **CUSTOMER,
    }
    body.update(changes)
    return body


def call(service: LedgerService | None, method: str, url: str, **kwargs: object) -> httpx.Response:
    async def scenario() -> httpx.Response:
        async with client_for(service) as client:
            return await client.request(method, url, **kwargs)  # type: ignore[arg-type]

    return run(scenario())


ROUTES = [
    ("POST", "/ledger/import", {"json": {}}),
    ("GET", "/ledger/customers", {}),
    ("GET", "/ledger/promises", {}),
]


# ----- authentication and the unset ledger --------------------------------------------------------


@pytest.mark.parametrize(("method", "url", "kwargs"), ROUTES)
def test_every_ledger_route_needs_the_token(  # [NEG]
    tmp_path: Path, method: str, url: str, kwargs: dict[str, object]
) -> None:
    with draft_for(tmp_path, supported_outcomes(1)) as d:
        assert call(d.service, method, url, **kwargs).status_code == 401
        wrong = {"X-GRC-Auth": "0" * 64}
        assert call(d.service, method, url, headers=wrong, **kwargs).status_code == 401


@pytest.mark.parametrize(
    ("method", "url", "kwargs"),
    [
        (
            "POST",
            "/ledger/import",
            {"json": {"run_id": "a" * 32, "file": "x.xlsx", "sent_on": SENT_ON, **CUSTOMER}},
        ),
        ("GET", "/ledger/customers", {}),
        ("GET", "/ledger/promises", {}),
    ],
)
def test_with_no_ledger_folder_every_route_says_so(  # [NEG]
    method: str, url: str, kwargs: dict[str, object]
) -> None:
    response = call(None, method, url, headers=AUTH, **kwargs)
    assert response.status_code == 503
    assert response.json() == {"error": "ledger_root_not_configured"}


def test_a_ledger_without_an_export_folder_refuses_the_import_by_naming_the_folder(
    tmp_path: Path,
) -> None:  # [NEG]
    with draft_for(tmp_path, supported_outcomes(1)) as d:
        service = LedgerService(d.repo, None, d.clock)
        refused = call(service, "POST", "/ledger/import", headers=AUTH, json=import_body(d))
        assert refused.status_code == 503
        assert refused.json() == {"error": "export_root_not_configured"}
        # The listings need only the ledger, so they keep working.
        assert call(service, "GET", "/ledger/customers", headers=AUTH).json() == []
        assert call(service, "GET", "/ledger/promises", headers=AUTH).json()["total"] == 0


# ----- the import over HTTP -----------------------------------------------------------------------


def test_an_import_returns_counts_and_ids_and_no_question_or_answer_text(tmp_path: Path) -> None:
    with draft_for(tmp_path, supported_outcomes(3)) as d:
        edit_cells(
            d,
            {
                (2, REVIEW_COL): "approved",
                (3, REVIEW_COL): f"{CANARY} maybe",
                (4, REVIEW_COL): "rejected",
            },
        )
        response = call(d.service, "POST", "/ledger/import", headers=AUTH, json=import_body(d))
        assert response.status_code == 200
        body = response.json()
        assert body["created"] == 1 and body["created_edited"] == 0
        assert body["customer_created"] is True and len(body["promise_ids"]) == 1
        assert body["skipped"]["not_approved"] == 2 and body["skipped"]["no_answer"] == 0
        assert body["review"] == {"approved": 1, "rejected": 1, "unreviewed": 0, "other": 1}
        assert body["other_values"] == [{"text": f"{CANARY} maybe", "truncated": False, "count": 1}]
        assert "Do you encrypt" not in response.text and "Yes, answer" not in response.text
        assert "encryption-policy" not in response.text


def test_a_whole_import_refusal_is_a_422_with_a_code_and_counts_only(
    tmp_path: Path,
) -> None:  # [NEG]
    with draft_for(tmp_path, supported_outcomes(2)) as d:
        approve(d, 2, 3)
        edit_cells(d, {(2, 2): f"A different question {CANARY}?"})
        response = call(d.service, "POST", "/ledger/import", headers=AUTH, json=import_body(d))
        assert response.status_code == 422
        assert response.json() == {
            "error": "import_refused",
            "code": "question_mismatch",
            "counts": {"mismatched_rows": 1, "workbook_rows": 2, "recorded_rows": 2},
        }
        assert CANARY not in response.text


def test_the_other_refusals_have_their_own_status_and_fixed_words(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, supported_outcomes(1)) as d:
        approve(d, 2)
        missing = call(
            d.service, "POST", "/ledger/import", headers=AUTH, json=import_body(d, run_id="b" * 32)
        )
        assert (missing.status_code, missing.json()["code"]) == (422, "run_record_missing")
        future = call(
            d.service,
            "POST",
            "/ledger/import",
            headers=AUTH,
            json=import_body(d, sent_on="2099-01-01"),
        )
        assert (future.status_code, future.json()["code"]) == (422, "sent_on_in_future")
        outside = call(
            d.service, "POST", "/ledger/import", headers=AUTH, json=import_body(d, file="../x.xlsx")
        )
        assert (outside.status_code, outside.json()) == (400, {"error": "path_not_allowed"})
        gone = call(
            d.service, "POST", "/ledger/import", headers=AUTH, json=import_body(d, file="gone.xlsx")
        )
        assert (gone.status_code, gone.json()) == (404, {"error": "file_not_found"})
        unknown = call(
            d.service,
            "POST",
            "/ledger/import",
            headers=AUTH,
            json=import_body(d, new_customer_name=None, existing_customer_id="e" * 32),
        )
        assert unknown.status_code == 422
        assert unknown.json() == {"error": "ledger_refused", "code": "customer_unknown"}


def test_a_name_that_exists_is_refused_with_the_customers_id_and_never_the_name(
    tmp_path: Path,
) -> None:  # [NEG]
    with draft_for(tmp_path, supported_outcomes(2)) as d:
        approve(d, 2)
        first = call(d.service, "POST", "/ledger/import", headers=AUTH, json=import_body(d))
        approve(d, 3)
        again = call(
            d.service,
            "POST",
            "/ledger/import",
            headers=AUTH,
            json=import_body(d, new_customer_name="acme test co"),
        )
        assert again.status_code == 422
        assert again.json() == {
            "error": "ledger_refused",
            "code": "customer_exists",
            "customer_id": first.json()["customer_id"],
        }


@pytest.mark.parametrize(
    "changes",
    [
        {"url": "http://example.invalid"},  # no URL, token, credential or host field exists
        {"token": "x"},
        {"credential": "x"},
        {"host": "x"},
        {"run_id": "not-hex"},
        {"run_id": "a" * 7},
        {"sent_on": "10/10/2026"},
        {"file": ""},
        {"confirm_new": "maybe"},
        {"existing_customer_id": "not hex"},
    ],
)
def test_a_malformed_request_is_rejected_without_echoing_a_value(  # [NEG]
    tmp_path: Path, changes: dict[str, object]
) -> None:
    with draft_for(tmp_path, supported_outcomes(1)) as d:
        response = call(
            d.service, "POST", "/ledger/import", headers=AUTH, json=import_body(d, **changes)
        )
        assert response.status_code == 422 and response.json()["error"] == "invalid_request"
        assert "example.invalid" not in response.text and "not-hex" not in response.text


def test_a_canary_in_an_unknown_field_is_not_echoed(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, supported_outcomes(1)) as d:
        response = call(
            d.service, "POST", "/ledger/import", headers=AUTH, json=import_body(d, extra=CANARY)
        )
        assert response.status_code == 422 and CANARY not in response.text


# ----- the listings -------------------------------------------------------------------------------


def test_the_listings_show_customers_and_promises(tmp_path: Path) -> None:
    with draft_for(tmp_path, supported_outcomes(2)) as d:
        approve(d, 2, 3)
        imported = call(
            d.service, "POST", "/ledger/import", headers=AUTH, json=import_body(d)
        ).json()
        customers = call(d.service, "GET", "/ledger/customers", headers=AUTH).json()
        assert customers == [
            {"customer_id": imported["customer_id"], "display_name": "Acme Test Co"}
        ]
        page = call(d.service, "GET", "/ledger/promises", headers=AUTH).json()
        assert (page["page"], page["page_size"], page["total"]) == (1, PAGE_SIZE, 2)
        first = page["promises"][0]
        assert first["customer_id"] == imported["customer_id"] and first["voided"] is False
        assert first["status"] == "SUPPORTED" and first["edited_after_draft"] is False
        assert (first["start_line"], first["end_line"]) == (12, 13)
        assert first["sent_on"] == SENT_ON and first["run_row_index"] == 2
        assert "cited_lines_sha256" not in first and "import_workbook_sha256" not in first
        filtered = call(
            d.service,
            "GET",
            "/ledger/promises",
            headers=AUTH,
            params={"customer_id": "a" * 32},
        ).json()
        assert filtered["total"] == 0


def test_a_voided_promise_is_left_out_unless_asked_for(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, supported_outcomes(2)) as d:
        approve(d, 2, 3)
        call(d.service, "POST", "/ledger/import", headers=AUTH, json=import_body(d))
        first, second = run(d.repo.list_promises())
        run(
            d.repo.add_event(
                NewPromiseEvent(
                    promise_id=first.promise_id, kind="voided", detail_code="wrong_customer"
                )
            )
        )
        shown = call(d.service, "GET", "/ledger/promises", headers=AUTH).json()
        assert shown["total"] == 1
        assert [p["promise_id"] for p in shown["promises"]] == [second.promise_id]
        everything = call(
            d.service, "GET", "/ledger/promises", headers=AUTH, params={"include_voided": "true"}
        ).json()
        assert everything["total"] == 2
        assert {p["promise_id"]: p["voided"] for p in everything["promises"]} == {
            first.promise_id: True,
            second.promise_id: False,
        }


def test_a_page_holds_at_most_one_hundred_promises(tmp_path: Path) -> None:
    with draft_for(tmp_path, supported_outcomes(1)) as d:
        approve(d, 2)
        imported = call(
            d.service, "POST", "/ledger/import", headers=AUTH, json=import_body(d)
        ).json()
        [seed] = run(d.repo.list_promises())
        for extra in range(PAGE_SIZE):  # one more than a page, with the imported one
            run(
                d.repo.add_promise(
                    NewPromise(
                        **{
                            **seed.model_dump(exclude={"promise_id", "created_on", "row_sha256"}),
                            "run_row_index": 100 + extra,
                        }
                    )
                )
            )
        assert imported["created"] == 1
        one = call(d.service, "GET", "/ledger/promises", headers=AUTH).json()
        assert (one["total"], len(one["promises"])) == (PAGE_SIZE + 1, PAGE_SIZE)
        two = call(d.service, "GET", "/ledger/promises", headers=AUTH, params={"page": 2}).json()
        assert len(two["promises"]) == 1 and two["page"] == 2
        three = call(d.service, "GET", "/ledger/promises", headers=AUTH, params={"page": 3}).json()
        assert three["promises"] == []
        bad = call(d.service, "GET", "/ledger/promises", headers=AUTH, params={"page": 0})
        assert bad.status_code == 422


# ----- the wiring from the settings ---------------------------------------------------------------


def settings_with(tmp_path: Path, **folders: Path | None) -> Settings:
    return Settings(log_dir=tmp_path / "logs", **folders)  # type: ignore[arg-type]


def test_there_is_no_service_without_a_ledger_folder(tmp_path: Path) -> None:
    service, closer = build_ledger_service(settings_with(tmp_path), None, FixedClock(SENT_DATE))
    assert service is None and closer is None


def test_a_ledger_folder_alone_gives_a_service_that_has_no_export_folder(tmp_path: Path) -> None:
    settings = settings_with(tmp_path, ledger_dir=tmp_path / "ledger")
    service, closer = build_ledger_service(settings, None, FixedClock(SENT_DATE))
    assert service is not None and closer is not None
    assert service._export_root is None
    assert not (tmp_path / "ledger").exists()  # nothing is created until the ledger is used
    run(closer())


def test_the_repository_the_runtime_already_built_is_reused_and_not_closed_here(
    tmp_path: Path,
) -> None:
    (tmp_path / "d").mkdir()
    with draft_for(tmp_path / "d", supported_outcomes(1)) as d:
        settings = settings_with(tmp_path, ledger_dir=tmp_path / "ledger")
        service, closer = build_ledger_service(settings, d.repo, FixedClock(SENT_DATE))
        assert service is not None and closer is None
        assert service._repo is d.repo
