"""The recall, void and acknowledge routes (ADR-017 decisions 13, 17 and 20): authenticated, typed
refusals with fixed words, no free text anywhere, and the vault folder named when it is missing."""

import hashlib
from contextlib import AbstractContextManager
from datetime import date
from pathlib import Path

import pytest

from grc_sidecar.ledger import service as service_module
from grc_sidecar.ledger.recall import SCOPE, STATEMENT
from grc_sidecar.ledger.service import LedgerService
from grc_sidecar.repositories.sqlite_ledger_repo import SqliteLedgerRepository
from grc_sidecar.vault.clock import FixedClock

from .ledgerkit import answered, approve, draft_for, real_citation, run, supported_outcomes
from .test_ledger_recall import (
    BASE,
    HEADING,
    KEYS,
    POLICY,
    QUOTE,
    Scenario,
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
    ("POST", f"/ledger/promises/{SOME_ID}/acknowledge", {"json": {"seen_recall_event_id": 1}}),
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
        seen = recall_event_id(s)
        ack = call(no_vault, "POST", ack_url(pid), headers=AUTH, json=seen_body(seen))
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


def ack_url(promise_id: str) -> str:
    return f"/ledger/promises/{promise_id}/acknowledge"


def seen_body(seen: object) -> dict[str, object]:
    return {"seen_recall_event_id": seen}


def recall_event_id(s: Scenario) -> int:
    """Run recall and return the id of the recall event the owner would be looking at."""
    items = call(s.d.service, "POST", "/ledger/recall", headers=AUTH, json={}).json()["items"]
    seen = items[0]["recall_event_id"]
    assert isinstance(seen, int)
    return seen


def test_acknowledge_appends_one_event_carrying_the_state_the_owner_saw(tmp_path: Path) -> None:
    with scenario(tmp_path) as s:
        [pid] = s.promise_ids()
        seen = recall_event_id(s)
        ok = call(s.d.service, "POST", ack_url(pid), headers=AUTH, json=seen_body(seen))
        assert ok.status_code == 200 and ok.json()["kind"] == "acknowledged"
        assert set(ok.json()) == {"promise_id", "event_id", "kind"}
        [*_, stored] = run(s.d.repo.list_events(pid))
        assert (stored.kind, stored.state, stored.event_id) == (
            "acknowledged",
            "intact",
            ok.json()["event_id"],
        )
        assert [e.kind for e in run(s.d.repo.list_events(pid))] == [
            "created",
            "recall_state_changed",
            "acknowledged",
        ]


def test_a_stale_acknowledgement_is_a_422_with_a_fixed_word_and_the_ledger_is_unchanged(
    tmp_path: Path,
) -> None:  # [NEG]
    with scenario(tmp_path) as s:
        [pid] = s.promise_ids()
        seen = recall_event_id(s)
        set_text(s.vault, POLICY, policy_text(HEADING, "Gone.", KEYS))
        recall_event_id(s)  # a newer recall event now exists
        ledger_file = tmp_path / "ledger" / "ledger.sqlite3"
        before = hashlib.sha256(ledger_file.read_bytes()).hexdigest()
        stale = call(s.d.service, "POST", ack_url(pid), headers=AUTH, json=seen_body(seen))
        assert stale.status_code == 422
        assert stale.json() == {"error": "recall_refused", "code": "acknowledge_stale"}
        assert hashlib.sha256(ledger_file.read_bytes()).hexdigest() == before
        assert [e.kind for e in run(s.d.repo.list_events(pid))] == [
            "created",
            "recall_state_changed",
            "recall_state_changed",
        ]


def test_acknowledging_before_any_recall_is_stale(tmp_path: Path) -> None:  # [NEG]
    with scenario(tmp_path) as s:
        [pid] = s.promise_ids()
        stale = call(s.d.service, "POST", ack_url(pid), headers=AUTH, json=seen_body(1))
        assert stale.json() == {"error": "recall_refused", "code": "acknowledge_stale"}
        assert [e.kind for e in run(s.d.repo.list_events(pid))] == ["created"]


@pytest.mark.parametrize(
    "body",
    [
        None,  # no body at all: the field is required
        {},
        {"seen_recall_event_id": 0},
        {"seen_recall_event_id": -3},
        {"seen_recall_event_id": "1"},
        {"seen_recall_event_id": True},
        {"seen_recall_event_id": 1.5},
        {"seen_recall_event_id": None},
        {"seen_recall_event_id": 2**63},  # larger than any event id SQLite can hold
        {"seen_recall_event_id": 1, "note": CANARY},
        {"seen_recall_event_id": 1, "url": "http://example.invalid"},
    ],
)
def test_a_malformed_acknowledge_body_is_a_422_that_echoes_nothing_and_writes_nothing(  # [NEG]
    tmp_path: Path, body: dict[str, object] | None
) -> None:
    with scenario(tmp_path) as s:
        [pid] = s.promise_ids()
        kwargs: dict[str, object] = {} if body is None else {"json": body}
        refused = call(s.d.service, "POST", ack_url(pid), headers=AUTH, **kwargs)
        assert refused.status_code == 422 and refused.json()["error"] == "invalid_request"
        assert CANARY not in refused.text and "example.invalid" not in refused.text
        assert [e.kind for e in run(s.d.repo.list_events(pid))] == ["created"]


def test_acknowledge_refuses_an_unknown_and_a_voided_promise(tmp_path: Path) -> None:  # [NEG]
    with scenario(tmp_path) as s:
        [pid] = s.promise_ids()
        seen = recall_event_id(s)
        unknown = call(s.d.service, "POST", ack_url(SOME_ID), headers=AUTH, json=seen_body(seen))
        assert unknown.json() == {"error": "ledger_refused", "code": "promise_unknown"}
        call(
            s.d.service,
            "POST",
            f"/ledger/promises/{pid}/void",
            headers=AUTH,
            json={"reason": "other"},
        )
        voided = call(s.d.service, "POST", ack_url(pid), headers=AUTH, json=seen_body(seen))
        assert voided.json() == {"error": "recall_refused", "code": "promise_voided"}


def test_recall_and_the_listing_carry_the_acknowledgement_fields(tmp_path: Path) -> None:
    with scenario(tmp_path) as s:
        [pid] = s.promise_ids()
        first = call(s.d.service, "POST", "/ledger/recall", headers=AUTH, json={}).json()["items"][
            0
        ]
        assert first["acknowledged_event_id"] is None and first["acknowledgement_current"] is False
        assert first["acknowledged_state"] is None
        ack = call(
            s.d.service,
            "POST",
            ack_url(pid),
            headers=AUTH,
            json=seen_body(first["recall_event_id"]),
        ).json()
        listed = call(s.d.service, "GET", "/ledger/promises", headers=AUTH).json()["promises"][0]
        assert listed["recall_event_id"] == first["recall_event_id"]
        assert listed["acknowledged_event_id"] == ack["event_id"]
        assert listed["acknowledged_state"] == "intact"
        assert listed["acknowledgement_current"] is True
        set_text(s.vault, POLICY, policy_text(HEADING, "Gone.", KEYS))
        later = call(s.d.service, "POST", "/ledger/recall", headers=AUTH, json={}).json()["items"][
            0
        ]
        assert later["recall_event_id"] > ack["event_id"]
        assert later["acknowledged_event_id"] == ack["event_id"]
        assert later["acknowledged_state"] == "intact" and later["state"] == "changed"
        assert later["acknowledgement_current"] is False
        again = call(s.d.service, "GET", "/ledger/promises", headers=AUTH).json()["promises"][0]
        assert again["acknowledgement_current"] is False


# ----- cited paths (chunk 65b) --------------------------------------------------------------------

OTHER_POLICY = "policies/backup-policy.md"
BACKUP_LINE = "Backups are daily."
CITED = "/ledger/cited-paths"


def three_cites(tmp_path: Path) -> AbstractContextManager[Scenario]:
    files = {POLICY: BASE, OTHER_POLICY: policy_text(HEADING, BACKUP_LINE, KEYS)}
    cites = [(POLICY, QUOTE), (OTHER_POLICY, BACKUP_LINE), (POLICY, QUOTE)]
    return scenario(tmp_path, files=files, cites=cites)


def test_cited_paths_lists_each_live_path_once_with_its_count_and_nothing_else(
    tmp_path: Path,
) -> None:
    with three_cites(tmp_path) as s:
        response = call(s.d.service, "GET", CITED, headers=AUTH)
        assert response.status_code == 200
        assert response.json() == {
            "paths": [
                {"source_path": OTHER_POLICY, "promise_count": 1},
                {"source_path": POLICY, "promise_count": 2},
            ]
        }
        assert "Do you encrypt" not in response.text and "Yes, answer" not in response.text


def test_cited_paths_leaves_out_voided_promises(tmp_path: Path) -> None:  # [NEG]
    with three_cites(tmp_path) as s:
        first, second, third = s.promise_ids()
        void = {"reason": "other"}
        call(s.d.service, "POST", f"/ledger/promises/{second}/void", headers=AUTH, json=void)
        shown = call(s.d.service, "GET", CITED, headers=AUTH).json()["paths"]
        assert shown == [{"source_path": POLICY, "promise_count": 2}]  # the only backup is voided
        call(s.d.service, "POST", f"/ledger/promises/{first}/void", headers=AUTH, json=void)
        shown = call(s.d.service, "GET", CITED, headers=AUTH).json()["paths"]
        assert shown == [{"source_path": POLICY, "promise_count": 1}]  # counts live ones only
        call(s.d.service, "POST", f"/ledger/promises/{third}/void", headers=AUTH, json=void)
        assert call(s.d.service, "GET", CITED, headers=AUTH).json() == {"paths": []}


def test_cited_paths_keeps_the_stored_spelling_and_reads_the_vault_not_at_all(
    tmp_path: Path,
) -> None:
    with three_cites(tmp_path) as s:
        s.file.unlink()  # the vault file is gone; the stored path is still what was promised
        paths = call(s.d.service, "GET", CITED, headers=AUTH).json()["paths"]
        assert [p["source_path"] for p in paths] == [OTHER_POLICY, POLICY]


def test_cited_paths_refuses_the_whole_list_above_the_cap_with_counts_only(  # [NEG]
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with three_cites(tmp_path) as s:
        monkeypatch.setattr(service_module, "MAX_CITED_PATHS", 1)
        refused = call(s.d.service, "GET", CITED, headers=AUTH)
        assert refused.status_code == 422
        assert refused.json() == {
            "error": "recall_refused",
            "code": "too_many_paths",
            "counts": {"paths": 2, "cap": 1},
        }
        monkeypatch.setattr(service_module, "MAX_CITED_PATHS", 2)  # exactly at the cap is fine
        assert len(call(s.d.service, "GET", CITED, headers=AUTH).json()["paths"]) == 2


def test_cited_paths_counts_distinct_paths_not_promises_against_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with three_cites(tmp_path) as s:
        monkeypatch.setattr(service_module, "MAX_CITED_PATHS", 2)  # three promises, two paths
        assert call(s.d.service, "GET", CITED, headers=AUTH).status_code == 200


def test_cited_paths_writes_nothing_and_takes_no_query_or_body(tmp_path: Path) -> None:  # [NEG]
    with three_cites(tmp_path) as s:
        ledger_file = tmp_path / "ledger" / "ledger.sqlite3"
        before = hashlib.sha256(ledger_file.read_bytes()).hexdigest()
        call(s.d.service, "GET", CITED, headers=AUTH, params={"path": CANARY})
        assert hashlib.sha256(ledger_file.read_bytes()).hexdigest() == before
        assert call(s.d.service, "POST", CITED, headers=AUTH).status_code == 405


def test_cited_paths_does_not_create_the_ledger_file_or_its_folder(tmp_path: Path) -> None:  # [NEG]
    clock = FixedClock(date(2026, 10, 10))
    folder = tmp_path / "ledger"
    folder.mkdir()
    for ledger_folder in (folder, tmp_path / "not-made-yet"):
        repo = SqliteLedgerRepository(ledger_folder / "ledger.sqlite3", clock)
        service = LedgerService(repo, None, clock)
        for _ in range(2):  # asking again changes nothing either
            response = call(service, "GET", CITED, headers=AUTH)
            assert response.status_code == 200 and response.json() == {"paths": []}
        run(repo.close())
    assert list(folder.iterdir()) == []  # no ledger file, no journal
    assert not (tmp_path / "not-made-yet").exists()  # not even the folder


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
