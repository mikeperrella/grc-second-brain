"""The reviewed-workbook import (ADR-017 decisions 1, 2, 4, 5, 10, 11).

Each test makes a real draft (real run, real writer, real run recorder, real ledger), simulates the
owner by editing cells and saving in place, and calls the service the route calls. Invisible and
look-alike characters are built with chr().
"""

import datetime
import hashlib
import logging
from pathlib import Path

import pytest

from grc_sidecar.errors import (
    ExportRootNotConfiguredError,
    ImportRefusedError,
    LedgerRefusedError,
    PathNotAllowedError,
    PathNotFoundError,
    WorkbookRefusedError,
)
from grc_sidecar.ledger.service import SKIP_CODES, CustomerChoice, LedgerService
from grc_sidecar.questionnaire.loader import load_questionnaire
from grc_sidecar.questionnaire.sanitize import DIRECTION_MARKER
from grc_sidecar.questionnaire.status import blank_result
from grc_sidecar.vault.scan import CitedFile

from .ledgerkit import (
    ANSWER_COL,
    EVIDENCE_COL,
    QUESTION_COL,
    REVIEW_COL,
    SENT_ON,
    STATUS_COL,
    Draft,
    answered,
    approve,
    cited,
    draft_for,
    edit_cells,
    edit_sheet,
    import_rows,
    lines_hash,
    promises_of,
    run,
    supported_outcomes,
)
from .runskit import CANARY as RUN_CANARY
from .runskit import FILE_HASH, outcome
from .xlsxkit import simple_workbook, to_bytes

CANARY = "ALIBI-LEDGER-CANARY-5d2c"
CYRILLIC_A = chr(0x430)
NBSP = chr(0xA0)
ZERO_WIDTH_SPACE = chr(0x200B)
RLO = chr(0x202E)
NONE_SKIPPED = dict.fromkeys(SKIP_CODES, 0)


def skipped(**counts: int) -> dict[str, int]:
    return {**NONE_SKIPPED, **counts}


def customers_of(d: Draft) -> list[object]:
    return list(run(d.repo.list_customers()))


# ----- an unedited, approved draft ------------------------------------------------------------


def test_an_approved_draft_creates_one_promise_per_row_from_the_run_record(tmp_path: Path) -> None:
    with draft_for(tmp_path, supported_outcomes(3)) as d:
        approve(d, 2, 3, 4)
        out = import_rows(d)
        assert out.created == 3 and out.created_edited == 0
        assert out.customer_created is True and out.customer_id is not None
        assert out.skipped == NONE_SKIPPED
        assert out.review == {"approved": 3, "rejected": 0, "unreviewed": 0, "other": 0}
        assert out.other_values == ()
        stored = promises_of(d)
        assert [p.promise_id for p in stored] == list(out.promise_ids)
        answers = {a.row_index: a for a in run(d.repo.list_run_answers(d.run_id))}
        workbook_hash = hashlib.sha256(d.path.read_bytes()).hexdigest()
        for promise in stored:
            recorded = answers[promise.run_row_index]
            assert promise.customer_id == out.customer_id and promise.run_id == d.run_id
            assert promise.status == "SUPPORTED" and promise.edited_after_draft is False
            assert (promise.source_path, promise.doc_id) == (recorded.source_path, recorded.doc_id)
            assert (promise.start_line, promise.end_line) == (12, 13)
            assert promise.quote == recorded.quote == "Data at rest is encrypted with AES-256."
            assert promise.cited_lines_sha256 == recorded.cited_lines_sha256 == lines_hash(12)
            assert promise.cited_file_sha256 == recorded.cited_file_sha256 == FILE_HASH
            assert promise.question_sha256 == recorded.question_sha256
            assert promise.import_workbook_sha256 == workbook_hash
            assert promise.sent_on == datetime.date(2026, 10, 10)
        assert [p.answer_text for p in stored] == [f"Yes, answer {n}." for n in (1, 2, 3)]


def test_a_partial_answer_is_kept_as_partial(tmp_path: Path) -> None:
    partial = answered("Partly.", status="PARTIAL", reasons=("review_overdue",), cite=cited(5, 6))
    with draft_for(tmp_path, [partial]) as d:
        approve(d, 2)
        assert import_rows(d).created == 1
        [promise] = promises_of(d)
        assert promise.status == "PARTIAL" and (promise.start_line, promise.end_line) == (5, 6)


# ----- whole-import refusals --------------------------------------------------------------------


def test_a_run_with_no_record_is_refused(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, supported_outcomes(1)) as d:
        approve(d, 2)
        with pytest.raises(ImportRefusedError) as info:
            import_rows(d, run_id="c" * 32)
        assert info.value.code == "run_record_missing"
        assert promises_of(d) == [] and customers_of(d) == []


def test_a_missing_export_folder_is_refused_and_names_that_folder(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, supported_outcomes(1)) as d:
        service = LedgerService(d.repo, None, d.clock)
        with pytest.raises(ExportRootNotConfiguredError):
            run(
                service.import_reviewed(
                    d.run_id,
                    d.path.name,
                    CustomerChoice(new_customer_name="X", confirm_new=True),
                    SENT_ON,
                )
            )


def test_a_file_the_loader_refuses_is_refused_whole(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, supported_outcomes(1)) as d:
        (d.export_root / "junk.xlsx").write_bytes(b"this is not a workbook")
        with pytest.raises(WorkbookRefusedError):
            import_rows(d, file="junk.xlsx")
        assert promises_of(d) == [] and customers_of(d) == []


@pytest.mark.parametrize("column", range(ANSWER_COL, REVIEW_COL + 1))
def test_each_of_alibis_six_headers_must_be_where_the_record_says(  # [NEG]
    tmp_path: Path, column: int
) -> None:
    with draft_for(tmp_path, supported_outcomes(2)) as d:
        approve(d, 2, 3)
        edit_cells(d, {(1, column): "Something else"})
        with pytest.raises(ImportRefusedError) as info:
            import_rows(d)
        assert info.value.code == "headers_changed"
        assert promises_of(d) == [] and customers_of(d) == []


def test_a_header_that_was_deleted_is_also_headers_changed(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, supported_outcomes(1)) as d:
        edit_cells(d, {(1, STATUS_COL): None})
        with pytest.raises(ImportRefusedError) as info:
            import_rows(d)
        assert info.value.code == "headers_changed"


def mismatch(d: Draft) -> ImportRefusedError:
    with pytest.raises(ImportRefusedError) as info:
        import_rows(d)
    assert info.value.code == "question_mismatch"
    assert promises_of(d) == [] and customers_of(d) == []
    return info.value


def test_a_changed_question_refuses_the_whole_import_with_counts_only(
    tmp_path: Path,
) -> None:  # [NEG]
    with draft_for(tmp_path, supported_outcomes(3)) as d:
        approve(d, 2, 3, 4)
        edit_cells(d, {(3, QUESTION_COL): f"A question with {CANARY} in it?"})
        refused = mismatch(d)
        assert refused.counts == {"mismatched_rows": 1, "workbook_rows": 3, "recorded_rows": 3}
        assert CANARY not in str(refused) and CANARY not in repr(refused.counts)


def test_sorted_rows_are_a_question_mismatch(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, supported_outcomes(3)) as d:
        approve(d, 2, 3, 4)

        def swap(sheet: object) -> None:
            first = sheet.cell(row=2, column=QUESTION_COL)  # type: ignore[attr-defined]
            second = sheet.cell(row=3, column=QUESTION_COL)  # type: ignore[attr-defined]
            first.value, second.value = second.value, first.value

        edit_sheet(d, swap)  # type: ignore[arg-type]
        assert mismatch(d).counts["mismatched_rows"] == 2


def test_a_deleted_row_is_a_question_mismatch(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, supported_outcomes(3)) as d:
        approve(d, 2, 3, 4)
        edit_sheet(d, lambda sheet: sheet.delete_rows(3))
        refused = mismatch(d)
        assert refused.counts["workbook_rows"] == 2 and refused.counts["recorded_rows"] == 3


def test_an_extra_question_row_is_a_question_mismatch(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, supported_outcomes(2)) as d:
        approve(d, 2, 3)
        edit_cells(d, {(4, 1): "Q-new", (4, QUESTION_COL): "A question nobody drafted?"})
        refused = mismatch(d)
        assert refused.counts == {"mismatched_rows": 1, "workbook_rows": 3, "recorded_rows": 2}


def test_a_changed_cell_in_the_question_column_that_only_changes_spacing_is_not_a_mismatch(
    tmp_path: Path,
) -> None:
    """The question hash is over whitespace-normalized text, as the writer took it."""
    with draft_for(tmp_path, supported_outcomes(1)) as d:
        approve(d, 2)
        edit_cells(d, {(2, QUESTION_COL): "  Do   you encrypt\ndata at rest, question 1?  "})
        assert import_rows(d).created == 1


# ----- customers ----------------------------------------------------------------------------------


def test_a_choice_must_name_exactly_one_customer(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, supported_outcomes(1)) as d:
        approve(d, 2)
        for choice in (
            CustomerChoice(),
            CustomerChoice(existing_customer_id="a" * 32, new_customer_name="X", confirm_new=True),
        ):
            with pytest.raises(ImportRefusedError) as info:
                import_rows(d, choice=choice)
            assert info.value.code == "customer_choice_invalid"
        assert customers_of(d) == []


def test_a_new_name_without_confirmation_is_refused(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, supported_outcomes(1)) as d:
        approve(d, 2)
        with pytest.raises(ImportRefusedError) as info:
            import_rows(d, choice=CustomerChoice(new_customer_name="Acme", confirm_new=False))
        assert info.value.code == "new_customer_not_confirmed"
        assert customers_of(d) == [] and promises_of(d) == []


@pytest.mark.parametrize("name", ["Bad\x07Name", "Zero" + ZERO_WIDTH_SPACE + "Width", "x" * 201])
def test_a_bad_customer_name_is_refused_even_when_nothing_would_be_created(  # [NEG]
    tmp_path: Path, name: str
) -> None:
    with draft_for(tmp_path, supported_outcomes(1)) as d:  # nothing is approved
        with pytest.raises(LedgerRefusedError) as info:
            import_rows(d, new_name=name)
        assert info.value.code == "customer_name_invalid"
        assert customers_of(d) == []


def test_a_name_that_normalizes_to_an_existing_customer_is_refused_with_its_id(
    tmp_path: Path,
) -> None:  # [NEG]
    with draft_for(tmp_path, supported_outcomes(2)) as d:
        approve(d, 2)
        first = import_rows(d)
        assert first.customer_id is not None
        approve(d, 3)
        with pytest.raises(LedgerRefusedError) as info:
            import_rows(d, new_name="  ACME   test co ")
        assert info.value.code == "customer_exists" and info.value.customer_id == first.customer_id
        assert len(customers_of(d)) == 1 and len(promises_of(d)) == 1


def test_an_unknown_customer_id_is_refused(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, supported_outcomes(1)) as d:
        approve(d, 2)
        with pytest.raises(LedgerRefusedError) as info:
            import_rows(d, choice=CustomerChoice(existing_customer_id="f" * 32))
        assert info.value.code == "customer_unknown"


def test_a_new_customer_is_made_only_when_something_will_be_created(tmp_path: Path) -> None:
    with draft_for(tmp_path, supported_outcomes(2)) as d:  # nothing approved: every row skipped
        out = import_rows(d)
        assert out.created == 0 and out.customer_id is None and out.customer_created is False
        assert out.skipped == skipped(not_approved=2)
        assert customers_of(d) == []  # a refused or empty import leaves nothing behind


def test_an_existing_customer_can_take_a_later_import(tmp_path: Path) -> None:
    with draft_for(tmp_path, supported_outcomes(2)) as d:
        approve(d, 2)
        first = import_rows(d)
        assert first.customer_id is not None
        approve(d, 3)
        second = import_rows(d, choice=CustomerChoice(existing_customer_id=first.customer_id))
        assert second.created == 1 and second.customer_created is False
        assert second.customer_id == first.customer_id and second.skipped["already_imported"] == 1
        assert len(customers_of(d)) == 1


# ----- the sent date ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sent_on", "code"),
    [
        ("2026-10-11", "sent_on_in_future"),
        ("2099-01-01", "sent_on_in_future"),
        ("2026-10-09", "sent_before_drafted"),
        ("2020-01-01", "sent_before_drafted"),
        ("2026-13-40", "sent_on_invalid"),
        ("20261010", "sent_on_invalid"),
        ("2026-1-1", "sent_on_invalid"),
        ("tomorrow", "sent_on_invalid"),
        ("", "sent_on_invalid"),
        ("2026-10-10 ", "sent_on_invalid"),
    ],
)
def test_the_sent_date_must_be_a_possible_day(  # [NEG]
    tmp_path: Path, sent_on: str, code: str
) -> None:
    with draft_for(tmp_path, supported_outcomes(1)) as d:
        approve(d, 2)
        with pytest.raises(ImportRefusedError) as info:
            import_rows(d, sent_on=sent_on)
        assert info.value.code == code
        assert customers_of(d) == [] and promises_of(d) == []


def test_the_day_the_draft_was_made_and_today_are_both_allowed(tmp_path: Path) -> None:
    with draft_for(tmp_path, supported_outcomes(1)) as d:
        approve(d, 2)
        assert import_rows(d, sent_on="2026-10-10").created == 1  # drafted today, sent today


# ----- per-row skips, each with a code and a count ----------------------------------------------


def test_only_approved_rows_become_promises_and_the_others_are_counted(tmp_path: Path) -> None:
    with draft_for(tmp_path, supported_outcomes(4)) as d:
        edit_cells(d, {(2, REVIEW_COL): "approved", (3, REVIEW_COL): "rejected"})
        out = import_rows(d)
        assert out.created == 1 and [p.run_row_index for p in promises_of(d)] == [2]
        assert out.skipped == skipped(not_approved=3)
        assert out.review == {"approved": 1, "rejected": 1, "unreviewed": 2, "other": 0}


def test_a_blank_and_a_needs_review_row_are_no_answer_even_when_approved(tmp_path: Path) -> None:
    outcomes = [outcome(blank_result("no_evidence")), *supported_outcomes(1)]
    with draft_for(tmp_path, outcomes) as d:
        approve(d, 2, 3)
        out = import_rows(d)
        assert out.created == 1 and out.skipped == skipped(no_answer=1)
        assert [p.run_row_index for p in promises_of(d)] == [3]


def test_an_answer_the_owner_typed_into_a_blank_row_has_no_verified_source(tmp_path: Path) -> None:
    outcomes = [outcome(blank_result("no_evidence")), *supported_outcomes(1)]
    with draft_for(tmp_path, outcomes) as d:
        edit_cells(d, {(2, ANSWER_COL): "Yes, we do that.", (2, REVIEW_COL): "approved"})
        approve(d, 3)
        out = import_rows(d)
        assert out.created == 1 and out.skipped == skipped(no_answer=1)
        assert [p.run_row_index for p in promises_of(d)] == [3]


def test_an_answer_the_owner_blanked_is_no_answer(tmp_path: Path) -> None:
    with draft_for(tmp_path, supported_outcomes(2)) as d:
        approve(d, 2, 3)
        edit_cells(d, {(2, ANSWER_COL): None, (3, ANSWER_COL): "   "})
        out = import_rows(d)
        assert out.created == 0 and out.skipped == skipped(no_answer=2)


NON_TEXT = [
    pytest.param(42, "n", id="number"),
    pytest.param(3.5, "n", id="decimal"),
    pytest.param(datetime.datetime(2026, 10, 1, 12, 0), "d", id="date"),
    pytest.param(True, "b", id="boolean"),
    pytest.param("#N/A", "e", id="error-value"),
]


@pytest.mark.parametrize(("value", "cell_type"), NON_TEXT)
def test_an_answer_that_is_not_text_is_never_converted_so_the_row_is_no_answer(  # [NEG]
    tmp_path: Path, value: object, cell_type: str
) -> None:
    with draft_for(tmp_path, supported_outcomes(2)) as d:
        edit_cells(d, {(2, ANSWER_COL): value, (2, REVIEW_COL): "approved"})
        # The premise: the saved cell really is a number, a date, a boolean or an error value.
        loaded = load_questionnaire(d.path.read_bytes())
        try:
            assert (
                loaded.workbook["Questions"].cell(row=2, column=ANSWER_COL).data_type == cell_type
            )
        finally:
            loaded.close()
        out = import_rows(d)
        assert out.created == 0 and out.skipped == skipped(not_approved=1, no_answer=1)
        assert promises_of(d) == [] and customers_of(d) == []


def test_a_formula_the_owner_typed_is_kept_as_text_and_never_evaluated(tmp_path: Path) -> None:
    formula = '=HYPERLINK("http://example.invalid/","Yes")'
    with draft_for(tmp_path, supported_outcomes(2)) as d:
        approve(d, 2, 3)
        edit_cells(d, {(2, ANSWER_COL): "=1+1", (3, ANSWER_COL): formula})
        out = import_rows(d)
        assert out.created == 2 and out.created_edited == 2
        texts = [p.answer_text for p in promises_of(d)]
        assert texts == ["=1+1", formula]  # exactly what was typed, as a string
        assert all(isinstance(t, str) for t in texts)


def test_an_edited_status_cell_skips_the_row(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, supported_outcomes(4)) as d:
        approve(d, 2, 3, 4, 5)
        edit_cells(
            d,
            {
                (2, STATUS_COL): "SUPPORTED ",  # a trailing space
                (3, STATUS_COL): "PARTIAL",  # a claim ALIBI never made
                (4, STATUS_COL): 7,  # not even text
                (5, STATUS_COL): None,
            },
        )
        out = import_rows(d)
        assert out.created == 0 and out.skipped == skipped(status_edited=4)


def test_an_edited_or_blanked_evidence_cell_skips_the_row(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, supported_outcomes(3)) as d:
        approve(d, 2, 3, 4)
        edit_cells(
            d,
            {
                (2, EVIDENCE_COL): 'policies/other.md:L1 "Everything is encrypted."',
                (3, EVIDENCE_COL): None,  # a blank cell where one was written counts as edited
                (4, EVIDENCE_COL): 12,
            },
        )
        out = import_rows(d)
        assert out.created == 0 and out.skipped == skipped(evidence_edited=3)


def test_a_row_with_no_recorded_baseline_is_skipped(tmp_path: Path) -> None:  # [NEG]
    def one_unreadable(path: str, ranges: object) -> CitedFile | str:
        return "not_found"

    with draft_for(tmp_path, supported_outcomes(2), read_cited=one_unreadable) as d:  # type: ignore[arg-type]
        approve(d, 2, 3)
        out = import_rows(d)
        assert out.created == 0 and out.skipped == skipped(baseline_unavailable=2)
        assert customers_of(d) == []


def test_a_file_that_changed_since_the_answer_carried_its_hash_has_no_baseline(
    tmp_path: Path,
) -> None:  # [NEG]
    other = CitedFile("f" * 64, (lines_hash(12),))
    with draft_for(tmp_path, supported_outcomes(1), read_cited=lambda _p, _r: other) as d:
        approve(d, 2)
        assert import_rows(d).skipped == skipped(baseline_unavailable=1)


def test_a_repeated_import_skips_every_row_as_already_imported(tmp_path: Path) -> None:
    with draft_for(tmp_path, supported_outcomes(3)) as d:
        approve(d, 2, 3, 4)
        first = import_rows(d)
        assert first.customer_id is not None
        again = import_rows(d, choice=CustomerChoice(existing_customer_id=first.customer_id))
        assert again.created == 0 and again.skipped == skipped(already_imported=3)
        assert again.promise_ids == () and len(promises_of(d)) == 3


def test_the_first_rule_that_applies_gives_the_skip_code(tmp_path: Path) -> None:
    outcomes = [outcome(blank_result("no_evidence")), *supported_outcomes(3)]
    with draft_for(tmp_path, outcomes) as d:
        edit_cells(
            d,
            {
                (2, REVIEW_COL): "approved",  # no_answer (blank row) before the edited status
                (2, STATUS_COL): "SUPPORTED",
                (3, REVIEW_COL): "unreviewed",  # not_approved before the edited status
                (3, STATUS_COL): "PARTIAL",
                (4, REVIEW_COL): "approved",  # status_edited before the edited evidence
                (4, STATUS_COL): "PARTIAL",
                (4, EVIDENCE_COL): None,
                (5, REVIEW_COL): "approved",  # evidence_edited
                (5, EVIDENCE_COL): None,
            },
        )
        out = import_rows(d)
        assert out.created == 0
        assert out.skipped == skipped(
            no_answer=1, not_approved=1, status_edited=1, evidence_edited=1
        )


# ----- what the promise keeps ---------------------------------------------------------------------


def test_an_edited_answer_is_flagged_and_stored_as_the_owner_left_it(tmp_path: Path) -> None:
    with draft_for(tmp_path, supported_outcomes(2)) as d:
        approve(d, 2, 3)
        edit_cells(d, {(2, ANSWER_COL): "We encrypt it with AES-256 and rotate keys yearly."})
        out = import_rows(d)
        first, second = promises_of(d)
        assert out.created == 2 and out.created_edited == 1
        assert first.answer_text == "We encrypt it with AES-256 and rotate keys yearly."
        assert first.edited_after_draft is True
        assert second.answer_text == "Yes, answer 2." and second.edited_after_draft is False


def test_an_unedited_answer_that_sanitize_changed_is_not_read_as_edited(tmp_path: Path) -> None:
    raw = "=SUM(1)" + RLO + " hidden\x07 end\r\nsecond line"  # cleaned before it was stored
    with draft_for(tmp_path, [answered(raw, cite=cited(12, 13))]) as d:
        approve(d, 2)
        out = import_rows(d)
        [promise] = promises_of(d)
        assert out.created == 1 and promise.edited_after_draft is False
        assert promise.answer_text != raw and RLO not in promise.answer_text


def test_the_citation_is_the_run_records_even_when_the_evidence_cell_was_rewritten_by_sanitize(
    tmp_path: Path,
) -> None:
    """A quote with a direction control is stored in the Evidence cell behind a warning marker; the
    promise must still hold the quote and path exactly as the run record has them."""
    quote = "Data " + RLO + "at rest"
    with draft_for(tmp_path, [answered("Yes.", cite=cited(12, 13, quote=quote))]) as d:
        approve(d, 2)
        assert import_rows(d).created == 1
        [promise] = promises_of(d)
        assert promise.source_path == "policies/encryption-policy.md"
        assert promise.quote == quote
        assert promise.doc_id == "ENC-001"
        # The premise: the cell the owner saw really did start with the marker.
        recorded = run(d.repo.list_run_answers(d.run_id))[0]
        assert recorded.source_path == promise.source_path
        assert DIRECTION_MARKER not in promise.source_path


def test_two_identical_questions_both_import(tmp_path: Path) -> None:
    rows = [("Q-1", "Do you encrypt data at rest?"), ("Q-2", "Do you encrypt data at rest?")]
    with draft_for(tmp_path, supported_outcomes(2), data=to_bytes(simple_workbook(rows))) as d:
        approve(d, 2, 3)
        out = import_rows(d)
        assert out.created == 2 and out.skipped == NONE_SKIPPED
        first, second = promises_of(d)
        assert first.question_sha256 == second.question_sha256
        assert (first.run_row_index, second.run_row_index) == (2, 3)


# ----- the "other" review values -----------------------------------------------------------------


def test_unrecognized_review_values_are_reported_shortened_and_counted_never_approved(  # [NEG]
    tmp_path: Path,
) -> None:
    long_text = "z" * 70
    with draft_for(tmp_path, supported_outcomes(5)) as d:
        edit_cells(
            d,
            {
                (2, REVIEW_COL): "approved yes",
                (3, REVIEW_COL): "approved yes",
                (4, REVIEW_COL): "appr" + CYRILLIC_A + "ved",
                (5, REVIEW_COL): long_text,
                (6, REVIEW_COL): "approved" + NBSP,
            },
        )
        out = import_rows(d)
        assert out.created == 0 and out.skipped == skipped(not_approved=5)
        assert out.review == {"approved": 0, "rejected": 0, "unreviewed": 0, "other": 5}
        shown = {(v.text, v.truncated): v.count for v in out.other_values}
        assert shown == {
            ("approved yes", False): 2,
            ("appr" + CYRILLIC_A + "ved", False): 1,
            (long_text[:64], True): 1,
            ("approved" + NBSP, False): 1,
        }
        assert all(len(v.text) <= 64 for v in out.other_values)


# ----- the path guard -----------------------------------------------------------------------------


def test_a_file_outside_the_export_folder_is_refused(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, supported_outcomes(1)) as d:
        approve(d, 2)
        outside = tmp_path / "outside.xlsx"
        outside.write_bytes(d.path.read_bytes())
        for name in ("../outside.xlsx", str(outside), "..\\outside.xlsx"):
            with pytest.raises(PathNotAllowedError):
                import_rows(d, file=name)
        assert promises_of(d) == [] and customers_of(d) == []


def test_a_missing_file_is_refused(tmp_path: Path) -> None:  # [NEG]
    with draft_for(tmp_path, supported_outcomes(1)) as d, pytest.raises(PathNotFoundError):
        import_rows(d, file="nothing-here.xlsx")


def test_a_link_to_a_file_outside_the_export_folder_is_refused(  # [NEG]
    tmp_path: Path, symlink_support: None
) -> None:
    with draft_for(tmp_path, supported_outcomes(1)) as d:
        approve(d, 2)
        outside = tmp_path / "outside.xlsx"
        outside.write_bytes(d.path.read_bytes())
        (d.export_root / "link.xlsx").symlink_to(outside)
        with pytest.raises(PathNotAllowedError):
            import_rows(d, file="link.xlsx")
        assert promises_of(d) == []


def test_the_reviewed_copy_may_be_saved_under_another_name_in_the_export_folder(
    tmp_path: Path,
) -> None:
    with draft_for(tmp_path, supported_outcomes(1)) as d:
        name = edit_cells(d, {(2, REVIEW_COL): "approved"}, name="reviewed copy.xlsx")
        assert name == "reviewed copy.xlsx"
        assert import_rows(d, file=name).created == 1


# ----- no content in any log ----------------------------------------------------------------------


def test_a_canary_in_a_name_a_question_an_answer_and_a_review_value_reaches_no_log(  # [NEG]
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    rows = [(f"Q-{n}", f"{CANARY} question {n}?") for n in (1, 2, 3)]
    outcomes = [answered(f"answer {CANARY}", cite=cited(12, 13, quote=f"quote {CANARY}"))] * 3
    with draft_for(tmp_path, outcomes, data=to_bytes(simple_workbook(rows))) as d:
        edit_cells(
            d,
            {
                (2, REVIEW_COL): "approved",
                (2, ANSWER_COL): f"edited answer {CANARY}",
                (3, REVIEW_COL): f"{CANARY}-review",  # an "other" value
                (4, REVIEW_COL): "approved",
            },
        )
        out = import_rows(d, new_name=f"Customer {CANARY}")
        assert out.created == 2 and [v.text for v in out.other_values] == [f"{CANARY}-review"]
        with pytest.raises(
            LedgerRefusedError
        ):  # the same name again: a refusal that names a customer
            import_rows(d, new_name=f"Customer {CANARY}")
        with pytest.raises(ImportRefusedError):  # and a whole-import refusal
            import_rows(d, new_name="Other", sent_on="2099-01-01")
        run(d.service.customers())
        run(d.service.promises(None, False, 1))
    assert CANARY not in caplog.text and RUN_CANARY not in caplog.text
