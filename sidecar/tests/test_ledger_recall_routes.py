"""The recall, void and acknowledge routes (ADR-017 decisions 13, 17 and 20): authenticated, typed
refusals with fixed words, no free text anywhere, and the vault folder named when it is missing."""

from pathlib import Path

import pytest

from grc_sidecar.ledger import service as service_module
from grc_sidecar.ledger.recall import SCOPE, STATEMENT
from grc_sidecar.ledger.service import LedgerService

from .ledgerkit import answered, approve, draft_for, real_citation, run, supported_outcomes
from .test_ledger_recall import (
    BASE,
    HEADING,
    KEYS,
    POLICY,
    QUOTE,
    policy_text,
    scenario,
    set_text,
)
from .test_ledger_routes import AUTH, call, import_body

pytestmark = pytest.mark.usefixtures("clean_grc_env")

CANARY = "ALIBI-RECALL-ROUTE-CANARY-7a21"
SOME_ID = "00000000-0000-4000-8000-000000000000"
REASONS = ("wrong_customer", "imported_in_error", "not_actually_sent", "other")

NEW_ROUTES = [
    ("POST", "/ledger/recall", {"json": {}}),
    ("POST", f"/ledger/promises/{SOME_ID}/void", {"json": {"reason": "other"}}),
    ("POST", f"/ledger/promises/{SOME_ID}/acknowledge", {}),
]


@pytest.mark.parametrize(("method", "url", "kwargs"), NEW_ROUTES)
def test_every_new_route_needs_the_token(  # [NEG]
    tmp_path: Path, method: str, url: str, kwargs: dict[str, object]
) -> None:
    with scenario(tmp_path) as s:
        assert call(s.d.service, method, url, **kwargs).status_code == 401
        wrong = {"X-GRC-Auth": "0" * 64}
        assert call(s.d.service, method, url, headers=wrong, **kwargs).status_code == 401


@pytest.mark.parametrize(("method", "url", "kwargs"), NEW_ROUTES)
def test_with_no_ledger_folder_every_new_route_says_so(  # [NEG]
    method: str, url: str, kwargs: dict[str, object]
) -> None:
    response = call(None, method, url, headers=AUTH, **kwargs)
    assert response.status_code == 503
    assert response.json() == {"error": "ledger_root_not_configured"}


def test_recall_without_a_vault_folder_names_the_vault_never_the_ledger(
    tmp_path: Path,
) -> None:  # [NEG]
    with scenario(tmp_path) as s:
        no_vault = LedgerService(s.d.repo, s.d.export_root, s.d.clock)
        response = call(no_vault, "POST", "/ledger/recall", headers=AUTH, json={})
        assert response.status_code == 503
        assert response.json() == {"error": "vault_root_not_configured"}
        # Void and acknowledge need no vault, so they still work.
        [pid] = s.promise_ids()
        ack = call(no_vault, "POST", f"/ledger/promises/{pid}/acknowledge", headers=AUTH)
        assert ack.status_code == 200 and ack.json()["kind"] == "acknowledged"


# ----- recall -------------------------------------------------------------------------------------


def test_recall_returns_codes_lines_and_the_fixed_statement_on_every_item(tmp_path: Path) -> None:
    with scenario(tmp_path, cites=[(POLICY, QUOTE)] * 2) as s:
        first = call(s.d.service, "POST", "/ledger/recall", headers=AUTH, json={})
        assert first.status_code == 200
        body = first.json()
        assert body["checked"] == 2 and body["events_recorded"] == 2
        assert body["counts"] == {"intact": 2, "moved": 0, "changed": 0, "missing": 0}
        assert body["statement"] == STATEMENT and body["scope"] == SCOPE
        assert all(i["state"] == "intact" and i["statement"] == STATEMENT for i in body["items"])
        assert all(i["scope"] == SCOPE and i["previous_state"] is None for i in body["items"])
        assert "Do you encrypt" not in first.text and "Yes, answer" not in first.text

        set_text(s.vault, POLICY, policy_text(HEADING, "Gone.", QUOTE, "Middle.", QUOTE))
        moved = call(s.d.service, "POST", "/ledger/recall", headers=AUTH, json={}).json()
        assert moved["events_recorded"] == 2
        item = moved["items"][0]
        assert item["state"] == "moved" and item["previous_state"] == "intact"
        assert item["locations"] == [
            {"start_line": 9, "end_line": 9},
            {"start_line": 11, "end_line": 11},
        ]
        assert item["statement"] == STATEMENT

        again = call(s.d.service, "POST", "/ledger/recall", headers=AUTH, json={}).json()
        assert again["events_recorded"] == 0  # asking again appends nothing


def test_recall_flags_and_missing_details_come_through(tmp_path: Path) -> None:
    with scenario(tmp_path) as s:
        set_text(s.vault, POLICY, BASE.replace("review_by: 2030-01-01", "review_by: 2020-01-01"))
        flagged = call(s.d.service, "POST", "/ledger/recall", headers=AUTH, json={}).json()[
            "items"
        ][0]
        assert flagged["state"] == "intact"
        assert flagged["flags"] == ["document_changed_elsewhere", "review_overdue"]
        s.file.unlink()
        gone = call(s.d.service, "POST", "/ledger/recall", headers=AUTH, json={}).json()["items"][0]
        assert (gone["state"], gone["detail"], gone["flags"]) == ("missing", "not_found", [])
        assert gone["statement"] == STATEMENT


def test_recall_takes_no_body_an_empty_body_or_a_customer(tmp_path: Path) -> None:
    with scenario(tmp_path) as s:
        assert call(s.d.service, "POST", "/ledger/recall", headers=AUTH).status_code == 200
        assert call(s.d.service, "POST", "/ledger/recall", headers=AUTH, json={}).status_code == 200
        mine = {"customer_id": s.imported.customer_id}
        assert (
            call(s.d.service, "POST", "/ledger/recall", headers=AUTH, json=mine).json()["checked"]
            == 1
        )
        other = {"customer_id": "a" * 32}
        assert (
            call(s.d.service, "POST", "/ledger/recall", headers=AUTH, json=other).json()["checked"]
            == 0
        )


@pytest.mark.parametrize(
    "body",
    [
        {"url": "http://example.invalid"},
        {"token": "x"},
        {"host": "x"},
        {"customer_id": "not hex"},
        {"extra": CANARY},
    ],
)
def test_a_malformed_recall_request_is_rejected_without_echoing_a_value(  # [NEG]
    tmp_path: Path, body: dict[str, object]
) -> None:
    with scenario(tmp_path) as s:
        response = call(s.d.service, "POST", "/ledger/recall", headers=AUTH, json=body)
        assert response.status_code == 422 and response.json()["error"] == "invalid_request"
        assert "example.invalid" not in response.text and CANARY not in response.text
        assert "not hex" not in response.text


def test_too_many_promises_is_a_typed_refusal_with_counts_only(  # [NEG]
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with scenario(tmp_path, cites=[(POLICY, QUOTE)] * 2) as s:
        monkeypatch.setattr(service_module, "MAX_RECALL_PROMISES", 1)
        response = call(s.d.service, "POST", "/ledger/recall", headers=AUTH, json={})
        assert response.status_code == 422
        assert response.json() == {
            "error": "recall_refused",
            "code": "too_many_promises",
            "counts": {"promises": 2, "cap": 1},
        }


# ----- void ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("reason", REASONS)
def test_void_takes_each_fixed_reason_and_the_promise_leaves_the_listing(
    tmp_path: Path, reason: str
) -> None:
    with scenario(tmp_path, cites=[(POLICY, QUOTE)] * 2) as s:
        first, second = s.promise_ids()
        response = call(
            s.d.service,
            "POST",
            f"/ledger/promises/{first}/void",
            headers=AUTH,
            json={"reason": reason},
        )
        assert response.status_code == 200
        assert response.json()["promise_id"] == first and response.json()["kind"] == "voided"
        listed = call(s.d.service, "GET", "/ledger/promises", headers=AUTH).json()
        assert [p["promise_id"] for p in listed["promises"]] == [second]
        everything = call(
            s.d.service, "GET", "/ledger/promises", headers=AUTH, params={"include_voided": "true"}
        ).json()
        assert {p["promise_id"]: p["voided"] for p in everything["promises"]} == {
            first: True,
            second: False,
        }
        events = run(s.d.repo.list_events(first))
        assert [(e.kind, e.detail_code) for e in events] == [("created", None), ("voided", reason)]


@pytest.mark.parametrize(
    "body",
    [
        {"reason": f"I sent this to the wrong person {CANARY}"},  # free text
        {"reason": "Other"},  # not one of the four words exactly
        {"reason": ""},
        {"reason": None},
        {},
        {"reason": "other", "note": CANARY},
        {"reason": "other", "url": "http://example.invalid"},
    ],
)
def test_void_refuses_free_text_and_unknown_fields_and_echoes_nothing(  # [NEG]
    tmp_path: Path, body: dict[str, object]
) -> None:
    with scenario(tmp_path) as s:
        [pid] = s.promise_ids()
        response = call(
            s.d.service, "POST", f"/ledger/promises/{pid}/void", headers=AUTH, json=body
        )
        assert response.status_code == 422 and response.json()["error"] == "invalid_request"
        assert CANARY not in response.text and "example.invalid" not in response.text
        assert [e.kind for e in run(s.d.repo.list_events(pid))] == ["created"]  # nothing appended


def test_void_refuses_an_unknown_promise_a_second_void_and_a_malformed_id(  # [NEG]
    tmp_path: Path,
) -> None:
    with scenario(tmp_path) as s:
        [pid] = s.promise_ids()
        unknown = call(
            s.d.service,
            "POST",
            f"/ledger/promises/{SOME_ID}/void",
            headers=AUTH,
            json={"reason": "other"},
        )
        assert unknown.status_code == 422
        assert unknown.json() == {"error": "ledger_refused", "code": "promise_unknown"}
        malformed = call(
            s.d.service,
            "POST",
            "/ledger/promises/not-an-id/void",
            headers=AUTH,
            json={"reason": "other"},
        )
        assert malformed.status_code == 422 and malformed.json()["error"] == "invalid_request"
        url = f"/ledger/promises/{pid}/void"
        assert (
            call(s.d.service, "POST", url, headers=AUTH, json={"reason": "other"}).status_code
            == 200
        )
        second = call(s.d.service, "POST", url, headers=AUTH, json={"reason": "other"})
        assert second.status_code == 422
        assert second.json() == {"error": "recall_refused", "code": "promise_voided"}
        assert len(run(s.d.repo.list_events(pid))) == 2


# ----- acknowledge --------------------------------------------------------------------------------


def test_acknowledge_appends_one_event_and_takes_no_fields(tmp_path: Path) -> None:
    with scenario(tmp_path) as s:
        [pid] = s.promise_ids()
        url = f"/ledger/promises/{pid}/acknowledge"
        ok = call(s.d.service, "POST", url, headers=AUTH)
        assert ok.status_code == 200 and ok.json()["kind"] == "acknowledged"
        assert call(s.d.service, "POST", url, headers=AUTH, json={}).status_code == 200
        refused = call(s.d.service, "POST", url, headers=AUTH, json={"note": CANARY})
        assert refused.status_code == 422 and CANARY not in refused.text
        assert [e.kind for e in run(s.d.repo.list_events(pid))] == [
            "created",
            "acknowledged",
            "acknowledged",
        ]


def test_acknowledge_refuses_an_unknown_and_a_voided_promise(tmp_path: Path) -> None:  # [NEG]
    with scenario(tmp_path) as s:
        [pid] = s.promise_ids()
        unknown = call(s.d.service, "POST", f"/ledger/promises/{SOME_ID}/acknowledge", headers=AUTH)
        assert unknown.json() == {"error": "ledger_refused", "code": "promise_unknown"}
        call(
            s.d.service,
            "POST",
            f"/ledger/promises/{pid}/void",
            headers=AUTH,
            json={"reason": "other"},
        )
        voided = call(s.d.service, "POST", f"/ledger/promises/{pid}/acknowledge", headers=AUTH)
        assert voided.json() == {"error": "recall_refused", "code": "promise_voided"}


# ----- the import's information count -------------------------------------------------------------


def test_the_import_response_carries_the_information_count_or_none(tmp_path: Path) -> None:
    with scenario(tmp_path) as s:
        assert s.imported.already_changed_since_draft == 0
    plain = tmp_path / "plain"
    plain.mkdir()
    with draft_for(plain, supported_outcomes(1)) as d:  # no vault folder configured
        approve(d, 2)
        response = call(d.service, "POST", "/ledger/import", headers=AUTH, json=import_body(d))
        assert response.status_code == 200
        assert response.json()["already_changed_since_draft"] is None
    changed = tmp_path / "changed"
    changed.mkdir()
    vault = changed / "vault"
    set_text(vault, POLICY, BASE)
    cite = real_citation(vault, POLICY, QUOTE)
    with draft_for(changed, [answered("Yes.", cite=cite)], vault=vault) as d:
        set_text(vault, POLICY, policy_text(HEADING, "Gone.", KEYS))
        approve(d, 2)
        body = call(d.service, "POST", "/ledger/import", headers=AUTH, json=import_body(d)).json()
        assert body["created"] == 1 and body["already_changed_since_draft"] == 1
