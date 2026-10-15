"""The one-heavy-job slot (ADR-014, ADR-019 decision 11): taken once, refused while held, and
released on a refusal, an exception and a cancelled request, for both kinds of job."""

import asyncio
import dataclasses
from pathlib import Path

import pytest

from grc_sidecar.contradictions.models import NUMERIC_ONLY_STATEMENT, DetectionReport
from grc_sidecar.contradictions.runs import ContradictionRuns
from grc_sidecar.errors import IndexNotBuiltError, RunInProgressError
from grc_sidecar.jobs import JobSlot
from grc_sidecar.questionnaire.runs import QuestionnaireRuns

from .contradictionmodelkit import run_scenario
from .runskit import FILE_NAME, MAPPING, FakeGate, ScriptedAnswerer, models_unavailable, setup

EMPTY = DetectionReport(
    findings=(),
    dropped={},
    chunks_read=0,
    excluded_chunks=0,
    claims_found=0,
    pairs_generated=0,
    withheld_stale=0,
    says=NUMERIC_ONLY_STATEMENT,
)


class HeldDetector:
    """A detector that waits for the test, then returns a report or raises."""

    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def detect(self, *, scope: str | None, cap: int) -> DetectionReport:
        self.entered.set()
        await self.release.wait()
        if self.error is not None:
            raise self.error
        return EMPTY


def contradiction_runs(slot: JobSlot, detector: HeldDetector) -> ContradictionRuns:
    return ContradictionRuns(detector, slot, "llama3.1:8b")


# ----- the slot itself ---------------------------------------------------------------------------


def test_a_second_begin_is_refused_until_the_slot_is_released() -> None:  # [NEG]
    slot = JobSlot()
    assert not slot.busy
    slot.begin()
    assert slot.busy
    with pytest.raises(RunInProgressError):
        slot.begin()
    slot.end()
    assert not slot.busy
    slot.begin()  # free again
    slot.end()
    slot.end()  # releasing a free slot is harmless
    assert not slot.busy


# ----- contradiction runs -------------------------------------------------------------------------


def test_a_contradiction_run_holds_the_slot_and_frees_it_when_done() -> None:
    async def scenario() -> None:
        slot, detector = JobSlot(), HeldDetector()
        runs = contradiction_runs(slot, detector)
        task = asyncio.create_task(runs.run(scope=None, cap=5))
        await detector.entered.wait()
        assert slot.busy
        with pytest.raises(RunInProgressError):  # a second contradiction run is refused too
            await runs.run(scope=None, cap=5)
        detector.release.set()
        assert (await task).says == NUMERIC_ONLY_STATEMENT
        assert not slot.busy

    run_scenario(scenario())


def test_the_slot_is_released_when_the_detector_raises() -> None:
    async def scenario() -> None:
        slot, detector = JobSlot(), HeldDetector(error=IndexNotBuiltError())
        detector.release.set()
        with pytest.raises(IndexNotBuiltError):  # a refusal
            await contradiction_runs(slot, detector).run(scope=None, cap=5)
        assert not slot.busy
        detector.error = RuntimeError("boom")
        with pytest.raises(RuntimeError):  # an exception
            await contradiction_runs(slot, detector).run(scope=None, cap=5)
        assert not slot.busy

    run_scenario(scenario())


def test_the_slot_is_released_when_the_request_is_cancelled() -> None:
    async def scenario() -> None:
        slot, detector = JobSlot(), HeldDetector()
        task = asyncio.create_task(contradiction_runs(slot, detector).run(scope=None, cap=5))
        await detector.entered.wait()
        assert slot.busy
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not slot.busy

    run_scenario(scenario())


# ----- questionnaire runs share the slot ----------------------------------------------------------


def questionnaire_runs(
    tmp_path: Path, slot: JobSlot, **kwargs: object
) -> tuple[QuestionnaireRuns, ScriptedAnswerer]:
    answerer = kwargs.pop("answerer", None) or ScriptedAnswerer()
    s = setup(tmp_path, answerer=answerer, **kwargs)  # type: ignore[arg-type]
    return QuestionnaireRuns(dataclasses.replace(s.deps, slot=slot)), answerer  # type: ignore[return-value]


def test_a_questionnaire_run_holds_the_slot_and_a_contradiction_run_is_refused(
    tmp_path: Path,
) -> None:  # [NEG]
    async def scenario() -> None:
        slot = JobSlot()
        hold = asyncio.Event()
        runs, answerer = questionnaire_runs(tmp_path, slot, answerer=ScriptedAnswerer(hold=hold))
        state = await runs.start(FILE_NAME, MAPPING)
        await answerer.entered.wait()
        assert slot.busy
        detector = HeldDetector()
        detector.release.set()
        with pytest.raises(RunInProgressError):
            await contradiction_runs(slot, detector).run(scope=None, cap=5)
        assert not detector.entered.is_set()  # the detector was never started
        hold.set()
        async for _ in runs.events(state.run_id):
            pass
        assert not slot.busy  # a finished run gives the slot back
        assert (await contradiction_runs(slot, detector).run(scope=None, cap=5)) is EMPTY

    run_scenario(scenario())


def test_a_contradiction_run_holds_the_slot_and_a_questionnaire_start_is_refused(
    tmp_path: Path,
) -> None:  # [NEG]
    async def scenario() -> None:
        slot, detector = JobSlot(), HeldDetector()
        runs, answerer = questionnaire_runs(tmp_path, slot)
        task = asyncio.create_task(contradiction_runs(slot, detector).run(scope=None, cap=5))
        await detector.entered.wait()
        with pytest.raises(RunInProgressError):
            await runs.start(FILE_NAME, MAPPING)
        assert answerer.questions == []  # nothing was asked
        detector.release.set()
        await task
        state = await runs.start(FILE_NAME, MAPPING)  # free again
        async for _ in runs.events(state.run_id):
            pass

    run_scenario(scenario())


def test_a_refused_questionnaire_start_does_not_keep_the_slot(tmp_path: Path) -> None:
    async def scenario() -> None:
        slot = JobSlot()
        runs, _ = questionnaire_runs(tmp_path, slot, gate=FakeGate(models_unavailable()))
        with pytest.raises(Exception):  # noqa: B017 - the gate refuses: the type is not the point
            await runs.start(FILE_NAME, MAPPING)
        assert not slot.busy

    run_scenario(scenario())


def test_a_questionnaire_run_that_ends_in_failure_gives_the_slot_back(tmp_path: Path) -> None:
    async def scenario() -> None:
        slot = JobSlot()
        runs, _ = questionnaire_runs(
            tmp_path, slot, answerer=ScriptedAnswerer(script=[RuntimeError("boom")])
        )
        state = await runs.start(FILE_NAME, MAPPING)
        async for _ in runs.events(state.run_id):
            pass
        assert runs.get(state.run_id).status == "failed"
        assert not slot.busy

    run_scenario(scenario())
