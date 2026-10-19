"""Recall (ADR-017 decisions 12, 13, 17 to 20): does the policy a promise rests on still say it?

The first half tests the pure `check_promise` on in-memory text. The second half runs the service
against real vault files in `tmp_path` (through the real read function) and a real ledger.
"""

import logging
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from grc_sidecar.errors import LedgerRefusedError, RecallRefusedError, VaultRootNotConfiguredError
from grc_sidecar.ledger import service as service_module
from grc_sidecar.ledger.recall import SCOPE, STATEMENT, RecallResult, check_promise
from grc_sidecar.ledger.service import ImportOutcome, LedgerService, RecallReport
from grc_sidecar.repositories.base import Promise
from grc_sidecar.vault.normalize import MAX_FILE_BYTES, sha256_hex, split_lines
from grc_sidecar.vault.scan import ReadReason, VaultText

from .ledgerkit import (
    Draft,
    answered,
    approve,
    draft_for,
    import_rows,
    real_citation,
    run,
    supported_outcomes,
)
from .retrievalkit import stack
from .test_ledger_store import old_style_acknowledgement
from .vaultkit import doc_text

TODAY = date(2026, 10, 10)  # the fake clock's day in `ledgerkit`
QUOTE = "Data at rest is encrypted with AES-256."
HEADING = "# Encryption"
KEYS = "Keys rotate yearly."
POLICY = "policies/encryption-policy.md"
CANARY = "ALIBI-RECALL-CANARY-3b8e"


def policy_text(*body: str, review_by: str = "2030-01-01") -> str:
    """A valid vault file. The body starts on line 7, so with the usual body the quote is line 8."""
    frontmatter = (
        "doc_id: ENC-001",
        "type: policy",
        "owner: Test Owner",
        f"review_by: {review_by}",
    )
    return doc_text("\n".join(body), *frontmatter) + "\n"


BASE = policy_text(HEADING, QUOTE, KEYS)
QUOTE_LINE = 8


def make_promise(text: str = BASE, quote: str = QUOTE, line: int = QUOTE_LINE) -> Promise:
    """A promise whose baseline is `text` as it was when drafted, with the quote on `line`."""
    lines = split_lines(text)
    return Promise(
        promise_id="11111111-2222-4333-8444-555555555555",
        customer_id="c" * 32,
        run_id="a" * 32,
        run_row_index=2,
        question_sha256="b" * 64,
        question_text="Do you encrypt data at rest?",
        answer_text="Yes.",
        status="SUPPORTED",
        source_path="nowhere/on-disk.md",  # check_promise reads nothing from disk
        doc_id="ENC-001",
        start_line=line,
        end_line=line,
        quote=quote,
        cited_lines_sha256=sha256_hex(lines[line - 1]),
        cited_file_sha256=sha256_hex(text),
        edited_after_draft=False,
        import_workbook_sha256="d" * 64,
        sent_on=TODAY,
        created_on=TODAY,
        row_sha256="e" * 64,
    )


def check(text: str, promise: Promise | None = None, today: date = TODAY) -> RecallResult:
    """`check_promise` on in-memory text, against the usual promise unless one is given."""
    return check_promise(promise or make_promise(), VaultText(text), today)


# ----- the pure function: states and detail codes ----------------------------------------------


def test_unchanged_cited_lines_are_intact_and_say_so_in_words() -> None:
    result = check(BASE)
    assert (result.state, result.detail, result.locations, result.flags) == ("intact", None, (), ())
    assert (
        result.statement
        == STATEMENT
        == (
            "intact means the cited lines are unchanged since drafting; "
            "it does not mean the promise is still true."
        )
    )
    assert "cited lines only" in result.scope and result.scope == SCOPE


def test_fix_c_a_change_elsewhere_in_the_file_is_a_flag_never_a_state() -> None:
    result = check(policy_text(HEADING, QUOTE, "Keys rotate quarterly."))
    assert result.state == "intact" and result.flags == ("document_changed_elsewhere",)


@pytest.mark.parametrize(
    "cited_line",
    [
        QUOTE + " Backups are excluded.",  # a sentence added around an unchanged quote
        "Note: " + QUOTE,
        QUOTE + " ",  # even whitespace changes the cited lines' text
        "  " + QUOTE,
    ],
)
def test_fix_a_changed_text_on_the_cited_lines_is_never_intact_even_if_the_quote_is_still_there(
    cited_line: str,
) -> None:  # [NEG]
    result = check(policy_text(HEADING, cited_line, KEYS))
    assert (result.state, result.detail) == ("changed", "cited_lines_changed")


def test_a_quote_gone_from_the_file_is_changed_with_its_own_detail() -> None:
    result = check(policy_text(HEADING, "Data at rest is not encrypted.", KEYS))
    assert (result.state, result.detail, result.locations) == ("changed", "quote_not_found", ())


def test_a_quote_at_other_lines_only_is_moved_and_names_where() -> None:
    result = check(policy_text(HEADING, "New line one.", "New line two.", QUOTE, KEYS))
    assert result.state == "moved" and result.detail is None
    assert result.locations == ((10, 10),)


def test_fix_b_moved_carries_every_location_not_the_first() -> None:
    result = check(policy_text(HEADING, "Gone.", QUOTE, "Middle.", QUOTE))
    assert result.state == "moved" and result.locations == ((9, 9), (11, 11))


def test_a_quote_at_the_same_lines_and_elsewhere_is_changed_not_moved() -> None:
    result = check(policy_text(HEADING, QUOTE + " Plus more.", "Middle.", QUOTE))
    assert (result.state, result.detail) == ("changed", "cited_lines_changed")


def test_a_file_that_got_shorter_than_the_cited_lines_does_not_match() -> None:
    result = check(
        "---\ndoc_id: ENC-001\ntype: policy\nowner: Test Owner\nreview_by: 2030-01-01\n---\n"
    )
    assert (result.state, result.detail) == ("changed", "quote_not_found")


@pytest.mark.parametrize("reason", ["not_found", "unsafe_path", "too_large", "not_utf8"])
def test_no_text_is_missing_and_the_reason_is_the_detail(reason: ReadReason) -> None:  # [NEG]
    result = check_promise(make_promise(), reason, TODAY)
    assert (result.state, result.detail, result.flags) == ("missing", reason, ())


# ----- the flags -----------------------------------------------------------------------------


def test_a_review_date_before_today_is_flagged_overdue_and_today_is_not() -> None:
    overdue = check(policy_text(HEADING, QUOTE, KEYS, review_by="2026-10-09"))
    on_the_day = check(policy_text(HEADING, QUOTE, KEYS, review_by="2026-10-10"))
    assert overdue.state == "intact" and "review_overdue" in overdue.flags
    assert "review_overdue" not in on_the_day.flags


def test_an_overdue_file_from_the_start_has_only_that_flag() -> None:
    stale = policy_text(HEADING, QUOTE, KEYS, review_by="2024-09-30")
    assert check(stale, make_promise(stale)).flags == ("review_overdue",)


def test_a_frontmatter_that_no_longer_parses_is_flagged_and_the_review_date_is_unknown() -> None:
    broken = BASE.replace("review_by: 2030-01-01", "review_by: not-a-date")
    result = check(broken)
    assert result.state == "intact"  # the cited lines did not move or change
    assert result.flags == ("document_changed_elsewhere", "frontmatter_invalid")
    assert "review_overdue" not in result.flags


def test_flags_apply_whatever_the_state_is() -> None:
    changed = policy_text(HEADING, "Data at rest is not encrypted.", KEYS, review_by="2020-01-01")
    result = check(changed)
    assert result.state == "changed" and result.flags == ("review_overdue",)


def test_every_result_carries_the_fixed_statement_and_scope() -> None:
    results = [
        check(BASE),
        check(policy_text(HEADING, QUOTE, "Other.")),
        check(policy_text(HEADING, "x", QUOTE, KEYS)),
        check(policy_text(HEADING, QUOTE + "!", KEYS)),
        check(policy_text(HEADING, "gone", KEYS)),
        *[check_promise(make_promise(), r, TODAY) for r in ("not_found", "unsafe_path")],
    ]
    assert {r.state for r in results} == {"intact", "moved", "changed", "missing"}
    assert all(r.statement == STATEMENT and r.scope == SCOPE for r in results)


# ----- the service, against real vault files and a real ledger ------------------------------------


@dataclass
class Scenario:
    d: Draft
    vault: Path
    imported: ImportOutcome

    @property
    def file(self) -> Path:
        return self.vault / POLICY

    def promise_ids(self) -> list[str]:
        return [p.promise_id for p in run(self.d.repo.list_promises())]


def set_text(vault: Path, name: str, text: str) -> None:
    target = vault / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")


@contextmanager
def scenario(
    tmp_path: Path,
    *,
    files: dict[str, str] | None = None,
    cites: list[tuple[str, str]] | None = None,
) -> Iterator[Scenario]:
    """A vault, a run whose answers cite it, and an import of every row (so promises exist)."""
    vault = tmp_path / "vault"
    for name, text in (files or {POLICY: BASE}).items():
        set_text(vault, name, text)
    wanted = cites or [(POLICY, QUOTE)]
    outcomes = [
        answered(f"Yes, answer {n}.", cite=real_citation(vault, path, quote))
        for n, (path, quote) in enumerate(wanted, start=1)
    ]
    with draft_for(tmp_path, outcomes, vault=vault) as d:
        approve(d, *range(2, len(wanted) + 2))
        imported = import_rows(d)
        assert imported.created == len(wanted)
        yield Scenario(d, vault, imported)


def kinds(s: Scenario, promise_id: str) -> list[str]:
    return [event.kind for event in run(s.d.repo.list_events(promise_id))]


def recall(s: Scenario, customer_id: str | None = None) -> RecallReport:
    return run(s.d.service.recall(customer_id))


def test_an_unchanged_vault_is_intact_and_the_first_recall_records_the_state(
    tmp_path: Path,
) -> None:
    with scenario(tmp_path) as s:
        report = recall(s)
        [item] = report.items
        assert (item.state, item.detail, item.flags, item.previous_state) == (
            "intact",
            None,
            (),
            None,
        )
        assert item.event_recorded is True and report.events_recorded == 1
        assert report.counts == {"intact": 1, "moved": 0, "changed": 0, "missing": 0}
        assert item.statement == STATEMENT and report.statement == STATEMENT
        assert item.scope == SCOPE and report.scope == SCOPE
        [pid] = s.promise_ids()
        assert kinds(s, pid) == ["created", "recall_state_changed"]
        recorded = run(s.d.repo.list_events(pid))[-1]
        assert (recorded.state, recorded.detail_code) == ("intact", None)


def test_asking_again_appends_nothing_and_a_real_change_appends_exactly_one_event(
    tmp_path: Path,
) -> None:
    with scenario(tmp_path) as s:
        [pid] = s.promise_ids()
        recall(s)
        again = recall(s)
        assert again.events_recorded == 0 and again.items[0].event_recorded is False
        assert kinds(s, pid) == ["created", "recall_state_changed"]

        set_text(s.vault, POLICY, policy_text(HEADING, "Data at rest is not encrypted.", KEYS))
        changed = recall(s)
        assert changed.items[0].state == "changed" and changed.items[0].previous_state == "intact"
        assert changed.events_recorded == 1
        assert kinds(s, pid) == ["created", "recall_state_changed", "recall_state_changed"]
        last = run(s.d.repo.list_events(pid))[-1]
        assert (last.state, last.detail_code) == ("changed", "quote_not_found")

        assert recall(s).events_recorded == 0  # a repeat of the new state records nothing
        assert len(kinds(s, pid)) == 3


def test_a_change_of_detail_inside_the_same_state_is_not_a_new_event(tmp_path: Path) -> None:
    with scenario(tmp_path) as s:
        [pid] = s.promise_ids()
        set_text(s.vault, POLICY, policy_text(HEADING, "Gone.", KEYS))
        assert recall(s).items[0].detail == "quote_not_found"
        set_text(s.vault, POLICY, policy_text(HEADING, QUOTE + " Extra.", KEYS))
        second = recall(s).items[0]
        assert (second.state, second.detail, second.event_recorded) == (
            "changed",
            "cited_lines_changed",
            False,
        )
        assert len(kinds(s, pid)) == 2  # created, and the one recall event


def test_the_vault_changing_back_is_a_new_state_and_a_new_event(tmp_path: Path) -> None:
    with scenario(tmp_path) as s:
        [pid] = s.promise_ids()
        recall(s)
        set_text(s.vault, POLICY, policy_text(HEADING, "Gone.", KEYS))
        recall(s)
        set_text(s.vault, POLICY, BASE)
        back = recall(s).items[0]
        assert (back.state, back.previous_state, back.event_recorded) == ("intact", "changed", True)
        assert len(kinds(s, pid)) == 4


def test_moved_over_the_service_lists_every_location(tmp_path: Path) -> None:
    with scenario(tmp_path) as s:
        set_text(s.vault, POLICY, policy_text(HEADING, "Gone.", QUOTE, "Middle.", QUOTE))
        [item] = recall(s).items
        assert item.state == "moved" and item.locations == ((9, 9), (11, 11))


def test_a_missing_file_is_missing_and_says_why(
    tmp_path: Path, symlink_support: None
) -> None:  # [NEG]
    for reason in ("not_found", "unsafe_path", "too_large", "not_utf8"):
        sub = tmp_path / reason
        sub.mkdir()
        with scenario(sub) as s:
            if reason == "not_found":
                s.file.unlink()
            elif reason == "unsafe_path":
                outside = sub / "outside.md"
                outside.write_text(BASE, encoding="utf-8", newline="\n")
                s.file.unlink()
                s.file.symlink_to(outside)
            elif reason == "too_large":
                s.file.write_bytes(b"a" * (MAX_FILE_BYTES + 1))
            else:
                s.file.write_bytes(b"\xff\xfe not utf-8 \x00")
            [item] = recall(s).items
            assert (item.state, item.detail, item.flags) == ("missing", reason, ())
            assert item.statement == STATEMENT


def test_voided_promises_are_left_out_of_recall(tmp_path: Path) -> None:  # [NEG]
    with scenario(tmp_path, cites=[(POLICY, QUOTE), (POLICY, QUOTE)]) as s:
        first, second = s.promise_ids()
        run(s.d.service.void(first, "wrong_customer"))
        report = recall(s)
        assert report.checked == 1 and [i.promise_id for i in report.items] == [second]
        assert kinds(s, first) == ["created", "voided"]  # recall never touched it


def test_more_promises_than_the_cap_is_refused_whole_and_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # [NEG]
    with scenario(tmp_path, cites=[(POLICY, QUOTE)] * 3) as s:
        monkeypatch.setattr(service_module, "MAX_RECALL_PROMISES", 2)
        with pytest.raises(RecallRefusedError) as info:
            recall(s)
        assert info.value.code == "too_many_promises"
        assert info.value.counts == {"promises": 3, "cap": 2}
        assert all(kinds(s, pid) == ["created"] for pid in s.promise_ids())
        monkeypatch.setattr(service_module, "MAX_RECALL_PROMISES", 3)
        assert recall(s).checked == 3  # exactly at the cap is allowed


def test_the_cap_counts_only_promises_that_are_not_voided(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with scenario(tmp_path, cites=[(POLICY, QUOTE)] * 3) as s:
        run(s.d.service.void(s.promise_ids()[0], "other"))
        monkeypatch.setattr(service_module, "MAX_RECALL_PROMISES", 2)
        assert recall(s).checked == 2


def test_a_customer_filter_limits_the_check(tmp_path: Path) -> None:
    with scenario(tmp_path) as s:
        assert recall(s, "a" * 32).checked == 0
        assert recall(s, s.imported.customer_id).checked == 1


def test_each_distinct_vault_file_is_read_once_per_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = "policies/backup-policy.md"
    files = {POLICY: BASE, other: policy_text(HEADING, "Backups are daily.", KEYS)}
    cites = [
        (POLICY, QUOTE),
        (other, "Backups are daily."),
        (POLICY, QUOTE),
        (other, "Backups are daily."),
    ]
    with scenario(tmp_path, files=files, cites=cites) as s:
        reads: list[str] = []
        real = service_module.read_vault_text

        def counting(root: Path, source_path: str) -> VaultText | ReadReason:
            reads.append(source_path)
            return real(root, source_path)

        monkeypatch.setattr(service_module, "read_vault_text", counting)
        assert recall(s).checked == 4
        assert sorted(reads) == sorted([POLICY, other])


def test_recall_reads_the_vault_so_a_deleted_or_stale_index_makes_no_difference(
    tmp_path: Path,
) -> None:
    index = tmp_path / "index"

    async def build_index(vault: Path) -> None:
        async with stack(vault, index):
            pass

    with scenario(tmp_path) as s:
        run(build_index(s.vault))  # an index of the vault as it is now
        set_text(s.vault, POLICY, policy_text(HEADING, "Data at rest is not encrypted.", KEYS))
        stale = recall(s).items[0]  # the index still holds the old sentence; the vault does not
        assert (stale.state, stale.detail) == ("changed", "quote_not_found")
        shutil.rmtree(index)
        set_text(s.vault, POLICY, BASE)
        assert recall(s).items[0].state == "intact"  # and with no index at all


def test_recall_needs_the_vault_folder_and_says_which(tmp_path: Path) -> None:  # [NEG]
    with scenario(tmp_path) as s:
        no_vault = LedgerService(s.d.repo, s.d.export_root, s.d.clock)
        with pytest.raises(VaultRootNotConfiguredError):
            run(no_vault.recall(None))
        assert kinds(s, s.promise_ids()[0]) == ["created"]


# ----- void and acknowledge -----------------------------------------------------------------------


def test_void_appends_an_event_with_a_fixed_reason_and_never_edits_the_promise(
    tmp_path: Path,
) -> None:
    with scenario(tmp_path) as s:
        [pid] = s.promise_ids()
        before = run(s.d.repo.get_promise(pid))
        event = run(s.d.service.void(pid, "imported_in_error"))
        assert (event.kind, event.detail_code, event.state) == ("voided", "imported_in_error", None)
        assert run(s.d.repo.get_promise(pid)) == before
        assert kinds(s, pid) == ["created", "voided"]


def test_a_voided_promise_cannot_be_voided_or_acknowledged_again(tmp_path: Path) -> None:  # [NEG]
    with scenario(tmp_path) as s:
        [pid] = s.promise_ids()
        run(s.d.service.void(pid, "other"))
        for action in (
            lambda: run(s.d.service.void(pid, "other")),
            lambda: run(s.d.service.acknowledge(pid, 1)),
        ):
            with pytest.raises(RecallRefusedError) as info:
                action()
            assert info.value.code == "promise_voided"
        assert kinds(s, pid) == ["created", "voided"]


def test_an_unknown_promise_is_refused(tmp_path: Path) -> None:  # [NEG]
    with scenario(tmp_path) as s:
        for action in (
            lambda: run(s.d.service.void("00000000-0000-4000-8000-000000000000", "other")),
            lambda: run(s.d.service.acknowledge("00000000-0000-4000-8000-000000000000", 1)),
        ):
            with pytest.raises(LedgerRefusedError) as info:
                action()
            assert info.value.code == "promise_unknown"


def test_acknowledge_records_that_the_owner_saw_the_flag(tmp_path: Path) -> None:
    with scenario(tmp_path) as s:
        [pid] = s.promise_ids()
        seen = recall(s).items[0].ack.recall_event_id
        assert seen is not None
        event = run(s.d.service.acknowledge(pid, seen))
        assert (event.kind, event.state, event.detail_code) == ("acknowledged", "intact", None)
        assert kinds(s, pid) == ["created", "recall_state_changed", "acknowledged"]
        again = recall(s).items[
            0
        ]  # an acknowledgement is not a recall state: nothing new to record
        assert (again.previous_state, again.event_recorded) == ("intact", False)


GONE = policy_text(HEADING, "Data at rest is not encrypted.", KEYS)


def test_a_stale_acknowledgement_is_refused_and_the_ledger_file_is_unchanged(  # [NEG]
    tmp_path: Path,
) -> None:
    with scenario(tmp_path) as s:
        [pid] = s.promise_ids()
        first = recall(s).items[0].ack.recall_event_id
        set_text(s.vault, POLICY, GONE)
        second = recall(s).items[0].ack.recall_event_id
        assert first is not None and second is not None and second > first
        ledger_file = tmp_path / "ledger" / "ledger.sqlite3"  # where `draft_for` keeps it
        before = ledger_file.read_bytes()
        with pytest.raises(RecallRefusedError) as info:
            run(s.d.service.acknowledge(pid, first))  # the owner saw "intact"; it is "changed" now
        assert info.value.code == "acknowledge_stale" and info.value.counts == {}
        assert kinds(s, pid) == ["created", "recall_state_changed", "recall_state_changed"]
        assert ledger_file.read_bytes() == before


def test_an_acknowledgement_with_no_recall_yet_is_stale(tmp_path: Path) -> None:  # [NEG]
    with scenario(tmp_path) as s:
        [pid] = s.promise_ids()
        with pytest.raises(RecallRefusedError) as info:
            run(s.d.service.acknowledge(pid, 1))
        assert info.value.code == "acknowledge_stale"
        assert kinds(s, pid) == ["created"]


def test_recall_and_the_listing_say_which_state_was_acknowledged_and_whether_it_is_current(
    tmp_path: Path,
) -> None:
    with scenario(tmp_path) as s:
        [pid] = s.promise_ids()

        def listed() -> tuple[int | None, int | None, str | None, bool]:
            [view] = run(s.d.service.promises(None, False, 1)).items
            a = view.ack
            return (
                a.recall_event_id,
                a.acknowledged_event_id,
                a.acknowledged_state,
                (a.acknowledgement_current),
            )

        assert listed() == (None, None, None, False)  # no recall, no acknowledgement
        set_text(s.vault, POLICY, GONE)
        item = recall(s).items[0]
        seen = item.ack.recall_event_id
        assert seen is not None and item.ack.acknowledged_event_id is None
        assert item.ack.acknowledgement_current is False  # nothing acknowledged
        ack = run(s.d.service.acknowledge(pid, seen))
        assert listed() == (seen, ack.event_id, "changed", True)
        again = recall(s).items[0]  # nothing changed, so no new recall event: still current
        assert (again.event_recorded, again.ack.acknowledgement_current) == (False, True)
        assert again.ack.acknowledged_state == "changed"
        set_text(s.vault, POLICY, BASE)
        back = recall(s).items[0]  # a later recall event exists: the acknowledgement is old news
        assert back.event_recorded and back.state == "intact"
        assert back.ack.recall_event_id is not None and back.ack.recall_event_id > ack.event_id
        assert back.ack.acknowledged_event_id == ack.event_id
        assert back.ack.acknowledged_state == "changed"
        assert back.ack.acknowledgement_current is False
        assert listed() == (back.ack.recall_event_id, ack.event_id, "changed", False)


def test_an_old_acknowledgement_with_no_state_is_shown_without_one(tmp_path: Path) -> None:
    with scenario(tmp_path) as s:
        [pid] = s.promise_ids()
        seen = recall(s).items[0].ack.recall_event_id
        assert seen is not None
        old_style_acknowledgement(tmp_path / "ledger" / "ledger.sqlite3", pid)
        item = recall(s).items[0]
        assert item.ack.acknowledged_state is None and item.ack.acknowledged_event_id is not None
        assert item.ack.acknowledgement_current is True  # no recall event after it
        [view] = run(s.d.service.promises(None, False, 1)).items
        assert view.ack.acknowledged_state is None


# ----- item fields, the locations cap and flags_now -----------------------------------------------


def test_recall_items_say_which_promise_they_are_in_codes_and_numbers(tmp_path: Path) -> None:
    with scenario(tmp_path) as s:
        [promise] = run(s.d.repo.list_promises())
        item = recall(s).items[0]
        assert (item.customer_id, item.source_path) == (promise.customer_id, POLICY)
        assert (item.start_line, item.end_line) == (QUOTE_LINE, QUOTE_LINE)


def test_moved_locations_are_capped_at_twenty_with_a_count_of_the_rest(tmp_path: Path) -> None:
    many = policy_text(HEADING, "Gone.", *([QUOTE] * 25))
    with scenario(tmp_path) as s:
        set_text(s.vault, POLICY, many)
        item = recall(s).items[0]
        assert item.state == "moved"
        assert len(item.locations) == service_module.MAX_LOCATIONS == 20
        assert item.locations_not_listed == 5
        assert item.locations[0] == (9, 9) and item.locations[-1] == (28, 28)


def test_twenty_locations_are_all_listed_and_none_are_hidden(tmp_path: Path) -> None:
    exactly = policy_text(HEADING, "Gone.", *([QUOTE] * 20))
    with scenario(tmp_path) as s:
        set_text(s.vault, POLICY, exactly)
        item = recall(s).items[0]
        assert (len(item.locations), item.locations_not_listed) == (20, 0)


def test_a_blast_item_names_its_promise_and_carries_the_flags_of_the_current_file(
    tmp_path: Path,
) -> None:
    overdue = BASE.replace("review_by: 2030-01-01", "review_by: 2020-01-01")
    with scenario(tmp_path) as s:
        set_text(s.vault, POLICY, overdue)  # the file as it is NOW: a review that is overdue
        [promise] = run(s.d.repo.list_promises())
        report = run(s.d.service.blast_radius(POLICY, BASE, None))  # proposed: the date is fixed
        [item] = report.items
        assert item.flags == () and item.flags_now == (
            "document_changed_elsewhere",
            "review_overdue",
        )
        assert (item.customer_id, item.source_path) == (promise.customer_id, POLICY)
        assert (item.start_line, item.end_line) == (QUOTE_LINE, QUOTE_LINE)
        proposed_overdue = run(s.d.service.blast_radius(POLICY, overdue, None)).items[0]
        assert proposed_overdue.flags == proposed_overdue.flags_now != ()


def test_the_blast_radius_report_states_what_it_covers(tmp_path: Path) -> None:
    with scenario(tmp_path) as s:
        report = run(s.d.service.blast_radius(POLICY, BASE, None))
        assert report.coverage == "promises only; controls and linking documents are not checked"
        nothing = run(s.d.service.blast_radius("policies/none.md", BASE, None))
        assert nothing.items == () and nothing.coverage == report.coverage


def test_blast_locations_are_capped_at_twenty_with_a_count_of_the_rest(tmp_path: Path) -> None:
    with scenario(tmp_path) as s:
        proposed = policy_text(HEADING, "Gone.", *([QUOTE] * 30))
        [item] = run(s.d.service.blast_radius(POLICY, proposed, None)).items
        assert item.state_proposed == "moved"
        assert (len(item.locations), item.locations_not_listed) == (20, 10)


# ----- the import's information count -------------------------------------------------------------


def test_an_import_counts_the_promises_that_already_rest_on_changed_lines(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    set_text(vault, POLICY, BASE)
    cite = real_citation(vault, POLICY, QUOTE)
    with draft_for(tmp_path, [answered("Yes.", cite=cite)] * 2, vault=vault) as d:
        set_text(
            vault, POLICY, policy_text(HEADING, QUOTE + " Except backups.", KEYS)
        )  # after drafting
        approve(d, 2, 3)
        out = import_rows(d)
        assert out.created == 2 and out.already_changed_since_draft == 2
        for promise in run(d.repo.list_promises()):
            events = run(d.repo.list_events(promise.promise_id))
            assert [e.kind for e in events] == ["created"]  # information only: no event


def test_an_import_of_an_unchanged_policy_counts_zero_and_a_flag_alone_does_not_count(
    tmp_path: Path,
) -> None:
    with scenario(tmp_path) as s:
        assert s.imported.already_changed_since_draft == 0
    flagged = tmp_path / "flagged"
    flagged.mkdir()
    vault = flagged / "vault"
    set_text(vault, POLICY, BASE)
    with draft_for(
        flagged, [answered("Yes.", cite=real_citation(vault, POLICY, QUOTE))], vault=vault
    ) as d:
        set_text(vault, POLICY, policy_text(HEADING, QUOTE, "Keys rotate quarterly."))
        approve(d, 2)
        assert import_rows(d).already_changed_since_draft == 0  # intact; only a flag


def test_an_import_with_no_vault_folder_reports_none(tmp_path: Path) -> None:
    with draft_for(tmp_path, supported_outcomes(1)) as d:
        approve(d, 2)
        assert import_rows(d).already_changed_since_draft is None


# ----- no content in any log ----------------------------------------------------------------------


def test_a_canary_in_a_quote_an_answer_and_a_customer_name_reaches_no_log(  # [NEG]
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    sentence = f"{CANARY} data at rest is encrypted with AES-256."
    vault = tmp_path / "vault"
    set_text(vault, POLICY, policy_text(HEADING, sentence, KEYS))
    cite = real_citation(vault, POLICY, sentence)
    with draft_for(tmp_path, [answered(f"answer {CANARY}", cite=cite)], vault=vault) as d:
        approve(d, 2)
        out = import_rows(d, new_name=f"Customer {CANARY}")
        assert out.created == 1
        recall_report = run(d.service.recall(None))
        assert recall_report.items[0].state == "intact"
        set_text(vault, POLICY, policy_text(HEADING, f"{CANARY} was removed.", KEYS))
        changed = run(d.service.recall(None)).items[0]
        assert changed.state == "changed" and changed.ack.recall_event_id is not None
        [pid] = [p.promise_id for p in run(d.repo.list_promises())]
        run(d.service.acknowledge(pid, changed.ack.recall_event_id))
        run(d.service.void(pid, "other"))
        run(d.service.promises(None, True, 1))
    assert CANARY not in caplog.text
