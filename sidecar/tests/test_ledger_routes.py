"""The ledger routes (ADR-017): authenticated, typed refusals, no content in a refusal body, a page
size of 100, voided promises left out unless asked for, and the service wiring from the settings."""

import hashlib
import logging
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
    ANSWER_COL,
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
    ("GET", "/ledger/cited-paths", {}),
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
        ("GET", "/ledger/cited-paths", {}),
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


# ----- preview: every check and count, nothing written (chunk 65b) -------------------------------


def ledger_bytes(tmp_path: Path) -> bytes:
    return (tmp_path / "ledger" / "ledger.sqlite3").read_bytes()


def test_a_preview_writes_nothing_and_makes_no_customer(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, supported_outcomes(2)) as d:
        approve(d, 2, 3)
        before = ledger_bytes(tmp_path)
        response = call(
            d.service, "POST", "/ledger/import", headers=AUTH, json=import_body(d, preview=True)
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["preview"] is True and body["created"] == 2  # what a real import would make
        assert body["customer_created"] is False and body["customer_id"] is None
        assert body["promise_ids"] == []  # nothing exists to point at
        assert ledger_bytes(tmp_path) == before  # not one byte of the ledger file changed
        assert call(d.service, "GET", "/ledger/customers", headers=AUTH).json() == []
        assert call(d.service, "GET", "/ledger/promises", headers=AUTH).json()["total"] == 0


def test_a_preview_counts_exactly_what_the_real_import_then_does(tmp_path: Path) -> None:
    with draft_for(tmp_path, supported_outcomes(4)) as d:
        edit_cells(
            d,
            {
                (2, REVIEW_COL): "approved",
                (3, REVIEW_COL): "approved",
                (3, ANSWER_COL): "Yes, and we edited this answer.",
                (4, REVIEW_COL): "rejected",
                (5, REVIEW_COL): f"{CANARY} maybe",
            },
        )
        previewed = call(
            d.service, "POST", "/ledger/import", headers=AUTH, json=import_body(d, preview=True)
        ).json()
        real = call(d.service, "POST", "/ledger/import", headers=AUTH, json=import_body(d)).json()
        assert previewed["preview"] is True and real["preview"] is False
        assert previewed["created"] == real["created"] == 2
        assert previewed["created_edited"] == real["created_edited"] == 1
        for key in ("skipped", "review", "other_values", "already_changed_since_draft"):
            assert previewed[key] == real[key], key
        assert len(real["promise_ids"]) == 2
        # Previewing again, for the customer that now exists, shows the rows as already imported.
        existing = {"existing_customer_id": real["customer_id"]}
        again = call(
            d.service,
            "POST",
            "/ledger/import",
            headers=AUTH,
            json=import_body(
                d, new_customer_name=None, confirm_new=False, preview=True, **existing
            ),
        ).json()
        assert again["created"] == 0 and again["skipped"]["already_imported"] == 2
        assert again["customer_id"] == real["customer_id"]


def import_for(d: Draft, customer_id: str, *, preview: bool) -> dict[str, object]:
    body = import_body(
        d, new_customer_name=None, confirm_new=False, existing_customer_id=customer_id,
        preview=preview,
    )  # fmt: skip
    response = call(d.service, "POST", "/ledger/import", headers=AUTH, json=body)
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_a_preview_counts_rows_a_real_import_would_skip_as_already_imported(  # [NEG]
    tmp_path: Path,
) -> None:
    # Over-count found at the 65b review: a row already imported for this customer must be a
    # skipped `already_imported` in the preview, never a would-create, and the preview must say
    # exactly what the real second import then does.
    with draft_for(tmp_path, supported_outcomes(4)) as d:
        approve(d, 2, 3)
        first = call(d.service, "POST", "/ledger/import", headers=AUTH, json=import_body(d)).json()
        assert first["created"] == 2
        customer_id = first["customer_id"]
        before = ledger_bytes(tmp_path)

        same = import_for(d, customer_id, preview=True)  # the very same import, again
        assert same["created"] == 0 and same["customer_created"] is False
        assert same["skipped"]["already_imported"] == first["created"]
        assert ledger_bytes(tmp_path) == before  # a preview wrote nothing
        second = import_for(d, customer_id, preview=False)  # what a real second import does
        assert second["created"] == 0 and second["skipped"]["already_imported"] == 2
        for key in ("created", "created_edited", "skipped", "review", "other_values"):
            assert same[key] == second[key], key

        approve(d, 2, 3, 4, 5)  # two more rows approved: a mix of old and new rows
        mixed = import_for(d, customer_id, preview=True)
        assert mixed["created"] == 2 and mixed["skipped"]["already_imported"] == 2
        real = import_for(d, customer_id, preview=False)
        assert real["created"] == 2 and real["skipped"]["already_imported"] == 2
        for key in ("created", "created_edited", "skipped", "review", "other_values"):
            assert mixed[key] == real[key], key


def test_a_preview_with_a_new_customer_name_has_nothing_to_find(tmp_path: Path) -> None:
    with draft_for(tmp_path, supported_outcomes(2)) as d:
        approve(d, 2, 3)
        previewed = call(
            d.service, "POST", "/ledger/import", headers=AUTH, json=import_body(d, preview=True)
        ).json()
        assert previewed["created"] == 2 and previewed["skipped"]["already_imported"] == 0


def test_a_preview_refuses_everything_a_real_import_refuses_and_writes_nothing(  # [NEG]
    tmp_path: Path,
) -> None:
    with draft_for(tmp_path, supported_outcomes(2)) as d:
        approve(d, 2)
        before = ledger_bytes(tmp_path)
        cases: list[tuple[dict[str, object], int, str]] = [
            ({"run_id": "b" * 32}, 422, "run_record_missing"),
            ({"sent_on": "2099-01-01"}, 422, "sent_on_in_future"),
            ({"sent_on": "2026-02-30"}, 422, "sent_on_invalid"),
            ({"confirm_new": False}, 422, "new_customer_not_confirmed"),
            ({"new_customer_name": None}, 422, "customer_choice_invalid"),
            ({"new_customer_name": "bad​name"}, 422, "customer_name_invalid"),
        ]
        for changes, status, code in cases:
            real = call(
                d.service, "POST", "/ledger/import", headers=AUTH, json=import_body(d, **changes)
            )
            shown = call(
                d.service,
                "POST",
                "/ledger/import",
                headers=AUTH,
                json=import_body(d, preview=True, **changes),
            )
            assert (shown.status_code, shown.json()) == (status, real.json()), changes
            assert shown.json()["code"] == code
        assert ledger_bytes(tmp_path) == before


def test_a_preview_of_a_name_that_exists_is_refused_with_the_id_not_the_name(  # [NEG]
    tmp_path: Path,
) -> None:
    with draft_for(tmp_path, supported_outcomes(2)) as d:
        approve(d, 2)
        first = call(d.service, "POST", "/ledger/import", headers=AUTH, json=import_body(d)).json()
        before = ledger_bytes(tmp_path)
        refused = call(
            d.service, "POST", "/ledger/import", headers=AUTH, json=import_body(d, preview=True)
        )
        assert refused.status_code == 422
        assert refused.json() == {
            "error": "ledger_refused",
            "code": "customer_exists",
            "customer_id": first["customer_id"],
        }
        assert ledger_bytes(tmp_path) == before


def test_a_preview_flag_must_be_a_boolean_and_other_fields_are_still_refused(  # [NEG]
    tmp_path: Path,
) -> None:
    with draft_for(tmp_path, supported_outcomes(1)) as d:
        approve(d, 2)
        before = ledger_bytes(tmp_path)
        for bad in ({"preview": "maybe"}, {"preview": [True]}, {"previews": True}, {"url": "x"}):
            refused = call(
                d.service, "POST", "/ledger/import", headers=AUTH, json=import_body(d, **bad)
            )
            assert refused.status_code == 422 and refused.json()["error"] == "invalid_request"
            assert "maybe" not in refused.text
        assert ledger_bytes(tmp_path) == before


def test_a_canary_in_a_customer_name_is_not_echoed_or_logged_by_a_preview(  # [NEG]
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    with draft_for(tmp_path, supported_outcomes(1)) as d:
        approve(d, 2)
        response = call(
            d.service,
            "POST",
            "/ledger/import",
            headers=AUTH,
            json=import_body(d, new_customer_name=f"Customer {CANARY}", preview=True),
        )
        assert response.status_code == 200 and CANARY not in response.text
        assert CANARY not in caplog.text
        assert CANARY.encode() not in ledger_bytes(tmp_path)  # and no customer was made from it


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


def five_promises(d: Draft) -> list[str]:
    """Import one promise, then add four more rows of it, so five promises exist."""
    call(d.service, "POST", "/ledger/import", headers=AUTH, json=import_body(d))
    [seed] = run(d.repo.list_promises())
    for extra in range(4):
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
    return [p.promise_id for p in run(d.repo.list_promises())]


def listing(d: Draft, **params: object) -> httpx.Response:
    return call(d.service, "GET", "/ledger/promises", headers=AUTH, params=params)  # type: ignore[arg-type]


def test_page_size_shortens_the_page_and_the_pages_still_add_up(tmp_path: Path) -> None:
    with draft_for(tmp_path, supported_outcomes(1)) as d:
        approve(d, 2)
        ids = five_promises(d)
        seen: list[str] = []
        for page, expected in ((1, 2), (2, 2), (3, 1), (4, 0)):
            body = listing(d, page=page, page_size=2).json()
            assert (body["page"], body["page_size"], body["total"]) == (page, 2, 5)
            assert len(body["promises"]) == expected
            seen += [p["promise_id"] for p in body["promises"]]
        assert seen == ids
        voided = listing(d, page=3, page_size=2, include_voided="true").json()
        assert (voided["total"], len(voided["promises"])) == (5, 1)
        assert listing(d, page_size=1).json()["promises"][0]["promise_id"] == ids[0]


def test_page_size_defaults_to_one_hundred_as_before(tmp_path: Path) -> None:
    with draft_for(tmp_path, supported_outcomes(1)) as d:
        approve(d, 2)
        five_promises(d)
        body = listing(d).json()
        assert (body["page_size"], len(body["promises"])) == (PAGE_SIZE, 5) and PAGE_SIZE == 100


def test_page_size_stays_in_the_closed_range_one_to_one_hundred(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, supported_outcomes(1)) as d:
        approve(d, 2)
        five_promises(d)
        for ok_value in (1, 100):
            assert listing(d, page_size=ok_value).status_code == 200
        for bad in (0, 101, -1, 100_000, "x", "1.5", ""):
            refused = listing(d, page_size=bad)
            assert refused.status_code == 422, bad
            assert refused.json() == {"error": "invalid_request", "fields": ["query.page_size"]}


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
