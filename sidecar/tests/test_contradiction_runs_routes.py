"""`POST /contradictions/runs` and `GET /contradictions/runs/{run_id}` (ADR-019 addendum, G40, chunk
65c): a run you start and then poll. Authenticated, typed refusals, the shared one-job slot in both
directions, the slot freed after a failure and on shutdown, only the last few finished runs kept,
and no report, quote or model text in a file, a log or an error body. A scripted fake model only;
no test reaches port 11434. All data is synthetic.
"""

import asyncio
import dataclasses
import hashlib
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from grc_sidecar.api.contradictions import RunStatusResponse
from grc_sidecar.config import Settings
from grc_sidecar.contradiction_runtime import build_contradiction_runtime
from grc_sidecar.contradictions.models import DetectionReport
from grc_sidecar.contradictions.run_jobs import KEPT_RUNS, ContradictionRun, ContradictionRunJobs
from grc_sidecar.contradictions.runs import ContradictionRuns
from grc_sidecar.contradictions.service import ContradictionDetector, ProgressCallback
from grc_sidecar.errors import IndexNotBuiltError, RunInProgressError
from grc_sidecar.jobs import JobSlot
from grc_sidecar.main import create_app
from grc_sidecar.questionnaire.runs import QuestionnaireRuns
from grc_sidecar.repositories.sqlite_vec_repo import SqliteVecRepository
from grc_sidecar.security.net import create_async_client
from grc_sidecar.vault.index_jobs import IndexJobs

from .contradictionmodelkit import CANARY, MODEL, repeat, reply, run_scenario
from .runskit import FILE_NAME, MAPPING, FakeGate, ScriptedAnswerer, setup
from .test_contradiction_routes import canary_vault, real_runs, served
from .test_index_routes import GatedEmbedder, rig
from .test_job_slot import EMPTY
from .vaultkit import CONTRADICTIONS_VAULT

pytestmark = pytest.mark.usefixtures("clean_grc_env")

TOKEN = hashlib.sha256(b"grc-test-token").hexdigest()
AUTH = {"X-GRC-Auth": TOKEN}
START = "/contradictions/runs"
RUN_ID = re.compile(r"[0-9a-f]{32}")
STATUS_KEYS = {"run_id", "status", "pairs_total", "pairs_done", "failure", "report"}


class ScriptedDetector:
    """A detector that reports progress under the test's control.

    `gates[i]` lets pair `i` finish. `error` is raised after the last pair. `index_error` is raised
    by `ensure_index`, as the real one does when there is no index."""

    def __init__(
        self,
        total: int = 2,
        *,
        error: BaseException | None = None,
        index_error: BaseException | None = None,
    ) -> None:
        self.total = total
        self.error = error
        self.index_error = index_error
        self.entered = asyncio.Event()
        self.gates = [asyncio.Event() for _ in range(total)]
        self.cancelled = False
        self.calls: list[tuple[str | None, int]] = []

    def open_all(self) -> None:
        for gate in self.gates:
            gate.set()

    async def ensure_index(self) -> None:
        if self.index_error is not None:
            raise self.index_error

    async def detect(
        self, *, scope: str | None, cap: int, on_progress: ProgressCallback | None = None
    ) -> DetectionReport:
        self.calls.append((scope, cap))
        self.entered.set()
        try:
            if on_progress is not None:
                on_progress(0, self.total)
            for done, gate in enumerate(self.gates, start=1):
                await gate.wait()
                if on_progress is not None:
                    on_progress(done, self.total)
            if self.error is not None:
                raise self.error
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return EMPTY


@asynccontextmanager
async def client_for(
    contradictions: ContradictionRuns | None,
    questionnaire: QuestionnaireRuns | None = None,
    index_jobs: IndexJobs | None = None,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        TOKEN, contradictions=contradictions, questionnaire=questionnaire, index_jobs=index_jobs
    )
    async with create_async_client(
        "http://127.0.0.1:8000", transport=httpx.ASGITransport(app=app)
    ) as client:
        yield client


async def start(client: httpx.AsyncClient, **body: object) -> httpx.Response:
    return await client.post(START, json=body, headers=AUTH)


async def status_of(client: httpx.AsyncClient, run_id: str) -> httpx.Response:
    return await client.get(f"{START}/{run_id}", headers=AUTH)


async def until(client: httpx.AsyncClient, run_id: str, wanted: str) -> dict[str, object]:
    for _ in range(500):
        body = (await status_of(client, run_id)).json()
        if body["status"] == wanted:
            return dict(body)
        await asyncio.sleep(0.01)
    raise AssertionError(f"the run never reached {wanted}")


def runs_for(detector: ScriptedDetector, slot: JobSlot | None = None) -> ContradictionRuns:
    return ContradictionRuns(detector, slot or JobSlot(), MODEL)


# ----- the token, the unset vault, the request ----------------------------------------------------


@pytest.mark.parametrize("headers", [{}, {"X-GRC-Auth": "0" * 64}, {"X-GRC-Auth": ""}])
def test_both_routes_need_the_token(headers: dict[str, str]) -> None:  # [NEG]
    async def scenario() -> None:
        detector = ScriptedDetector()
        async with client_for(runs_for(detector)) as client:
            posted = await client.post(START, json={}, headers=headers)
            polled = await client.get(f"{START}/{'a' * 32}", headers=headers)
            for response in (posted, polled):
                assert response.status_code == 401 and response.content == b""  # no detail (3.4)
        assert not detector.entered.is_set()  # nothing ran

    run_scenario(scenario())


def test_without_a_configured_vault_both_routes_refuse_503() -> None:  # [NEG]
    async def scenario() -> None:
        async with client_for(None) as client:
            for response in (await start(client), await status_of(client, "a" * 32)):
                assert response.status_code == 503
                assert response.json() == {"error": "vault_root_not_configured"}

    run_scenario(scenario())


@pytest.mark.parametrize(
    "body",
    [
        {"url": "http://evil.example/x"},
        {"token": CANARY},
        {"credential": CANARY},
        {"host": "evil.example"},
        {"cap": 0},
        {"cap": 101},
        {"cap": CANARY},
        {"scope": ""},
        {"scope": "../outside"},
        {"scope": "C:/drive"},
        {"scope": "x" * 300},
    ],
)
def test_a_bad_or_unknown_field_is_refused_without_echoing_its_value(
    body: dict[str, object],
) -> None:  # [NEG]
    async def scenario() -> None:
        detector = ScriptedDetector()
        slot = JobSlot()
        async with client_for(runs_for(detector, slot)) as client:
            response = await client.post(START, json=body, headers=AUTH)
            assert response.status_code == 422
            payload = response.json()
            assert payload["error"] == "invalid_request" and payload["fields"]
            for secret in (CANARY, "evil.example", "outside", "C:/drive"):
                assert secret not in response.text
        assert not detector.entered.is_set() and not slot.busy  # refused before the slot

    run_scenario(scenario())


def test_the_start_request_is_the_existing_one_and_the_status_has_the_listed_fields() -> None:
    from grc_sidecar.api.contradictions import RunRequest, StartResponse

    assert set(RunRequest.model_fields) == {"scope", "cap"}
    assert RunRequest.model_config["extra"] == "forbid"
    assert set(RunStatusResponse.model_fields) == STATUS_KEYS
    assert set(StartResponse.model_fields) == {"run_id"}
    for model in (RunStatusResponse, StartResponse):
        assert model.model_config["extra"] == "forbid"


@pytest.mark.parametrize("run_id", ["XYZ", "a" * 7, "a" * 65, "A" * 32, "../etc", "a" * 31 + "g"])
def test_a_malformed_run_id_is_a_422_and_an_unknown_one_a_404(run_id: str) -> None:  # [NEG]
    async def scenario() -> None:
        async with client_for(runs_for(ScriptedDetector())) as client:
            malformed = await status_of(client, run_id)
            assert malformed.status_code in {404, 422}  # a path with "/" or ".." never matches
            if malformed.status_code == 422:
                assert run_id not in malformed.text
            unknown = await status_of(client, "a" * 32)
            assert unknown.status_code == 404 and unknown.json() == {"error": "run_not_found"}

    run_scenario(scenario())


# ----- start, poll, complete ----------------------------------------------------------------------


def test_start_answers_202_with_a_run_id_and_the_run_reports_progress_to_complete() -> None:
    async def scenario() -> None:
        detector = ScriptedDetector(total=2)
        slot = JobSlot()
        async with client_for(runs_for(detector, slot)) as client:
            started = await start(client, scope="a.md", cap=7)
            assert started.status_code == 202
            run_id = started.json()["run_id"]
            assert set(started.json()) == {"run_id"} and RUN_ID.fullmatch(run_id)
            await detector.entered.wait()
            assert detector.calls == [("a.md", 7)]
            assert slot.busy  # the run holds the slot while it goes on
            running = (await status_of(client, run_id)).json()
            assert set(running) == STATUS_KEYS
            assert running["status"] == "running" and running["report"] is None
            assert (running["pairs_total"], running["pairs_done"]) == (2, 0)
            detector.gates[0].set()
            for _ in range(100):  # pair 1 done, pair 2 not
                one = (await status_of(client, run_id)).json()
                if one["pairs_done"] == 1:
                    break
                await asyncio.sleep(0.01)
            assert (one["status"], one["pairs_total"], one["pairs_done"]) == ("running", 2, 1)
            detector.gates[1].set()
            done = await until(client, run_id, "complete")
            assert (done["pairs_total"], done["pairs_done"], done["failure"]) == (2, 2, None)
            assert isinstance(done["report"], dict) and done["report"]["model"] == MODEL
            assert not slot.busy  # given back at the end

    run_scenario(scenario())


def test_the_total_is_null_until_it_is_known() -> None:
    async def scenario() -> None:
        runs = runs_for(ScriptedDetector(total=0))
        run_id = await runs.start(scope=None, cap=5)  # the task has not run yet
        snapshot = runs.get(run_id)
        assert snapshot.status == "running" and snapshot.pairs_total is None
        await runs.aclose()

    run_scenario(scenario())


def test_a_started_run_reports_what_the_one_response_route_reports(tmp_path: Path) -> None:
    async def scenario() -> None:
        steps = repeat(reply(True), 50)
        async with served(CONTRADICTIONS_VAULT, tmp_path / "one", steps) as (client, _):
            direct = (
                await client.post("/contradictions/run", json={"cap": 3}, headers=AUTH)
            ).json()
        async with served(CONTRADICTIONS_VAULT, tmp_path / "two", steps) as (client, _):
            run_id = (await start(client, cap=3)).json()["run_id"]
            done = await until(client, run_id, "complete")
        assert done["report"] == direct
        assert (done["pairs_total"], done["pairs_done"]) == (3, 3)
        assert len(direct["findings"]) == 3

    run_scenario(scenario())


def test_the_detector_reports_progress_with_integers_only(tmp_path: Path) -> None:
    async def scenario() -> None:
        steps = repeat(reply(True), 50)
        async with real_runs(CONTRADICTIONS_VAULT, tmp_path / "idx", steps) as (runs, _):
            detector = runs._detector  # the real detector the runs wrap
            assert isinstance(detector, ContradictionDetector)
            seen: list[tuple[int, int]] = []
            await detector.detect(scope=None, cap=3, on_progress=lambda d, t: seen.append((d, t)))
        assert seen[0] == (0, 3) and seen[-1] == (3, 3)
        assert [d for d, _ in seen] == sorted(d for d, _ in seen)  # never goes backwards
        assert {t for _, t in seen} == {3}

    run_scenario(scenario())


# ----- the shared slot, both ways -----------------------------------------------------------------


def test_a_second_start_gets_409_while_a_run_goes_on_and_the_run_is_not_disturbed() -> (
    None
):  # [NEG]
    async def scenario() -> None:
        detector, slot = ScriptedDetector(), JobSlot()
        async with client_for(runs_for(detector, slot)) as client:
            first = (await start(client)).json()["run_id"]
            await detector.entered.wait()
            second = await start(client)
            assert second.status_code == 409 and second.json() == {"error": "run_in_progress"}
            assert slot.busy  # a refusal does not free the other run's slot
            detector.open_all()
            assert (await until(client, first, "complete"))["report"] is not None

    run_scenario(scenario())


def test_a_questionnaire_run_holding_the_slot_refuses_the_start(tmp_path: Path) -> None:  # [NEG]
    async def scenario() -> None:
        slot, hold = JobSlot(), asyncio.Event()
        answerer = ScriptedAnswerer(hold=hold)
        s = setup(tmp_path, answerer=answerer)
        questions = QuestionnaireRuns(dataclasses.replace(s.deps, slot=slot))
        detector = ScriptedDetector(total=0)
        async with client_for(runs_for(detector, slot), questions) as client:
            began = await client.post(
                "/questionnaire/runs",
                json={"file": FILE_NAME, "mapping": MAPPING.model_dump()},
                headers=AUTH,
            )
            assert began.status_code == 202
            await answerer.entered.wait()
            refused = await start(client)
            assert refused.status_code == 409 and refused.json() == {"error": "run_in_progress"}
            assert not detector.entered.is_set()  # no task was made
            hold.set()
            await client.get(f"/questionnaire/runs/{began.json()['run_id']}/events", headers=AUTH)
            assert (await start(client)).status_code == 202  # free again

    run_scenario(scenario())


def test_a_started_run_holding_the_slot_refuses_a_questionnaire_start(
    tmp_path: Path,
) -> None:  # [NEG]
    async def scenario() -> None:
        slot, detector = JobSlot(), ScriptedDetector()
        answerer = ScriptedAnswerer()
        s = setup(tmp_path, answerer=answerer)
        questions = QuestionnaireRuns(dataclasses.replace(s.deps, slot=slot))
        async with client_for(runs_for(detector, slot), questions) as client:
            assert (await start(client)).status_code == 202
            await detector.entered.wait()
            refused = await client.post(
                "/questionnaire/runs",
                json={"file": FILE_NAME, "mapping": MAPPING.model_dump()},
                headers=AUTH,
            )
            assert refused.status_code == 409 and refused.json() == {"error": "run_in_progress"}
            assert answerer.questions == []
            detector.open_all()

    run_scenario(scenario())


def test_an_index_job_and_a_started_run_refuse_each_other(tmp_path: Path) -> None:  # [NEG]
    async def scenario() -> None:
        embedder = GatedEmbedder()
        r = rig(tmp_path, embedder=embedder)
        detector = ScriptedDetector()
        runs = runs_for(detector, r.slot)
        async with client_for(runs, index_jobs=r.jobs) as client:
            indexing = await client.post("/vault/index", headers=AUTH)
            assert indexing.status_code == 202
            await embedder.entered.wait()
            refused = await start(client)
            assert refused.status_code == 409 and refused.json() == {"error": "run_in_progress"}
            embedder.release.set()
            await r.jobs._active  # wait for the job to end
            run_id = (await start(client)).json()["run_id"]  # the slot is free again
            await detector.entered.wait()
            refused_index = await client.post("/vault/index", headers=AUTH)
            assert refused_index.status_code == 409
            assert refused_index.json() == {"error": "run_in_progress"}
            detector.open_all()
            await until(client, run_id, "complete")
        await r.repo.close()

    run_scenario(scenario())


def test_no_index_is_a_409_at_the_start_and_gives_the_slot_back(tmp_path: Path) -> None:  # [NEG]
    async def scenario() -> None:
        repo = SqliteVecRepository(tmp_path / "missing.sqlite3")
        slot = JobSlot()
        runs = ContradictionRuns(ContradictionDetector(repo), slot, MODEL)
        async with client_for(runs) as client:
            for _ in range(2):  # the second request is not "run_in_progress"
                response = await start(client)
                assert response.status_code == 409
                assert response.json() == {"error": "index_not_built"}
                assert not slot.busy
        await repo.close()

    run_scenario(scenario())


# ----- failure, shutdown, eviction ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "word"),
    [
        (RuntimeError(f"{CANARY} boom"), "internal_error"),
        (IndexNotBuiltError(), "index_not_built"),
        (RunInProgressError(), "detector_error"),  # any other typed domain error
    ],
)
def test_a_failed_run_reports_a_fixed_word_and_frees_the_slot(  # [NEG]
    error: BaseException, word: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    async def scenario() -> None:
        detector, slot = ScriptedDetector(total=1, error=error), JobSlot()
        async with client_for(runs_for(detector, slot)) as client:
            run_id = (await start(client)).json()["run_id"]
            detector.open_all()
            failed = await until(client, run_id, "failed")
            assert failed["failure"] == word and failed["report"] is None
            assert CANARY not in str(failed)
            assert not slot.busy  # a failure frees the slot
            assert (await start(client)).status_code == 202  # so the next start works
            await asyncio.sleep(0)

    run_scenario(scenario())
    assert CANARY not in "\n".join(
        f"{r.getMessage()}|{r.exc_text}|{r.args}" for r in caplog.records
    )


def test_shutdown_cancels_the_run_and_gives_the_slot_back() -> None:  # [NEG]
    async def scenario() -> None:
        detector, slot = ScriptedDetector(), JobSlot()
        runs = runs_for(detector, slot)
        async with client_for(runs) as client:
            run_id = (await start(client)).json()["run_id"]
            await detector.entered.wait()
            assert slot.busy
            await runs.aclose()
            assert detector.cancelled and not slot.busy
            stopped = (await status_of(client, run_id)).json()
            assert stopped["status"] == "cancelled" and stopped["report"] is None
            assert stopped["failure"] is None

    run_scenario(scenario())


def test_shutdown_right_after_the_start_still_gives_the_slot_back() -> None:  # [NEG]
    async def scenario() -> None:
        detector, slot = ScriptedDetector(), JobSlot()
        runs = runs_for(detector, slot)
        run_id = await runs.start(scope=None, cap=5)  # the task has not had a turn yet
        assert slot.busy
        await runs.aclose()
        assert not slot.busy and runs.get(run_id).status == "cancelled"
        assert not detector.entered.is_set()

    run_scenario(scenario())


def test_only_the_last_few_finished_runs_are_kept_and_an_evicted_id_is_404() -> None:  # [NEG]
    async def scenario() -> None:
        detector, slot = ScriptedDetector(total=0), JobSlot()
        async with client_for(runs_for(detector, slot)) as client:
            ids: list[str] = []
            for _ in range(KEPT_RUNS + 2):
                run_id = (await start(client)).json()["run_id"]
                await until(client, run_id, "complete")
                ids.append(run_id)
            assert KEPT_RUNS == 5  # the plan's starting value
            for evicted in ids[:2]:
                response = await status_of(client, evicted)
                assert response.status_code == 404
                assert response.json() == {"error": "run_not_found"}
            for kept in ids[2:]:
                assert (await status_of(client, kept)).status_code == 200

    run_scenario(scenario())


def test_eviction_drops_the_oldest_finished_runs_and_never_one_that_is_still_going() -> None:
    jobs = ContradictionRunJobs(ScriptedDetector(), JobSlot())
    going = ContradictionRun("a" * 32)  # still running, and the oldest of all
    jobs._runs[going.run_id] = going  # white box: only one run can go on at a time
    finished = []
    for n in range(KEPT_RUNS + 3):
        run = ContradictionRun(f"{n:032x}")
        run.finish(EMPTY)
        jobs._runs[run.run_id] = run
        finished.append(run.run_id)
    jobs._evict()
    assert going.run_id in jobs._runs
    assert list(jobs._runs) == [going.run_id, *finished[3:]]


# ----- nothing is stored or logged ----------------------------------------------------------------


def tree(*roots: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                found[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                found[str(path) + "/"] = ""
    return found


def test_a_started_run_writes_no_file_anywhere_it_could_reach(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # [NEG] ADR-019 decision 1: findings are never persisted
    work, ledger = tmp_path / "cwd", tmp_path / "ledger"
    work.mkdir()
    ledger.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setenv("TMPDIR", str(work))
    watched = (work, ledger, CONTRADICTIONS_VAULT)
    before = tree(*watched)

    async def scenario() -> None:
        steps = repeat(reply(True), 50)
        async with served(CONTRADICTIONS_VAULT, tmp_path / "index", steps) as (client, _):
            run_id = (await start(client, cap=3)).json()["run_id"]
            done = await until(client, run_id, "complete")
            assert done["report"]["findings"]  # type: ignore[index]  # there was something to keep

    run_scenario(scenario())
    assert tree(*watched) == before


def test_a_canary_in_a_note_a_quote_and_a_reply_is_in_no_log_record_of_a_started_run(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:  # [NEG] CLAUDE.md 7
    caplog.set_level(logging.DEBUG)

    async def scenario() -> None:
        vault = canary_vault(tmp_path)
        explanation = f"{CANARY}-reply says these differ."
        async with served(vault, tmp_path / "idx", [reply(True, explanation=explanation)]) as (
            client,
            _,
        ):
            run_id = (await start(client)).json()["run_id"]
            done = await until(client, run_id, "complete")
            assert CANARY in str(done)  # the caller may see the findings; the logs are the question
            for refused in (
                await start(client, url=CANARY),  # 422
                await status_of(client, "b" * 32),  # 404
            ):
                assert CANARY not in refused.text

    run_scenario(scenario())
    logged = "\n".join(f"{r.getMessage()}|{r.exc_text}|{r.args}|{r.name}" for r in caplog.records)
    assert logged  # logging was captured, so the absence below means something
    assert CANARY not in logged


# ----- the real runtime ---------------------------------------------------------------------------


def test_the_runtime_stops_started_runs_before_it_closes_the_index(tmp_path: Path) -> None:
    index = tmp_path / "index"
    index.mkdir()
    slot = JobSlot()
    runtime = build_contradiction_runtime(
        Settings(vault_dir=CONTRADICTIONS_VAULT, index_dir=index),
        FakeGate(),  # type: ignore[arg-type]
        slot,
    )
    assert runtime is not None
    assert runtime.closers[0] == runtime.runs.aclose  # first, so no run outlives the index

    async def scenario() -> None:
        assert runtime is not None
        with pytest.raises(IndexNotBuiltError):  # no index file yet, so nothing starts
            await runtime.runs.start(scope=None, cap=3)
        assert not slot.busy
        await runtime.aclose()

    run_scenario(scenario())
