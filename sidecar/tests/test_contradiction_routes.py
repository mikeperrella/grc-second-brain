"""`POST /contradictions/run` (ADR-019, chunk 4b): authenticated, typed refusals, one JSON response,
a fixed `model_status` word, the shared one-job slot in both directions, and no vault or model text
in any log or error body. A scripted fake model only; no test reaches port 11434.
"""

import asyncio
import dataclasses
import hashlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from grc_sidecar.contradictions.models import NUMERIC_ONLY_STATEMENT
from grc_sidecar.contradictions.runs import ContradictionRuns
from grc_sidecar.contradictions.service import ContradictionDetector
from grc_sidecar.contradictions.verdict import PAIR_TEMPLATE_SHA256, ModelAsker
from grc_sidecar.errors import GenerationUnavailableError, ModelMissingError
from grc_sidecar.jobs import JobSlot
from grc_sidecar.main import create_app
from grc_sidecar.questionnaire.runs import QuestionnaireRuns
from grc_sidecar.repositories.sqlite_vec_repo import SqliteVecRepository
from grc_sidecar.security.net import create_async_client

from .contradictionmodelkit import CANARY, MODEL, repeat, reply, run_scenario, verdict_json
from .retrievalkit import stack
from .runskit import FILE_NAME, MAPPING, ScriptedAnswerer, setup
from .servicekit import FakeModel, Step, counting_boundary
from .test_job_slot import HeldDetector
from .vaultkit import CONTRADICTIONS_VAULT

TOKEN = hashlib.sha256(b"grc-test-token").hexdigest()
AUTH = {"X-GRC-Auth": TOKEN}
URL = "/contradictions/run"
HOSTILE = "http://evil.example/exfiltrate"


@asynccontextmanager
async def client_for(
    contradictions: ContradictionRuns | None,
    questionnaire: QuestionnaireRuns | None = None,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(TOKEN, contradictions=contradictions, questionnaire=questionnaire)
    async with create_async_client(
        "http://127.0.0.1:8000", transport=httpx.ASGITransport(app=app)
    ) as client:
        yield client


@asynccontextmanager
async def real_runs(
    vault: Path,
    index_dir: Path,
    steps: list[Step],
    *,
    model_name: str = MODEL,
    slot: JobSlot | None = None,
) -> AsyncIterator[tuple[ContradictionRuns, FakeModel]]:
    """The real detector over a really indexed vault (fake embedder), with a scripted model."""
    async with stack(vault, index_dir) as s:
        model = FakeModel(list(steps))
        asker = ModelAsker(model, model_name=model_name, gate=s.gate, boundary=counting_boundary())
        detector = ContradictionDetector(s.vault_repo, asker)
        yield ContradictionRuns(detector, slot or JobSlot(), model_name), model


@asynccontextmanager
async def served(
    vault: Path, index_dir: Path, steps: list[Step], *, model_name: str = MODEL
) -> AsyncIterator[tuple[httpx.AsyncClient, FakeModel]]:
    """A client for an app whose detector is real and whose model is scripted."""
    served_runs = real_runs(vault, index_dir, steps, model_name=model_name)
    async with served_runs as (runs, model), client_for(runs) as client:
        yield client, model


# ----- the token ----------------------------------------------------------------------------------


@pytest.mark.parametrize("headers", [{}, {"X-GRC-Auth": "0" * 64}, {"X-GRC-Auth": ""}])
def test_the_route_requires_the_token(headers: dict[str, str]) -> None:  # [NEG]
    async def scenario() -> None:
        detector = HeldDetector()
        detector.release.set()
        async with client_for(ContradictionRuns(detector, JobSlot(), MODEL)) as client:
            response = await client.post(URL, json={}, headers=headers)
            assert response.status_code == 401 and response.content == b""  # no detail (3.4)
        assert not detector.entered.is_set()  # nothing ran

    run_scenario(scenario())


def test_without_a_configured_vault_the_route_refuses() -> None:
    async def scenario() -> None:
        async with client_for(None) as client:
            response = await client.post(URL, json={}, headers=AUTH)
            assert response.status_code == 503
            assert response.json() == {"error": "vault_root_not_configured"}

    run_scenario(scenario())


# ----- the request: two optional fields, and nothing a caller could point elsewhere --------------


@pytest.mark.parametrize(
    "body",
    [
        {"url": HOSTILE},
        {"base_url": HOSTILE},
        {"host": HOSTILE},
        {"token": CANARY},
        {"credential": CANARY},
        {"model": HOSTILE},
        {"cap": 5, "ollama": HOSTILE},
        {"cap": 0},
        {"cap": 101},
        {"cap": "five"},
        {"cap": CANARY},
        {"scope": ""},
        {"scope": "../outside"},
        {"scope": "a/../b"},
        {"scope": "/absolute"},
        {"scope": "C:/drive"},
        {"scope": "back\\slash"},
        {"scope": "x" * 300},
    ],
    ids=[
        "url",
        "base_url",
        "host",
        "token",
        "credential",
        "model",
        "unknown_with_cap",
        "cap_zero",
        "cap_over_max",
        "cap_string",
        "cap_canary",
        "scope_empty",
        "scope_dotdot",
        "scope_inner_dotdot",
        "scope_absolute",
        "scope_drive",
        "scope_backslash",
        "scope_long",
    ],
)
def test_a_bad_or_unknown_field_is_refused_without_echoing_its_value(
    body: dict[str, object],
) -> None:  # [NEG]
    async def scenario() -> None:
        detector = HeldDetector()
        detector.release.set()
        async with client_for(ContradictionRuns(detector, JobSlot(), MODEL)) as client:
            response = await client.post(URL, json=body, headers=AUTH)
            assert response.status_code == 422
            payload = response.json()
            assert payload["error"] == "invalid_request" and payload["fields"]
            for secret in (HOSTILE, CANARY, "evil.example", "outside", "C:/drive"):
                assert secret not in response.text
        assert not detector.entered.is_set()

    run_scenario(scenario())


def test_the_request_models_have_no_url_token_credential_or_host_field() -> None:
    from grc_sidecar.api.contradictions import RunRequest

    assert set(RunRequest.model_fields) == {"scope", "cap"}
    assert RunRequest.model_config["extra"] == "forbid"


# ----- a run: one JSON response -------------------------------------------------------------------


def test_a_run_returns_findings_counts_the_status_word_and_the_statement(tmp_path: Path) -> None:
    async def scenario() -> None:
        steps = repeat(reply(True), 50)
        async with served(CONTRADICTIONS_VAULT, tmp_path / "index", steps) as (client, model):
            response = await client.post(URL, json={"cap": 3}, headers=AUTH)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["model_status"] == "available" and body["model"] == MODEL
        assert body["says"] == NUMERIC_ONLY_STATEMENT
        assert body["template_sha256"] == PAIR_TEMPLATE_SHA256
        assert len(body["findings"]) == 3 and body["model_calls"] == 3
        assert body["over_cap"] > 0 and body["dropped"]["over_cap"] == body["over_cap"]
        assert body["no_verdict"] == 0
        assert body["verdict_counts"] == {
            "same_subject_value_mismatch": {"conflict_true": 3, "conflict_false": 0}
        }
        assert len(model.requests) == 3  # only the kept pairs were asked
        for finding in body["findings"]:
            assert finding["status"] in {"VALUE_CONFLICT", "CANDIDATE"}
            assert finding["verdict"]["relation"] == "same_subject_value_mismatch"
        assert "model_confidence" in body["findings"][0]["verdict"]

    run_scenario(scenario())


def test_the_scope_limits_the_run_to_a_path_prefix(tmp_path: Path) -> None:
    async def scenario() -> None:
        steps = repeat(reply(True), 50)
        async with served(CONTRADICTIONS_VAULT, tmp_path / "index", steps) as (client, _):
            response = await client.post(
                URL, json={"scope": "retention-notes-a.md", "cap": 50}, headers=AUTH
            )
        body = response.json()
        assert response.status_code == 200 and body["findings"]
        for finding in body["findings"]:
            assert finding["source_a"].startswith("retention-notes-a.md")
            assert finding["source_b"].startswith("retention-notes-a.md")

    run_scenario(scenario())


# ----- the model is not there, or not allowed: a 200 with the word, never a clean run -------


@pytest.mark.parametrize(
    ("steps", "model_name", "word", "calls"),
    [
        ([GenerationUnavailableError()], MODEL, "unavailable", 1),
        ([ModelMissingError()], MODEL, "model_missing", 1),
        ([reply(True)], "gpt-oss:120b-cloud", "non_local_refused", 0),
    ],
    ids=["unavailable", "model_missing", "non_local_refused_cloud_named"],
)
def test_a_missing_or_refused_model_is_a_200_with_every_finding_a_candidate(
    tmp_path: Path, steps: list[Step], model_name: str, word: str, calls: int
) -> None:  # [NEG] N5
    async def scenario() -> None:
        async with real_runs(
            CONTRADICTIONS_VAULT, tmp_path / "index", steps, model_name=model_name
        ) as (runs, model):
            async with client_for(runs) as client:
                response = await client.post(URL, json={"cap": 5}, headers=AUTH)
            assert len(model.requests) == calls  # a cloud-named model makes no call
        body = response.json()
        assert response.status_code == 200 and body["model_status"] == word
        assert body["findings"] and body["says"] == NUMERIC_ONLY_STATEMENT
        assert all(f["status"] == "CANDIDATE" for f in body["findings"])
        assert all(f["flags"] == ["model_not_consulted"] for f in body["findings"])
        assert body["verdict_counts"] == {} and body["no_verdict"] == len(body["findings"])

    run_scenario(scenario())


def test_partial_when_some_pairs_got_no_verdict(tmp_path: Path) -> None:
    async def scenario() -> None:
        steps: list[Step] = [reply(True), GenerationUnavailableError()]
        async with served(CONTRADICTIONS_VAULT, tmp_path / "index", steps) as (client, _):
            response = await client.post(URL, json={"cap": 4}, headers=AUTH)
        body = response.json()
        assert body["model_status"] == "partial" and body["no_verdict"] == 3
        assert sum(1 for f in body["findings"] if f["verdict"] is not None) == 1

    run_scenario(scenario())


def test_no_index_is_a_409_and_gives_the_slot_back(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = SqliteVecRepository(tmp_path / "missing.sqlite3")
        runs = ContradictionRuns(ContradictionDetector(repo), JobSlot(), MODEL)
        async with client_for(runs) as client:
            for _ in range(2):  # the second request is not "run_in_progress"
                response = await client.post(URL, json={}, headers=AUTH)
                assert response.status_code == 409
                assert response.json() == {"error": "index_not_built"}
        await repo.close()

    run_scenario(scenario())


# ----- the slot, both ways, over HTTP -------------------------------------------------------------


def test_a_second_contradiction_request_gets_409_while_one_runs() -> None:  # [NEG]
    async def scenario() -> None:
        detector = HeldDetector()
        async with client_for(ContradictionRuns(detector, JobSlot(), MODEL)) as client:
            first = asyncio.create_task(client.post(URL, json={}, headers=AUTH))
            await detector.entered.wait()
            second = await client.post(URL, json={}, headers=AUTH)
            assert second.status_code == 409 and second.json() == {"error": "run_in_progress"}
            detector.release.set()
            assert (await first).status_code == 200

    run_scenario(scenario())


def test_a_contradiction_request_gets_409_while_a_questionnaire_run_holds_the_slot(
    tmp_path: Path,
) -> None:  # [NEG]
    async def scenario() -> None:
        slot, hold = JobSlot(), asyncio.Event()
        answerer = ScriptedAnswerer(hold=hold)
        s = setup(tmp_path, answerer=answerer)
        questions = QuestionnaireRuns(dataclasses.replace(s.deps, slot=slot))
        detector = HeldDetector()
        detector.release.set()
        async with client_for(ContradictionRuns(detector, slot, MODEL), questions) as client:
            started = await client.post(
                "/questionnaire/runs",
                json={"file": FILE_NAME, "mapping": MAPPING.model_dump()},
                headers=AUTH,
            )
            assert started.status_code == 202
            await answerer.entered.wait()
            refused = await client.post(URL, json={}, headers=AUTH)
            assert refused.status_code == 409 and refused.json() == {"error": "run_in_progress"}
            assert not detector.entered.is_set()
            hold.set()
            run_id = started.json()["run_id"]
            await client.get(f"/questionnaire/runs/{run_id}/events", headers=AUTH)  # to the end
            assert (await client.post(URL, json={}, headers=AUTH)).status_code == 200

    run_scenario(scenario())


def test_a_questionnaire_start_gets_409_while_a_contradiction_run_holds_the_slot(
    tmp_path: Path,
) -> None:  # [NEG]
    async def scenario() -> None:
        slot, detector = JobSlot(), HeldDetector()
        answerer = ScriptedAnswerer()
        s = setup(tmp_path, answerer=answerer)
        questions = QuestionnaireRuns(dataclasses.replace(s.deps, slot=slot))
        async with client_for(ContradictionRuns(detector, slot, MODEL), questions) as client:
            running = asyncio.create_task(client.post(URL, json={}, headers=AUTH))
            await detector.entered.wait()
            refused = await client.post(
                "/questionnaire/runs",
                json={"file": FILE_NAME, "mapping": MAPPING.model_dump()},
                headers=AUTH,
            )
            assert refused.status_code == 409 and refused.json() == {"error": "run_in_progress"}
            assert answerer.questions == []
            detector.release.set()
            assert (await running).status_code == 200

    run_scenario(scenario())


# ----- no vault or model text in any log or error body --------------------------------------------

NOTE = """---
doc_id: LOG-{n}
type: policy
owner: Synthetic Owner
review_by: 2030-01-01
---
# Synthetic note {n}

> SYNTHETIC FIXTURE: fictional company, written for a test. Not a real policy.

{body}
"""


def canary_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    for n, days in ((1, 90), (2, 365)):
        # The canary sits inside the sentence, so it is in the verified quote the finding shows.
        text = NOTE.format(n=n, body=f"Audit logs are kept for {days} days ({CANARY}-vault-{n}).")
        (vault / f"note{n}.md").write_text(text, encoding="utf-8", newline="\n")
    return vault


def all_log_text(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(f"{r.getMessage()}|{r.exc_text}|{r.args}|{r.name}" for r in caplog.records)


def test_a_canary_in_a_note_a_quote_and_a_reply_is_in_no_log_record(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:  # [NEG] CLAUDE.md 7 (never log document content above DEBUG, and here not at DEBUG)
    caplog.set_level(logging.DEBUG)

    async def scenario() -> None:
        vault = canary_vault(tmp_path)
        explanation = f"{CANARY}-reply says these differ."
        invented = verdict_json(quote_a=f"{CANARY}-invented", quote_b="nothing")
        # Pair 1 gets a valid reply carrying a canary; the same vault again gets an invented quote.
        for steps, directory in (
            ([reply(True, explanation=explanation)], "one"),
            ([invented, invented], "two"),
        ):
            async with served(vault, tmp_path / directory, steps) as (client, _):
                response = await client.post(URL, json={}, headers=AUTH)
                assert response.status_code == 200
                # The caller IS allowed to see the findings; the logs are the question.
                assert CANARY in response.text

    run_scenario(scenario())
    logged = all_log_text(caplog)
    assert logged  # logging was captured, so the absence below means something
    assert CANARY not in logged


def test_no_error_body_holds_text_from_the_vault_or_the_model(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:  # [NEG]
    caplog.set_level(logging.DEBUG)

    async def scenario() -> None:
        vault = canary_vault(tmp_path)
        steps: list[Step] = [reply(True, explanation=f"{CANARY}-x"), GenerationUnavailableError()]
        async with real_runs(vault, tmp_path / "index", steps) as (_runs, _):
            detector = HeldDetector()
            blocked = ContradictionRuns(detector, JobSlot(), MODEL)  # held, so a second gets 409
            async with client_for(blocked) as client:
                first = asyncio.create_task(client.post(URL, json={}, headers=AUTH))
                await detector.entered.wait()
                bodies = [
                    (await client.post(URL, json={}, headers=AUTH)).text,  # 409
                    (await client.post(URL, json={"url": CANARY}, headers=AUTH)).text,  # 422
                    (await client.post(URL, json={})).text,  # 401
                ]
                detector.release.set()
                await first
            async with client_for(None) as client:
                bodies.append((await client.post(URL, json={}, headers=AUTH)).text)  # 503
        for body in bodies:
            assert CANARY not in body

    run_scenario(scenario())
    assert CANARY not in all_log_text(caplog)
