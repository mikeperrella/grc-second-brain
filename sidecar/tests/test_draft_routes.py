"""`POST /questionnaire/draft` (ADR-021, chunk 6b): the rows of a finished draft, joined with
its run record. Read-only. Offline: a real questionnaire run with a scripted answerer, the real
writer, a real ledger in `tmp_path`. All data is synthetic.

The controls under test (each has a mutation proof in the chunk report):

- the status, the reasons and the citation come from the run record, never from a workbook cell;
- `status_edited`, `answer_edited` and `evidence_edited` are hash comparisons with the record;
- the run record is found by the full recorded file name, never by the 8 hex digits in it.
"""

import hashlib
import logging
import os
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from grc_sidecar.ledger.service import LedgerService
from grc_sidecar.main import create_app
from grc_sidecar.questionnaire.runtime import LedgerRunRecorder
from grc_sidecar.questionnaire.status import blank_result
from grc_sidecar.repositories.sqlite_ledger_repo import SqliteLedgerRepository
from grc_sidecar.security.net import create_async_client

from .clockkit import ManualClock
from .ledgerkit import (
    ANSWER_COL,
    CONFIDENCE_COL,
    EVIDENCE_COL,
    REASON_COL,
    REVIEW_COL,
    STATUS_COL,
    Draft,
    answered,
    cited,
    draft_for,
    edit_cells,
    good_reader,
    run,
    supported_outcomes,
)
from .runskit import CANARY, FILE_NAME, MAPPING, make_workbook, outcome, setup, supported
from .xlsxkit import simple_bytes, to_bytes

pytestmark = pytest.mark.usefixtures("clean_grc_env")

TOKEN = hashlib.sha256(b"grc-test-token").hexdigest()
AUTH = {"X-GRC-Auth": TOKEN}
FIRST, SECOND, THIRD = 2, 3, 4  # sheet rows of the three questions (row 1 is the header)
FORGED = "FORGED-VERDICT-5e2c"


@asynccontextmanager
async def client_for(service: LedgerService | None) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(TOKEN, ledger=service)
    async with create_async_client(
        "http://127.0.0.1:8000", transport=httpx.ASGITransport(app=app)
    ) as client:
        yield client


def post(
    service: LedgerService | None, body: object, *, headers: dict[str, str] | None = None
) -> httpx.Response:
    async def go() -> httpx.Response:
        async with client_for(service) as client:
            return await client.post(
                "/questionnaire/draft", json=body, headers=AUTH if headers is None else headers
            )

    return run(go())


def outcomes() -> list:  # type: ignore[type-arg]
    """Row 2 SUPPORTED, row 3 PARTIAL, row 4 NEEDS_REVIEW (blank): all three kinds."""
    return [
        outcome(supported("Yes. Data at rest is encrypted.")),
        answered(
            "Mostly. Backups are encrypted.",
            status="PARTIAL",
            reasons=("review_overdue",),
            cite=cited(5, 6, quote="Backups are encrypted."),
        ),
        outcome(blank_result("no_evidence")),
    ]


def ask(d: Draft, **changes: object) -> httpx.Response:
    return post(d.service, {"file": d.path.name, **changes})


def rows_by_number(response: httpx.Response) -> dict[int, dict[str, object]]:
    assert response.status_code == 200, response.text
    return {row["row"]: row for row in response.json()["rows"]}


def tree_hashes(folder: Path) -> dict[str, str]:
    return {
        str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


# ----- the happy path and the shape of the answer ---------------------------------------------


def test_an_untouched_draft_returns_the_records_rows_and_no_edit_flags(tmp_path: Path) -> None:
    with draft_for(tmp_path, outcomes()) as d:
        response = ask(d)
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"model", "file_name", "complete", "question_count", "rows"}
        assert body["file_name"] == d.path.name and body["question_count"] == 3
        assert body["model"] == "llama3.1:8b" and body["complete"] is True
        rows = rows_by_number(response)
        assert sorted(rows) == [FIRST, SECOND, THIRD]
        first, second, third = rows[FIRST], rows[SECOND], rows[THIRD]
        assert set(first) == {
            "row",
            "question_text",
            "status",
            "reasons",
            "answer_text",
            "citation",
            "status_edited",
            "answer_edited",
            "evidence_edited",
        }  # no Confidence, no Review, no Reason cell, no Evidence cell, no Status cell
        assert first["question_text"] == "Do you encrypt data at rest, question 1?"
        assert first["status"] == "SUPPORTED" and first["reasons"] == []
        assert first["answer_text"] == "Yes. Data at rest is encrypted."
        assert first["citation"] == {
            "source_path": "policies/encryption-policy.md",
            "start_line": 12,
            "end_line": 13,
            "quote": "Data at rest is encrypted with AES-256.",
        }
        assert second["status"] == "PARTIAL" and second["reasons"] == ["review_overdue"]
        assert second["citation"] is not None and second["citation"]["start_line"] == 5
        assert third["status"] == "NEEDS_REVIEW" and third["reasons"] == ["no_evidence"]
        assert third["answer_text"] is None and third["citation"] is None
        for row in rows.values():  # a blank row's Status cell is compared too (it is never blank)
            assert row["status_edited"] is False
            assert row["answer_edited"] is False
            assert row["evidence_edited"] is False


def test_a_draft_of_500_questions_returns_500_rows(tmp_path: Path) -> None:
    with draft_for(tmp_path, supported_outcomes(500), data=make_workbook(500)) as d:
        rows = rows_by_number(ask(d))
        assert len(rows) == 500 and sorted(rows) == list(range(2, 502))


# ----- the verdict and the citation are the record's (mutation proofs 1 and 2) -----------------


def test_draft_status_comes_from_the_record_not_the_status_cell(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, outcomes()) as d:
        edit_cells(
            d,
            {
                (FIRST, STATUS_COL): "NEEDS_REVIEW",  # a SUPPORTED row made to look blocked
                (THIRD, STATUS_COL): "SUPPORTED",  # a blocked row made to look supported
                (SECOND, STATUS_COL): FORGED,
            },
        )
        response = ask(d)
        rows = rows_by_number(response)
        assert rows[FIRST]["status"] == "SUPPORTED" and rows[FIRST]["status_edited"] is True
        assert rows[SECOND]["status"] == "PARTIAL" and rows[SECOND]["status_edited"] is True
        assert rows[THIRD]["status"] == "NEEDS_REVIEW" and rows[THIRD]["status_edited"] is True
        assert FORGED not in response.text  # the edited cell's text is not even returned


def test_draft_reasons_come_from_the_record_not_the_reason_cell(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, outcomes()) as d:
        edit_cells(d, {(SECOND, REASON_COL): FORGED, (THIRD, REASON_COL): "none needed"})
        response = ask(d)
        rows = rows_by_number(response)
        assert rows[SECOND]["reasons"] == ["review_overdue"]
        assert rows[THIRD]["reasons"] == ["no_evidence"]
        assert FORGED not in response.text and "none needed" not in response.text


def test_draft_citation_comes_from_the_record_not_the_evidence_cell(
    tmp_path: Path,
) -> None:  # [NEG]
    forged = f'policies/{FORGED}.md L1-L2: "I am not the quote"'
    with draft_for(tmp_path, outcomes()) as d:
        edit_cells(d, {(FIRST, EVIDENCE_COL): forged, (THIRD, EVIDENCE_COL): forged})
        response = ask(d)
        rows = rows_by_number(response)
        assert rows[FIRST]["citation"] == {
            "source_path": "policies/encryption-policy.md",
            "start_line": 12,
            "end_line": 13,
            "quote": "Data at rest is encrypted with AES-256.",
        }
        assert rows[FIRST]["evidence_edited"] is True
        assert rows[THIRD]["citation"] is None  # the record has none; the cell cannot give one
        assert rows[THIRD]["evidence_edited"] is True
        assert rows[SECOND]["evidence_edited"] is False
        assert FORGED not in response.text and "I am not the quote" not in response.text


# ----- the edit flags are hash comparisons (mutation proof 3) ----------------------------------


def test_draft_edit_flags_are_hash_comparisons_with_the_record(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, outcomes()) as d:
        edit_cells(
            d,
            {
                (FIRST, ANSWER_COL): "Yes. Data at rest is encrypted!",  # one character
                (SECOND, ANSWER_COL): None,  # an answer deleted
                (THIRD, ANSWER_COL): "A note typed into a blank row",
            },
        )
        rows = rows_by_number(ask(d))
        assert rows[FIRST]["answer_edited"] is True
        assert rows[FIRST]["answer_text"] == "Yes. Data at rest is encrypted!"
        assert rows[SECOND]["answer_edited"] is True and rows[SECOND]["answer_text"] is None
        # The record says no answer was written, so the typed text is flagged and not returned.
        assert rows[THIRD]["answer_edited"] is True and rows[THIRD]["answer_text"] is None
        for row in rows.values():
            assert row["status_edited"] is False and row["evidence_edited"] is False


def test_draft_status_and_evidence_flags_are_independent(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, outcomes()) as d:
        edit_cells(d, {(FIRST, STATUS_COL): "PARTIAL", (SECOND, EVIDENCE_COL): "changed"})
        rows = rows_by_number(ask(d))
        assert (rows[FIRST]["status_edited"], rows[FIRST]["evidence_edited"]) == (True, False)
        assert (rows[SECOND]["status_edited"], rows[SECOND]["evidence_edited"]) == (False, True)
        assert (rows[THIRD]["status_edited"], rows[THIRD]["evidence_edited"]) == (False, False)
        assert all(row["answer_edited"] is False for row in rows.values())


def test_the_confidence_and_review_cells_are_never_returned(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, outcomes()) as d:
        edit_cells(d, {(FIRST, CONFIDENCE_COL): 0.01, (FIRST, REVIEW_COL): f"approved {FORGED}"})
        response = ask(d)
        assert FORGED not in response.text and "approved" not in response.text
        assert "0.01" not in response.text and "confidence" not in response.text.lower()


# ----- the run record is found by the full recorded name only (mutation proof 4) ---------------


def two_drafts_sharing_a_prefix(tmp_path: Path) -> tuple[LedgerService, Path, str, str]:
    """Two finished runs whose ids start with the same 8 hex digits, so their names end the same."""
    clock = ManualClock()
    repo = SqliteLedgerRepository(tmp_path / "ledger" / "ledger.sqlite3", clock)
    shared = "abcdef12"
    s = setup(
        tmp_path,
        count=3,
        run_recorder=LedgerRunRecorder(repo),
        read_cited=good_reader,
        run_ids=[shared + "1" * 24, shared + "2" * 24],
    )
    (s.questionnaire_root / "Second.xlsx").write_bytes(simple_bytes(2))

    async def scenario() -> tuple[str, str]:
        names = []
        for file in (FILE_NAME, "Second.xlsx"):
            state = await s.runs.start(file, MAPPING)
            _ = [event async for event in s.runs.events(state.run_id)]
            assert state.summary is not None and state.summary.file_name is not None
            names.append(state.summary.file_name)
        return names[0], names[1]

    first, second = run(scenario())
    assert first[-13:] == second[-13:]  # the same "-abcdef12.xlsx" ending
    return LedgerService(repo, s.export_root, clock), s.export_root, first, second


def test_two_records_sharing_a_prefix_return_only_the_exact_match(tmp_path: Path) -> None:  # [NEG]
    service, _, first, second = two_drafts_sharing_a_prefix(tmp_path)
    one = post(service, {"file": first})
    two = post(service, {"file": second})
    assert one.status_code == two.status_code == 200
    assert one.json()["file_name"] == first and one.json()["question_count"] == 3
    assert two.json()["file_name"] == second and two.json()["question_count"] == 2
    assert len(one.json()["rows"]) == 3 and len(two.json()["rows"]) == 2


def test_a_renamed_copy_is_refused_even_with_the_same_8_hex_digits(tmp_path: Path) -> None:  # [NEG]
    service, root, first, _ = two_drafts_sharing_a_prefix(tmp_path)
    renamed = "Renamed-UNREVIEWED-DRAFT-abcdef12.xlsx"  # same 8 hex digits, different name
    for name in (renamed, "Copy of " + first, "Different-" + first, "x.xlsx"):
        shutil.copyfile(root / first, root / name)
        response = post(service, {"file": name})
        assert response.status_code == 422, name
        assert response.json() == {"error": "draft_refused", "code": "run_record_missing"}


# ----- refusals: fixed words, no rows, no content ---------------------------------------------


def test_without_a_ledger_folder_the_route_refuses_503(tmp_path: Path) -> None:  # [NEG]
    response = post(None, {"file": "anything.xlsx"})
    assert response.status_code == 503
    assert response.json() == {"error": "ledger_root_not_configured"}


def test_without_an_export_folder_the_route_refuses_503(tmp_path: Path) -> None:  # [NEG]
    repo = SqliteLedgerRepository(tmp_path / "ledger" / "ledger.sqlite3", ManualClock())
    response = post(LedgerService(repo, None, ManualClock()), {"file": "anything.xlsx"})
    assert response.status_code == 503
    assert response.json() == {"error": "export_root_not_configured"}


def test_a_missing_file_is_404_file_not_found(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, outcomes()) as d:
        response = post(d.service, {"file": "nothing-here.xlsx"})
        assert response.status_code == 404 and response.json() == {"error": "file_not_found"}


def test_a_draft_from_a_run_made_without_a_ledger_returns_no_rows(tmp_path: Path) -> None:  # [NEG]
    s = setup(tmp_path, count=3, run_recorder=None, read_cited=good_reader)

    async def scenario() -> str:
        state = await s.runs.start(FILE_NAME, MAPPING)
        _ = [event async for event in s.runs.events(state.run_id)]
        assert state.summary is not None and state.summary.file_name is not None
        assert state.summary.warnings == ("run_record_not_kept",)
        return state.summary.file_name

    name = run(scenario())
    ledger_file = tmp_path / "ledger" / "ledger.sqlite3"
    service = LedgerService(
        SqliteLedgerRepository(ledger_file, ManualClock()), s.export_root, ManualClock()
    )
    response = post(service, {"file": name})
    assert response.status_code == 422
    assert response.json() == {"error": "draft_refused", "code": "run_record_missing"}
    assert "rows" not in response.text
    assert not ledger_file.exists() and not ledger_file.parent.exists()  # a read creates nothing


def test_a_ledger_without_this_draft_refuses_run_record_missing(tmp_path: Path) -> None:  # [NEG]
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    with draft_for(tmp_path / "a", outcomes()) as other, draft_for(tmp_path / "b", outcomes()) as d:
        stranger = other.path.read_bytes()
        (d.export_root / "Stranger.xlsx").write_bytes(stranger)
        response = post(d.service, {"file": "Stranger.xlsx"})
        assert response.status_code == 422
        assert response.json()["code"] == "run_record_missing"


def test_a_file_below_the_export_folder_is_not_a_draft(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, outcomes()) as d:
        sub = d.export_root / "older"
        sub.mkdir()
        shutil.copyfile(d.path, sub / d.path.name)  # the right name, one folder down
        response = post(d.service, {"file": f"older/{d.path.name}"})
        assert response.status_code == 422
        assert response.json() == {"error": "draft_refused", "code": "run_record_missing"}


def test_an_edited_question_refuses_the_whole_draft_with_counts_only(
    tmp_path: Path,
) -> None:  # [NEG]
    with draft_for(tmp_path, outcomes()) as d:
        edit_cells(d, {(SECOND, 2): f"A different question {CANARY}?"})
        response = ask(d)
        assert response.status_code == 422
        assert response.json() == {
            "error": "draft_refused",
            "code": "question_mismatch",
            "counts": {"mismatched_rows": 1, "workbook_rows": 3, "recorded_rows": 3},
        }
        assert CANARY not in response.text and "rows" not in response.json()


def test_an_added_question_row_refuses_the_whole_draft(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, outcomes()) as d:
        edit_cells(d, {(5, 2): "A question added after drafting?"})
        response = ask(d)
        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "question_mismatch"
        assert body["counts"] == {"mismatched_rows": 1, "workbook_rows": 4, "recorded_rows": 3}


def test_a_changed_header_refuses_the_whole_draft(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, outcomes()) as d:
        edit_cells(d, {(1, STATUS_COL): "Status"})
        response = ask(d)
        assert response.status_code == 422
        assert response.json() == {"error": "draft_refused", "code": "headers_changed"}


def test_a_file_that_is_not_a_workbook_is_refused_with_the_loaders_code(
    tmp_path: Path,
) -> None:  # [NEG]
    with draft_for(tmp_path, outcomes()) as d:
        d.path.write_bytes(b"not a zip file " + CANARY.encode())
        response = ask(d)
        assert response.status_code == 422
        assert response.json()["error"] == "draft_refused"
        assert response.json()["code"] == "not_a_zip"  # the loader's own fixed code
        assert CANARY not in response.text


def test_the_body_takes_a_file_and_nothing_else(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, outcomes()) as d:
        extra = ask(d, run_id="a" * 32)
        assert extra.status_code == 422 and extra.json() == {
            "error": "invalid_request",
            "fields": ["body.run_id"],
        }
        assert post(d.service, {}).status_code == 422
        assert post(d.service, {"file": ""}).status_code == 422
        assert post(d.service, {"file": "x" * 300}).status_code == 422


def test_the_route_requires_the_token(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, outcomes()) as d:
        assert post(d.service, {"file": d.path.name}, headers={}).status_code == 401
        wrong = {"X-GRC-Auth": "0" * 64}
        assert post(d.service, {"file": d.path.name}, headers=wrong).status_code == 401


# ----- the export folder's path guard ----------------------------------------------------------


def test_paths_that_leave_the_export_folder_are_refused(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, outcomes()) as d:
        outside = tmp_path / "outside.xlsx"
        shutil.copyfile(d.path, outside)
        for file in ("../outside.xlsx", str(outside), "..", "a/../../outside.xlsx"):
            response = post(d.service, {"file": file})
            assert response.status_code in (400, 404), file
            assert response.json()["error"] in ("path_not_allowed", "file_not_found"), file
            assert str(tmp_path) not in response.text


def test_a_link_out_of_the_export_folder_is_refused(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, outcomes()) as d:
        outside = tmp_path / "elsewhere.xlsx"
        shutil.copyfile(d.path, outside)
        link = d.export_root / "link.xlsx"
        try:
            os.symlink(outside, link)
        except OSError:
            pytest.skip("symbolic links are not available here")
        response = post(d.service, {"file": "link.xlsx"})
        assert response.status_code == 400 and response.json() == {"error": "path_not_allowed"}


# ----- read-only, and no content anywhere ------------------------------------------------------


def test_the_route_writes_nothing(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, outcomes()) as d:
        edit_cells(d, {(FIRST, ANSWER_COL): "edited", (SECOND, STATUS_COL): "SUPPORTED"})
        before = (tree_hashes(d.export_root), tree_hashes(d.export_root.parent / "ledger"))
        before_names = sorted(p.name for p in d.export_root.parent.iterdir())
        assert ask(d).status_code == 200
        assert post(d.service, {"file": "missing.xlsx"}).status_code == 404
        after = (tree_hashes(d.export_root), tree_hashes(d.export_root.parent / "ledger"))
        assert after == before  # no workbook saved, no ledger row, no event, no new file
        assert sorted(p.name for p in d.export_root.parent.iterdir()) == before_names


def test_no_content_reaches_a_log_at_debug_or_a_refusal_body(  # [NEG]
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with draft_for(tmp_path, outcomes()) as d:
        edit_cells(
            d,
            {
                (FIRST, ANSWER_COL): f"Typed answer {CANARY}",
                (FIRST, STATUS_COL): f"SUPPORTED {CANARY}",
                (FIRST, REASON_COL): f"reason {CANARY}",
                (FIRST, EVIDENCE_COL): f"evidence {CANARY}",
                (FIRST, CONFIDENCE_COL): CANARY,
                (FIRST, REVIEW_COL): f"approved {CANARY}",
            },
        )
        with caplog.at_level(logging.DEBUG):
            ok = ask(d)
            # The Answer cell is the one workbook text that is returned (as untrusted text).
            assert ok.status_code == 200
            assert rows_by_number(ok)[FIRST]["answer_text"] == f"Typed answer {CANARY}"
            others = ok.text.replace(f"Typed answer {CANARY}", "")
            assert CANARY not in others  # Status, Reason, Evidence, Confidence, Review: not echoed
            edit_cells(d, {(SECOND, 2): f"Edited question {CANARY}"})
            refused = ask(d)
            assert refused.status_code == 422 and CANARY not in refused.text
            missing = post(d.service, {"file": f"{CANARY}.xlsx"})
            assert missing.status_code == 404 and CANARY not in missing.text
            rejected = ask(d, run_id=CANARY)  # names the field, never echoes the value
            assert rejected.status_code == 422 and CANARY not in rejected.text
        assert CANARY not in caplog.text
        assert all(CANARY not in str(record.args) for record in caplog.records)


def test_a_synthetic_workbook_helper_still_builds() -> None:
    """Guards the helpers this module relies on (a rename there must fail loudly here)."""
    assert to_bytes and supported().status == "SUPPORTED"
