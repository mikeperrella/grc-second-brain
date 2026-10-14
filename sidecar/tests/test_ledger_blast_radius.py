"""Blast radius (ADR-018): a preview of what saving or deleting one vault file would do to the
promises that cite it. Promises only; nothing is saved or recorded.

Offline. Real vault files in `tmp_path`, a real ledger, the real read function, the real routes.
"""

import logging
from pathlib import Path

import httpx
import pytest

from grc_sidecar.errors import BlastRadiusRefusedError
from grc_sidecar.ledger import service as service_module
from grc_sidecar.ledger.recall import SCOPE, STATEMENT
from grc_sidecar.ledger.service import NO_MATCH_LINE, PREVIEW_LINE, LedgerService
from grc_sidecar.vault.normalize import MAX_FILE_BYTES

from .ledgerkit import run
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
from .test_ledger_routes import AUTH, call

pytestmark = pytest.mark.usefixtures("clean_grc_env")

CANARY = "ALIBI-BLAST-CANARY-5d09"
OTHER = "policies/other-policy.md"
URL = "/ledger/blast-radius"
ZERO_COUNTS = {"intact": 0, "moved": 0, "changed": 0, "missing": 0}
GONE = "Data at rest is not encrypted."


def blast(s: Scenario, **body: object) -> httpx.Response:
    return call(s.d.service, "POST", URL, headers=AUTH, json={"path": POLICY, **body})


def ok(s: Scenario, **body: object) -> dict[str, object]:
    response = blast(s, **body)
    assert response.status_code == 200, response.text
    found: dict[str, object] = response.json()
    return found


def only_item(report: dict[str, object]) -> dict[str, object]:
    items = report["items"]
    assert isinstance(items, list) and len(items) == 1
    item: dict[str, object] = items[0]
    return item


def tree(root: Path) -> dict[str, bytes]:
    """Every file under `root` with its bytes: a new, removed or changed file shows up."""
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def events(s: Scenario) -> list[object]:
    return [run(s.d.repo.list_events(pid)) for pid in s.promise_ids()]


# ----- the states -----------------------------------------------------------------------------


def test_intact_to_changed_when_the_quote_would_be_gone(tmp_path: Path) -> None:
    with scenario(tmp_path) as s:
        report = ok(s, proposed_text=policy_text(HEADING, GONE, KEYS))
        item = only_item(report)
        assert (item["state_now"], item["state_proposed"]) == ("intact", "changed")
        assert (item["would_change"], item["proposed_detail"]) == (True, "quote_not_found")
        assert report["checked"] == 1 and report["would_change"] == 1
        assert report["counts"] == {**ZERO_COUNTS, "changed": 1}


def test_intact_to_moved_lists_every_place_the_quote_would_be(tmp_path: Path) -> None:
    with scenario(tmp_path) as s:
        item = only_item(
            ok(s, proposed_text=policy_text(HEADING, "Gone.", QUOTE, "Middle.", QUOTE))
        )
        assert (item["state_now"], item["state_proposed"]) == ("intact", "moved")
        assert item["locations"] == [
            {"start_line": 9, "end_line": 9},
            {"start_line": 11, "end_line": 11},
        ]


def test_unchanged_text_stays_unchanged(tmp_path: Path) -> None:
    with scenario(tmp_path) as s:
        report = ok(s, proposed_text=BASE)
        item = only_item(report)
        assert (item["state_now"], item["state_proposed"], item["would_change"]) == (
            "intact",
            "intact",
            False,
        )
        assert report["would_change"] == 0 and report["counts"] == {**ZERO_COUNTS, "intact": 1}


def test_changed_to_intact_when_the_proposed_text_restores_the_sentence(tmp_path: Path) -> None:
    with scenario(tmp_path) as s:
        set_text(s.vault, POLICY, policy_text(HEADING, GONE, KEYS))
        item = only_item(ok(s, proposed_text=BASE))
        assert (item["state_now"], item["state_proposed"]) == ("changed", "intact")
        assert item["would_change"] is True


def test_a_file_that_is_gone_now_would_be_intact_if_the_text_came_back(tmp_path: Path) -> None:
    with scenario(tmp_path) as s:
        s.file.unlink()
        item = only_item(ok(s, proposed_text=BASE))
        assert (item["state_now"], item["state_proposed"]) == ("missing", "intact")
        assert item["would_change"] is True and item["proposed_detail"] is None


def test_a_sentence_added_around_the_quote_is_changed_never_intact(tmp_path: Path) -> None:  # [NEG]
    with scenario(tmp_path) as s:
        item = only_item(
            ok(s, proposed_text=policy_text(HEADING, QUOTE + " Backups are out.", KEYS))
        )
        assert (item["state_proposed"], item["proposed_detail"]) == (
            "changed",
            "cited_lines_changed",
        )
        assert item["would_change"] is True


def test_the_text_is_normalized_like_an_indexed_file(tmp_path: Path) -> None:
    with scenario(tmp_path) as s:
        windows = "\ufeff" + BASE.replace("\n", "\r\n")  # a BOM and CRLF line endings
        item = only_item(ok(s, proposed_text=windows))
        assert (item["state_proposed"], item["would_change"]) == ("intact", False)


def test_every_item_and_the_report_carry_the_fixed_words(tmp_path: Path) -> None:
    with scenario(tmp_path, cites=[(POLICY, QUOTE)] * 2) as s:
        report = ok(s, proposed_text=policy_text(HEADING, GONE, KEYS))
        assert report["statement"] == STATEMENT and report["scope"] == SCOPE
        assert (
            report["preview"] == PREVIEW_LINE == "this is a preview; nothing was saved or recorded"
        )
        items = report["items"]
        assert isinstance(items, list) and len(items) == 2
        assert all(i["statement"] == STATEMENT and i["scope"] == SCOPE for i in items)
        assert all(i["preview"] == PREVIEW_LINE for i in items)
        assert report["note"] is None


def test_it_reads_the_vault_now_not_a_copy_from_the_last_call(tmp_path: Path) -> None:
    with scenario(tmp_path) as s:
        assert only_item(ok(s, proposed_text=BASE))["state_now"] == "intact"
        set_text(s.vault, POLICY, policy_text(HEADING, GONE, KEYS))
        assert only_item(ok(s, proposed_text=BASE))["state_now"] == "changed"


# ----- which promises -------------------------------------------------------------------------


def test_a_promise_for_another_file_is_not_listed(tmp_path: Path) -> None:
    with scenario(
        tmp_path, files={POLICY: BASE, OTHER: BASE}, cites=[(POLICY, QUOTE), (OTHER, QUOTE)]
    ) as s:
        assert ok(s, proposed_text=BASE)["checked"] == 1
        other = call(
            s.d.service, "POST", URL, headers=AUTH, json={"path": OTHER, "proposed_text": BASE}
        )
        assert other.json()["checked"] == 1


def test_a_voided_promise_is_left_out(tmp_path: Path) -> None:
    with scenario(tmp_path, cites=[(POLICY, QUOTE)] * 2) as s:
        first, _second = s.promise_ids()
        run(s.d.service.void(first, "other"))
        report = ok(s, proposed_text=BASE)
        assert report["checked"] == 1
        assert [i["promise_id"] for i in report["items"]] == [_second]  # type: ignore[attr-defined]


def test_the_customer_filter_applies(tmp_path: Path) -> None:
    with scenario(tmp_path) as s:
        assert ok(s, proposed_text=BASE, customer_id=s.imported.customer_id)["checked"] == 1
        assert ok(s, proposed_text=BASE, customer_id="a" * 32)["checked"] == 0


def test_the_path_matches_ignoring_letter_case(tmp_path: Path) -> None:
    with scenario(tmp_path) as s:
        shouted = POLICY.upper()
        assert shouted != POLICY
        report = call(
            s.d.service, "POST", URL, headers=AUTH, json={"path": shouted, "proposed_text": GONE}
        ).json()
        item = only_item(report)
        # The current side is the file the promise really cites, so it is judged against that file.
        assert (item["state_now"], item["state_proposed"]) == ("intact", "changed")


def test_no_live_promise_for_the_path_says_so_in_a_fixed_line(tmp_path: Path) -> None:
    with scenario(tmp_path) as s:
        report = call(
            s.d.service,
            "POST",
            URL,
            headers=AUTH,
            json={"path": "policies/nothing-cites-this.md", "proposed_text": BASE},
        ).json()
        assert report["note"] == NO_MATCH_LINE == "no live promise cites this path as written"
        assert (report["checked"], report["would_change"], report["items"]) == (0, 0, [])
        assert report["counts"] == ZERO_COUNTS
        assert report["preview"] == PREVIEW_LINE


def test_a_voided_only_path_also_says_no_live_promise(tmp_path: Path) -> None:
    with scenario(tmp_path) as s:
        [pid] = s.promise_ids()
        run(s.d.service.void(pid, "other"))
        report = ok(s, proposed_text=BASE)
        assert (report["checked"], report["note"]) == (0, NO_MATCH_LINE)


def test_above_the_cap_the_whole_call_is_refused_with_counts_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # [NEG]
    monkeypatch.setattr(service_module, "MAX_BLAST_PROMISES", 1)
    with scenario(tmp_path, cites=[(POLICY, QUOTE)] * 2) as s:
        response = blast(s, proposed_text=BASE)
        assert response.status_code == 422
        assert response.json() == {
            "error": "blast_radius_refused",
            "code": "too_many_promises",
            "counts": {"promises": 2, "cap": 1},
        }


# ----- delete mode ----------------------------------------------------------------------------


def test_delete_mode_shows_every_live_promise_as_missing_not_found(tmp_path: Path) -> None:
    with scenario(tmp_path, cites=[(POLICY, QUOTE)] * 2) as s:
        before = s.file.read_bytes()
        report = ok(s, delete_file=True)
        assert report["checked"] == 2 and report["would_change"] == 2
        assert report["counts"] == {**ZERO_COUNTS, "missing": 2}
        for item in report["items"]:  # type: ignore[attr-defined]
            assert (item["state_now"], item["state_proposed"]) == ("intact", "missing")
            assert item["proposed_detail"] == "not_found" and item["would_change"] is True
            assert item["preview"] == PREVIEW_LINE
        assert s.file.read_bytes() == before  # a preview deletes nothing


def test_deleting_a_file_that_is_already_gone_changes_nothing(tmp_path: Path) -> None:
    with scenario(tmp_path) as s:
        s.file.unlink()
        item = only_item(ok(s, delete_file=True))
        assert (item["state_now"], item["state_proposed"], item["would_change"]) == (
            "missing",
            "missing",
            False,
        )


@pytest.mark.parametrize(
    "body",
    [
        {"proposed_text": CANARY, "delete_file": True},  # both
        {},  # neither
        {"delete_file": False},  # neither, spelled out
    ],
)
def test_exactly_one_of_text_or_delete_is_required(
    tmp_path: Path, body: dict[str, object]
) -> None:  # [NEG]
    with scenario(tmp_path) as s:
        response = blast(s, **body)
        assert response.status_code == 422
        assert response.json() == {"error": "invalid_request", "fields": ["body"]}
        assert CANARY not in response.text


# ----- refusals -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/x.md",
        "C:/x.md",
        "c:x.md",
        "a\\b.md",
        "../x.md",
        "a/../b.md",
        "a/..",
        "a\x00b.md",
        "x" * 1001,
    ],
)
def test_a_path_that_is_not_vault_relative_posix_is_a_422(
    tmp_path: Path, path: str
) -> None:  # [NEG]
    with scenario(tmp_path) as s:
        response = call(
            s.d.service, "POST", URL, headers=AUTH, json={"path": path, "proposed_text": BASE}
        )
        assert response.status_code == 422
        assert response.json() == {"error": "invalid_request", "fields": ["body.path"]}


def test_a_refused_path_is_never_echoed(tmp_path: Path) -> None:  # [NEG]
    with scenario(tmp_path) as s:
        response = call(
            s.d.service,
            "POST",
            URL,
            headers=AUTH,
            json={"path": f"{CANARY}/../x.md", "proposed_text": BASE},
        )
        assert response.status_code == 422 and CANARY not in response.text


def test_text_over_the_byte_cap_is_refused_with_counts_only(tmp_path: Path) -> None:  # [NEG]
    with scenario(tmp_path) as s:
        text = CANARY + "é" * (MAX_FILE_BYTES // 2)  # under the character bound, over the byte cap
        assert len(text) <= MAX_FILE_BYTES < len(text.encode())
        response = blast(s, proposed_text=text)
        assert response.status_code == 422
        assert response.json() == {
            "error": "blast_radius_refused",
            "code": "proposed_text_too_large",
            "counts": {"bytes": len(text.encode()), "cap": MAX_FILE_BYTES},
        }
        assert CANARY not in response.text


def test_text_over_the_character_bound_is_an_invalid_request(tmp_path: Path) -> None:  # [NEG]
    with scenario(tmp_path) as s:
        response = blast(s, proposed_text="a" * (MAX_FILE_BYTES + 1))
        assert response.status_code == 422
        assert response.json() == {"error": "invalid_request", "fields": ["body.proposed_text"]}


def test_text_exactly_at_the_cap_is_checked(tmp_path: Path) -> None:
    with scenario(tmp_path) as s:
        item = only_item(ok(s, proposed_text="a" * MAX_FILE_BYTES))
        assert item["state_proposed"] == "changed"


def test_a_nul_in_the_text_is_refused(tmp_path: Path) -> None:  # [NEG]
    with scenario(tmp_path) as s:
        response = blast(s, proposed_text=f"{CANARY}\x00")
        assert response.status_code == 422
        assert response.json() == {"error": "blast_radius_refused", "code": "proposed_text_has_nul"}
        assert CANARY not in response.text


def test_text_that_is_not_utf8_encodable_is_refused_without_echo(tmp_path: Path) -> None:  # [NEG]
    with scenario(tmp_path) as s:
        with pytest.raises(BlastRadiusRefusedError) as caught:
            run(s.d.service.blast_radius(POLICY, CANARY + "\ud800", None))
        assert caught.value.code == "proposed_text_not_utf8" and CANARY not in str(caught.value)


def test_unknown_fields_are_rejected_without_echo(tmp_path: Path) -> None:  # [NEG]
    with scenario(tmp_path) as s:
        for field in ("url", "token", "credential", "host"):
            response = blast(s, proposed_text=BASE, **{field: CANARY})
            assert response.status_code == 422
            assert response.json() == {"error": "invalid_request", "fields": [f"body.{field}"]}
            assert CANARY not in response.text


# ----- the route's gates ----------------------------------------------------------------------


def test_the_route_needs_the_token(tmp_path: Path) -> None:  # [NEG]
    with scenario(tmp_path) as s:
        body = {"path": POLICY, "proposed_text": BASE}
        assert call(s.d.service, "POST", URL, json=body).status_code == 401
        wrong = {"X-GRC-Auth": "0" * 64}
        assert call(s.d.service, "POST", URL, headers=wrong, json=body).status_code == 401


def test_with_no_ledger_folder_it_says_so() -> None:  # [NEG]
    response = call(None, "POST", URL, headers=AUTH, json={"path": POLICY, "delete_file": True})
    assert response.status_code == 503
    assert response.json() == {"error": "ledger_root_not_configured"}


def test_with_no_vault_folder_it_names_the_vault(tmp_path: Path) -> None:  # [NEG]
    with scenario(tmp_path) as s:
        no_vault = LedgerService(s.d.repo, s.d.export_root, s.d.clock)
        response = call(
            no_vault, "POST", URL, headers=AUTH, json={"path": POLICY, "delete_file": True}
        )
        assert response.status_code == 503
        assert response.json() == {"error": "vault_root_not_configured"}


# ----- a preview: nothing is written, recorded, logged or echoed ------------------------------


def test_a_preview_writes_and_records_nothing(tmp_path: Path) -> None:  # [NEG]
    with scenario(tmp_path, cites=[(POLICY, QUOTE)] * 2) as s:
        ledger_file = tmp_path / "ledger" / "ledger.sqlite3"
        events_before = events(s)
        ledger_before = ledger_file.read_bytes()
        vault_before = s.file.read_bytes()
        files_before = tree(tmp_path)

        for body in (
            {"proposed_text": policy_text(HEADING, GONE, KEYS)},  # a change is found
            {"proposed_text": policy_text(HEADING, "Gone.", QUOTE, "Middle.", QUOTE)},  # moved
            {"proposed_text": BASE},  # no change
            {"delete_file": True},
            {"proposed_text": CANARY + "\x00"},  # a refusal
        ):
            blast(s, **body)

        assert events(s) == events_before  # no event of any kind was appended
        assert ledger_file.read_bytes() == ledger_before
        assert s.file.read_bytes() == vault_before
        assert tree(tmp_path) == files_before  # no new, removed or changed file anywhere


def test_the_proposed_text_is_in_no_log_no_body_and_not_in_the_ledger(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:  # [NEG]
    caplog.set_level(logging.DEBUG)
    with scenario(tmp_path, cites=[(POLICY, QUOTE)] * 2) as s:
        bodies = [
            blast(s, proposed_text=policy_text(HEADING, CANARY, KEYS)),  # a change
            blast(s, proposed_text=policy_text(HEADING, CANARY, QUOTE)),  # moved
            blast(s, proposed_text=CANARY + "\x00"),  # a refusal
            blast(s, proposed_text=CANARY, delete_file=True),  # an invalid request
            blast(s, proposed_text=CANARY, path=f"{CANARY}\\x"),  # an invalid path
        ]
        assert [b.status_code for b in bodies] == [200, 200, 422, 422, 422]
        assert all(CANARY not in b.text for b in bodies)
        assert CANARY not in caplog.text
        assert all(CANARY not in str(record.args) for record in caplog.records)
        assert CANARY.encode() not in (tmp_path / "ledger" / "ledger.sqlite3").read_bytes()
