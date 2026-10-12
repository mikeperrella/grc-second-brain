"""Running a questionnaire (ADR-014): refusals first, events of codes only, one run at a time, and a
file at the end even when Ollama fails part-way (ADR-013, "Ollama fails partway")."""

import asyncio
import json
from collections.abc import Awaitable
from pathlib import Path
from typing import TypeVar

import pytest
from openpyxl import Workbook

from grc_sidecar.errors import (
    IndexNotBuiltError,
    ModelsUnavailableError,
    PathNotAllowedError,
    PathNotFoundError,
    QuestionMappingError,
    RunInProgressError,
    RunNotFoundError,
    VaultChangedError,
    WorkbookRefusedError,
)
from grc_sidecar.questionnaire.loader import load_questionnaire
from grc_sidecar.questionnaire.mapping import QuestionMapping
from grc_sidecar.questionnaire.package import sha256_hex
from grc_sidecar.questionnaire.runs import KEPT_RUNS, RunEvent, RunState

from .runskit import (
    CANARY,
    DIGEST,
    FILE_NAME,
    MAPPING,
    MODEL,
    FakeGate,
    FakeModelReader,
    FakeVault,
    Setup,
    make_workbook,
    models_unavailable,
    outcome,
    setup,
    supported,
    unavailable_outcome,
)
from .xlsxkit import rebuild, simple_workbook, to_bytes

T = TypeVar("T")


SCENARIO_TIMEOUT_SECONDS = 30  # a broken control must fail a test, not hang the suite


def run(coroutine: Awaitable[T]) -> T:
    return asyncio.run(asyncio.wait_for(coroutine, SCENARIO_TIMEOUT_SECONDS))  # type: ignore[arg-type]


async def finish(s: Setup, state: RunState) -> list[RunEvent]:
    """Every event of a run, read from the first to the end (the stream ends by itself)."""
    return [event async for event in s.runs.events(state.run_id)]


async def started_and_finished(s: Setup) -> tuple[RunState, list[RunEvent]]:
    state = await s.runs.start(FILE_NAME, MAPPING)
    return state, await finish(s, state)


def output_workbook(s: Setup) -> Workbook:
    [name] = s.exports()
    return load_questionnaire((s.export_root / name).read_bytes()).workbook


# ----- a normal run -------------------------------------------------------------------------


def test_a_run_answers_every_question_and_writes_the_file(tmp_path: Path) -> None:
    s = setup(tmp_path, count=3)

    async def scenario() -> None:
        state, events = await started_and_finished(s)
        assert [e.name for e in events] == ["started", "progress", "progress", "progress", "done"]
        assert state.status == "complete" and state.completed == 3
        assert events[-1].data == {
            "complete": True,
            "total": 3,
            "counts": {"SUPPORTED": 3, "PARTIAL": 0, "NEEDS_REVIEW": 0},
        }
        assert state.summary is not None and state.summary.file_name in s.exports()
        sheet = output_workbook(s)["Questions"]
        assert [sheet.cell(row=r, column=5).value for r in (2, 3, 4)] == ["SUPPORTED"] * 3

    run(scenario())
    assert len(s.answerer.questions) == 3


def test_the_summary_records_what_a_reviewer_needs(tmp_path: Path) -> None:
    s = setup(tmp_path, reader=FakeModelReader("sha256:before", "sha256:after"))

    async def scenario() -> None:
        state, _ = await started_and_finished(s)
        summary = state.summary
        assert summary is not None
        assert (summary.model, summary.digest_before, summary.digest_after) == (
            MODEL,
            "sha256:before",
            "sha256:after",
        )  # a model replaced during the run is visible
        assert summary.template_sha256 == "t" * 64
        assert summary.input_sha256 == sha256_hex((s.questionnaire_root / FILE_NAME).read_bytes())
        assert summary.complete is True and summary.rows_written == 3
        assert summary.counts == {"SUPPORTED": 3, "PARTIAL": 0, "NEEDS_REVIEW": 0}

    run(scenario())


def test_a_run_asks_for_both_required_models(tmp_path: Path) -> None:
    s = setup(tmp_path)
    run(started_and_finished(s))
    assert s.gate.required == [("nomic-embed-text", MODEL)]


# ----- events carry codes, never text -------------------------------------------------------


def test_events_hold_only_codes_counts_and_the_run_id(tmp_path: Path) -> None:  # [NEG]
    rows = [(f"Q-{n}", f"{CANARY} question {n}?") for n in range(1, 4)]
    s = setup(tmp_path, data=to_bytes(simple_workbook(rows)))
    s.answerer.script = [outcome(supported(f"answer with {CANARY}"))] * 3

    async def scenario() -> None:
        state, events = await started_and_finished(s)
        wire = json.dumps([[e.name, e.data] for e in events])
        assert CANARY not in wire
        assert FILE_NAME not in wire and "Vendor" not in wire and "encryption-policy" not in wire
        assert "AES-256" not in wire and "Yes." not in wire
        allowed = {"run_id", "total", "index", "status", "reasons", "complete", "counts", "code"}
        for event in events:
            assert set(event.data) <= allowed
        assert CANARY not in json.dumps(state.summary.__dict__, default=str)

    run(scenario())
    assert all(CANARY in q for q in s.answerer.questions)  # the questions did reach the answerer


def test_events_can_be_replayed_from_the_first_and_end_by_themselves(tmp_path: Path) -> None:
    s = setup(tmp_path)

    async def scenario() -> None:
        state, first = await started_and_finished(s)
        again = await finish(s, state)  # a second consumer, after the run is over
        assert [(e.name, e.data) for e in again] == [(e.name, e.data) for e in first]

    run(scenario())


def test_a_consumer_that_joins_late_still_sees_every_event(tmp_path: Path) -> None:
    s = setup(tmp_path)
    s.answerer.hold = hold = asyncio.Event()

    async def scenario() -> None:
        state = await s.runs.start(FILE_NAME, MAPPING)
        await s.answerer.entered.wait()  # the first question is being answered, nothing is done
        reader = asyncio.create_task(finish(s, state))
        await asyncio.sleep(0)  # let it start waiting
        hold.set()
        events = await reader
        assert events[0].name == "started" and events[-1].name == "done"
        assert [e.data["index"] for e in events if e.name == "progress"] == [1, 2, 3]

    run(scenario())


# ----- one run at a time ---------------------------------------------------------------------


def test_a_second_run_is_refused_while_one_is_going(tmp_path: Path) -> None:  # [NEG]
    s = setup(tmp_path)
    s.answerer.hold = hold = asyncio.Event()

    async def scenario() -> None:
        first = await s.runs.start(FILE_NAME, MAPPING)
        await s.answerer.entered.wait()
        with pytest.raises(RunInProgressError):
            await s.runs.start(FILE_NAME, MAPPING)
        hold.set()
        await finish(s, first)
        second = await s.runs.start(FILE_NAME, MAPPING)  # fine once the first has ended
        await finish(s, second)
        assert second.run_id != first.run_id

    run(scenario())
    assert len(s.exports()) == 2


# ----- refusals come before any work, and leave nothing behind -------------------------------


def refused(s: Setup, error: type[Exception], mapping: QuestionMapping = MAPPING) -> None:
    async def scenario() -> None:
        with pytest.raises(error):
            await s.runs.start(FILE_NAME, mapping)
        # Nothing was left running: a later run is not blocked, and nothing was written.
        assert s.exports() == []
        assert s.answerer.questions == []

    run(scenario())


def test_a_missing_model_refuses_the_run(tmp_path: Path) -> None:  # [NEG]
    refused(setup(tmp_path, gate=FakeGate(models_unavailable())), ModelsUnavailableError)


def test_an_unreadable_model_digest_refuses_the_run(tmp_path: Path) -> None:  # [NEG]
    s = setup(tmp_path, reader=FakeModelReader(ModelsUnavailableError((MODEL,))))
    refused(s, ModelsUnavailableError)


def test_changed_vault_files_refuse_the_run(tmp_path: Path) -> None:  # [NEG]
    refused(setup(tmp_path, vault=FakeVault(withheld=("a.md",))), VaultChangedError)


def test_an_index_that_is_not_built_refuses_the_run(tmp_path: Path) -> None:  # [NEG]
    refused(setup(tmp_path, vault=FakeVault(error=IndexNotBuiltError())), IndexNotBuiltError)


def test_a_mapping_that_does_not_fit_refuses_the_run(tmp_path: Path) -> None:  # [NEG]
    wrong = QuestionMapping(sheet="No such sheet", header_row=1, question_col=2)
    refused(setup(tmp_path), QuestionMappingError, wrong)


def test_a_workbook_with_no_questions_refuses_the_run(tmp_path: Path) -> None:  # [NEG]
    s = setup(tmp_path, data=to_bytes(simple_workbook([])))
    refused(s, QuestionMappingError)


def test_a_workbook_that_is_refused_by_the_scan_refuses_the_run(tmp_path: Path) -> None:  # [NEG]
    hostile = rebuild(
        make_workbook(2),
        replace={
            "xl/worksheets/sheet1.xml": b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]><a/>'
        },
    )
    refused(setup(tmp_path, data=hostile), WorkbookRefusedError)


def test_a_file_outside_the_questionnaire_folder_refuses_the_run(tmp_path: Path) -> None:  # [NEG]
    s = setup(tmp_path)
    (tmp_path / "outside.xlsx").write_bytes(make_workbook(1))

    async def scenario() -> None:
        with pytest.raises(PathNotAllowedError):
            await s.runs.start("../outside.xlsx", MAPPING)
        with pytest.raises(PathNotFoundError):
            await s.runs.start("missing.xlsx", MAPPING)

    run(scenario())
    assert s.exports() == [] and s.answerer.questions == []


def test_a_refused_run_is_not_remembered(tmp_path: Path) -> None:
    s = setup(tmp_path, vault=FakeVault(withheld=("a.md",)))

    async def scenario() -> None:
        with pytest.raises(VaultChangedError):
            await s.runs.start(FILE_NAME, MAPPING)
        with pytest.raises(RunNotFoundError):
            s.runs.get(f"{1:032x}")

    run(scenario())


# ----- Ollama fails part-way: stop asking, still write the file -------------------------------


def test_when_the_model_goes_away_the_rest_is_blank_and_the_file_is_still_written(
    tmp_path: Path,
) -> None:  # [NEG]
    s = setup(tmp_path, count=4)
    s.answerer.script = [outcome(supported()), unavailable_outcome()]

    async def scenario() -> None:
        state, events = await started_and_finished(s)
        assert state.status == "stopped"
        assert events[-1].name == "done" and events[-1].data["complete"] is False
        statuses = [e.data["status"] for e in events if e.name == "progress"]
        assert statuses == ["SUPPORTED", "NEEDS_REVIEW", "NEEDS_REVIEW", "NEEDS_REVIEW"]
        reasons = [e.data["reasons"] for e in events if e.name == "progress"][1:]
        assert reasons == [["model_unavailable"]] * 3
        sheet = output_workbook(s)["Questions"]
        # Columns: 3 answer, 5 status, 6 reason. The first question was answered, the rest blank.
        assert sheet.cell(row=2, column=3).value == "Yes. Data at rest is encrypted."
        assert [sheet.cell(row=r, column=3).value for r in (3, 4, 5)] == [None, None, None]
        assert [sheet.cell(row=r, column=5).value for r in (3, 4, 5)] == ["NEEDS_REVIEW"] * 3
        assert [sheet.cell(row=r, column=6).value for r in (3, 4, 5)] == ["model_unavailable"] * 3
        assert state.summary is not None and state.summary.complete is False

    run(scenario())
    assert len(s.answerer.questions) == 2  # the model was not asked again after it went away


def test_a_generation_timeout_fails_closed_through_the_real_service(
    tmp_path: Path,
) -> None:  # [NEG]
    """A read timeout is a `GenerationUnavailableError`; the real answer service turns it into a
    blank `model_unavailable` result, and the run stops asking and writes its file (D13)."""
    from grc_sidecar.errors import GenerationUnavailableError

    from .servicekit import answering

    async def scenario() -> None:
        async with answering(tmp_path / "svc", [GenerationUnavailableError()]) as (
            service,
            model,
            _stack,
        ):
            s = setup(tmp_path, count=3, answerer=service)  # type: ignore[arg-type]
            state, events = await started_and_finished(s)
            assert state.status == "stopped"
            assert [e.data["status"] for e in events if e.name == "progress"] == [
                "NEEDS_REVIEW"
            ] * 3
            assert len(model.requests) == 1  # only the first question reached the model
            assert s.exports()

    run(scenario())


# ----- failures after the start: a typed code, no file, no text ------------------------------


def test_an_index_error_during_the_run_fails_it_with_a_code_and_no_file(tmp_path: Path) -> None:
    s = setup(tmp_path)
    s.answerer.script = [outcome(supported()), IndexNotBuiltError()]

    async def scenario() -> None:
        state, events = await started_and_finished(s)
        assert state.status == "failed" and state.failure == "index_not_built"
        assert events[-1].name == "failed" and events[-1].data == {"code": "index_not_built"}

    run(scenario())
    assert s.exports() == []


def test_an_unexpected_error_is_a_fixed_code_and_never_its_text(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:  # [NEG]
    s = setup(tmp_path)
    s.answerer.script = [RuntimeError(f"boom {CANARY}")]
    caplog.set_level("DEBUG")

    async def scenario() -> None:
        state, events = await started_and_finished(s)
        assert state.failure == "internal_error"
        assert CANARY not in json.dumps([e.data for e in events])

    run(scenario())
    assert s.exports() == []
    assert CANARY not in caplog.text


def test_an_input_that_changes_during_the_run_writes_nothing(tmp_path: Path) -> None:  # [NEG]
    s = setup(tmp_path)

    def change_the_file(_index: int) -> None:
        (s.questionnaire_root / FILE_NAME).write_bytes(make_workbook(9))

    s.answerer.before_each = change_the_file

    async def scenario() -> None:
        state, events = await started_and_finished(s)
        assert state.status == "failed" and state.failure == "write_input_changed"
        assert events[-1].name == "failed"

    run(scenario())
    assert s.exports() == []


def test_a_cited_file_that_changed_blanks_its_answer_in_the_output(tmp_path: Path) -> None:  # [NEG]
    s = setup(tmp_path, current_sha256=lambda _path: "b" * 64)

    async def scenario() -> None:
        state, _ = await started_and_finished(s)
        assert state.status == "complete"
        sheet = output_workbook(s)["Questions"]
        assert sheet.cell(row=2, column=3).value is None
        assert sheet.cell(row=2, column=5).value == "NEEDS_REVIEW"
        assert sheet.cell(row=2, column=6).value == "source_changed"

    run(scenario())


def test_stopping_the_app_cancels_a_run_that_is_going(tmp_path: Path) -> None:
    s = setup(tmp_path)
    s.answerer.hold = asyncio.Event()  # never set: the first question never finishes

    async def scenario() -> None:
        state = await s.runs.start(FILE_NAME, MAPPING)
        await s.answerer.entered.wait()
        await s.runs.aclose()
        assert state.status == "failed" and state.failure == "cancelled"
        assert [e.name for e in state.events][-1] == "failed"

    run(scenario())
    assert s.exports() == []


# ----- status, inspect and memory -------------------------------------------------------------


def test_an_unknown_run_is_a_typed_error(tmp_path: Path) -> None:  # [NEG]
    s = setup(tmp_path)
    with pytest.raises(RunNotFoundError):
        s.runs.get("0" * 32)

    async def scenario() -> None:
        with pytest.raises(RunNotFoundError):
            await anext(s.runs.events("0" * 32))

    run(scenario())


def test_only_the_last_few_finished_runs_are_kept(tmp_path: Path) -> None:
    s = setup(tmp_path, count=1)

    async def scenario() -> None:
        ids = []
        for _ in range(KEPT_RUNS + 3):
            state = await s.runs.start(FILE_NAME, MAPPING)
            await finish(s, state)
            ids.append(state.run_id)
            (s.export_root / s.exports()[0]).unlink()  # keep the folder tidy between runs
        assert s.runs.get(ids[-1]).status == "complete"
        with pytest.raises(RunNotFoundError):
            s.runs.get(ids[0])

    run(scenario())


def test_inspect_reports_the_sheets_and_a_suggestion_and_changes_nothing(tmp_path: Path) -> None:
    s = setup(tmp_path, count=2)
    before = sha256_hex((s.questionnaire_root / FILE_NAME).read_bytes())

    async def scenario() -> None:
        found = await s.runs.inspect(FILE_NAME)
        assert found.sheets == (("Questions", 3, 2),)
        assert found.suggestion == MAPPING
        assert found.macro_enabled is False and found.input_sha256 == before

    run(scenario())
    assert s.exports() == [] and s.answerer.questions == []
    assert sha256_hex((s.questionnaire_root / FILE_NAME).read_bytes()) == before


def test_inspect_refuses_what_a_run_refuses(tmp_path: Path) -> None:  # [NEG]
    s = setup(tmp_path)
    (s.questionnaire_root / "bad.xlsx").write_bytes(b"not a workbook")

    async def scenario() -> None:
        with pytest.raises(WorkbookRefusedError):
            await s.runs.inspect("bad.xlsx")
        with pytest.raises(PathNotAllowedError):
            await s.runs.inspect("../x.xlsx")

    run(scenario())


def test_the_digest_constant_is_what_the_fake_reader_returns() -> None:
    assert DIGEST == "sha256:digest-one"  # the other tests rely on it
