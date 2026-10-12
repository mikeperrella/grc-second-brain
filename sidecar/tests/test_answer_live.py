"""Live evaluation of the answer service against a real local Ollama (marker `ollama`).

Deselected by default (`-m 'not ollama'` in pyproject). Run by hand, from `sidecar/`, with
`ollama serve` running, `nomic-embed-text` and `llama3.1:8b` installed (nothing here pulls a model).
Each command selects ONE test with `-k`:

    # the labeled evaluation (the dev split unless ALIBI_EVAL_SPLIT says otherwise)
    .venv/Scripts/python -m pytest -m ollama -s -rN tests/test_answer_live.py -k chosen_split

    # the oversize probe: does the server give an error, not a silent cut? (ADR-012, row 4)
    .venv/Scripts/python -m pytest -m ollama -s -rN tests/test_answer_live.py -k oversize

    # a canary in a vault note, searched for in Ollama's own logs
    .venv/Scripts/python -m pytest -m ollama -s -rN tests/test_answer_live.py -k canary

    # (stop Ollama, then) every answer must be blank and say the model is unavailable
    .venv/Scripts/python -m pytest -m ollama -s -rN tests/test_answer_live.py -k stopped

THE HELD-OUT SPLIT is run once, at live check B. It needs both
`ALIBI_EVAL_SPLIT=heldout` and `ALIBI_EVAL_HELDOUT_CONFIRM=run-the-heldout-split-once`, so it cannot
be started by accident, and the command and its full output go in the ADR.

The first run indexes the evaluation vaults and every pack with the real embedding model into a
folder under your temp directory (a few minutes; later runs embed only what changed). The grading
and the report are in `answerlivekit.py`, which `test_answer_eval_harness.py` runs offline.
"""

import asyncio
import os
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from grc_sidecar.errors import GrcError
from grc_sidecar.llm import limits
from grc_sidecar.llm.generation_client import GenerationClient
from grc_sidecar.llm.model_gate import ModelGate
from grc_sidecar.llm.ollama_embeddings import OllamaEmbeddingClient
from grc_sidecar.llm.prompt import PROMPT_TEMPLATE_SHA256
from grc_sidecar.packs.loader import index_packs
from grc_sidecar.questionnaire.service import AnswerService
from grc_sidecar.repositories.sqlite_vec_repo import SqliteVecRepository
from grc_sidecar.retrieval.service import Retriever
from grc_sidecar.security.net import Timeout, create_async_client
from grc_sidecar.vault.clock import SystemClock
from grc_sidecar.vault.indexer import Indexer

from .answerlivekit import ROW_HEADER, Row, acceptance_problems, format_report, format_row, run_eval
from .evalkit import load_eval_set
from .servicekit import merged_vault
from .vaultkit import EVAL_SET, REPO_ROOT

pytestmark = pytest.mark.ollama

BASE_URL = "http://127.0.0.1:11434"
EMBEDDING = "nomic-embed-text"
GENERATION = "llama3.1:8b"
PACKS = REPO_ROOT / "packs"
WORK = Path(tempfile.gettempdir()) / "grc-live-answer"
M4_PACKS_INDEX = Path(tempfile.gettempdir()) / "grc-live-retrieval-index" / "packs.sqlite3"
SPLIT_VAR = "ALIBI_EVAL_SPLIT"
CONFIRM_VAR = "ALIBI_EVAL_HELDOUT_CONFIRM"
CONFIRM_VALUE = "run-the-heldout-split-once"


class EvalClock:
    """The date the labels assume (so the overdue policy is overdue) with a real monotonic timer.

    The date is `clock_today` in the eval file and nowhere else (ADR-013 records it once).
    """

    def __init__(self) -> None:
        self._real = SystemClock()
        self._today = load_eval_set(EVAL_SET).clock_today

    def today(self) -> date:
        return self._today

    def monotonic(self) -> float:
        return self._real.monotonic()


@dataclass
class Parts:
    service: AnswerService
    generator: GenerationClient
    gate: ModelGate
    work: Path


def fresh_vault(work: Path, extra: dict[str, str] | None = None) -> Path:
    """The merged evaluation vault in `work/vault`, with exactly the intended files."""
    vault = work / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    for stale in vault.glob("*.md"):
        stale.unlink()
    return merged_vault(vault, extra=extra)


@asynccontextmanager
async def live_service(
    *,
    work: Path = WORK,
    build: bool,
    with_packs: bool = True,
    extra: dict[str, str] | None = None,
    base_url: str = BASE_URL,
) -> AsyncIterator[Parts]:
    work.mkdir(parents=True, exist_ok=True)
    index_dir = work / "index"
    index_dir.mkdir(parents=True, exist_ok=True)
    vault_dir = fresh_vault(work, extra)
    if with_packs and not (index_dir / "packs.sqlite3").exists() and M4_PACKS_INDEX.exists():
        shutil.copyfile(M4_PACKS_INDEX, index_dir / "packs.sqlite3")  # the same packs, embedded
    embed = OllamaEmbeddingClient(base_url, EMBEDDING, expected_dimension=768)
    vault_repo = SqliteVecRepository(index_dir / "vault.sqlite3")
    packs_repo = SqliteVecRepository(index_dir / "packs.sqlite3") if with_packs else None
    clock = EvalClock()
    gate = ModelGate(embed, (EMBEDDING, GENERATION), clock)
    generator = GenerationClient(base_url, GENERATION, gate)
    try:
        if build:
            report = await Indexer(vault_repo, embed, vault_dir).sync()
            print(
                f"\nindexed vault: {report.chunks_embedded} chunks embedded, "
                f"complete={report.complete}"
            )
            assert report.complete
            if packs_repo is not None:
                packs = await index_packs(PACKS, packs_repo, embed)
                print(
                    f"indexed packs: {packs.chunks_embedded} chunks embedded, "
                    f"complete={packs.complete}"
                )
                assert packs.complete
                await packs_repo.close()  # reopen like a fresh process: the retriever opens it
                packs_repo = SqliteVecRepository(index_dir / "packs.sqlite3")
            await vault_repo.close()
            vault_repo = SqliteVecRepository(index_dir / "vault.sqlite3")
        status = await gate.refresh()
        print(
            f"model gate: state={status.state} ollama={status.ollama} "
            f"missing={status.models_missing}"
        )
        retriever = Retriever(
            vault_repo=vault_repo,
            indexer=Indexer(vault_repo, embed, vault_dir),
            embedder=embed,
            gate=gate,
            embedding_model=EMBEDDING,
            clock=clock,
            packs_repo=packs_repo,
        )
        service = AnswerService(retriever=retriever, generator=generator, clock=clock)
        yield Parts(service, generator, gate, work)
    finally:
        await vault_repo.close()
        if packs_repo is not None:
            await packs_repo.close()
        await generator.aclose()
        await embed.aclose()


def chosen_split() -> str:
    split = os.environ.get(SPLIT_VAR, "dev")
    if split not in ("dev", "heldout"):
        pytest.fail(f"{SPLIT_VAR} must be dev or heldout, not {split!r}")
    if split == "heldout" and os.environ.get(CONFIRM_VAR) != CONFIRM_VALUE:
        pytest.fail(
            "The held-out split is run once, at live check B. To run it, set "
            f"{CONFIRM_VAR}={CONFIRM_VALUE} (and say so in the ADR). Not run."
        )
    return split


def show(row: Row) -> None:
    print(format_row(row), flush=True)


def test_the_chosen_split_with_the_real_models() -> None:
    split = chosen_split()
    evalset = load_eval_set(EVAL_SET)
    items = [i for i in evalset.items if i.split == split]

    async def scenario() -> list[Row]:
        async with live_service(build=True) as parts:
            entry = await parts.generator.read_model_entry()
            header = (
                f"=== EVALUATION split={split} items={len(items)} model={GENERATION} "
                f"digest={entry.digest} template_sha256={PROMPT_TEMPLATE_SHA256[:16]} "
                f"num_ctx={limits.NUM_CTX} num_predict={limits.NUM_PREDICT} seed={limits.SEED}"
            )
            print(header)
            print(ROW_HEADER, flush=True)
            rows = await run_eval(items, parts.service, on_row=show)
            print("\n" + format_report(rows, header=header))
            return rows

    rows = asyncio.run(scenario())
    assert acceptance_problems(rows) == []  # STOP AND REPORT if this fails (a blank item answered)


def test_an_oversize_prompt_is_an_error_not_a_silent_cut() -> None:
    """ADR-012, decision 3: `shift: false` should turn an over-long prompt into an error. This
    sends one directly, past the client's own size check, to see what the real server does."""

    async def probe() -> tuple[int, str]:
        client = create_async_client(
            BASE_URL, timeout=Timeout(connect=5.0, read=600.0, write=60.0, pool=5.0)
        )
        try:
            body = {
                "model": GENERATION,
                "messages": [
                    {"role": "system", "content": "Reply with the single word OK."},
                    {"role": "user", "content": "word " * 30_000},  # about 30,000 tokens
                ],
                "stream": False,
                "shift": False,
                "options": {
                    "num_ctx": limits.NUM_CTX,
                    "num_predict": 8,
                    "temperature": 0,
                    "seed": limits.SEED,
                },
            }
            response = await client.post("/api/chat", json=body)
            return response.status_code, response.text
        finally:
            await client.aclose()

    status, text = asyncio.run(probe())
    print(f"\nstatus: {status}\nbody (first 400 characters): {text[:400]}")
    if status == 400 and "context" in text.lower():
        print("RESULT: the over-long prompt was REFUSED with an error (what ADR-012 expects)")
    else:
        pytest.fail(
            "RESULT: the over-long prompt was NOT refused with a context error. If the status is "
            "200, the server cut the prompt silently: ADR-012 decision 3 must be re-opened."
        )


def test_a_canary_in_a_vault_note_is_not_in_ollamas_logs() -> None:
    canary = f"ALIBI-CANARY-{uuid.uuid4().hex[:12]}"
    note = (
        "---\ndoc_id: CAN-LIVE\ntype: policy\nowner: Test Owner\nreview_by: 2028-01-01\n---\n"
        f"# Badge safe\n\n{canary}: the emergency badge safe is on the third floor.\n"
    )
    work = Path(tempfile.gettempdir()) / "grc-live-answer-canary"

    async def scenario() -> None:
        async with live_service(
            work=work, build=True, with_packs=False, extra={"canary-live.md": note}
        ) as parts:
            outcome = await parts.service.answer("Where is the emergency badge safe?")
            print(f"\nstatus={outcome.result.status} reasons={list(outcome.result.reasons)}")

    asyncio.run(scenario())
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    candidates = [local / "Ollama" / "server.log", local / "Ollama" / "app.log"]
    for folder in (Path(tempfile.gettempdir()), Path(os.environ.get("SYSTEMROOT", "")) / "Temp"):
        if folder.is_dir():
            candidates.extend(folder.glob("ollama-request-logs-*/*"))
    found: list[str] = []
    for path in candidates:
        if path.is_file():
            hits = path.read_bytes().count(canary.encode("utf-8"))
            print(f"searched {path}: {hits} occurrence(s) of {canary}")
            if hits:
                found.append(str(path))
        else:
            print(f"not present: {path}")
    print(f"canary used: {canary}")
    assert found == [], f"the canary reached Ollama's own logs: {found}"


def test_with_ollama_stopped_every_answer_is_blank_and_says_the_model_is_unavailable() -> None:
    if not (WORK / "index" / "vault.sqlite3").exists():
        pytest.fail("No live index yet. Run the chosen_split test first (with Ollama running).")
    probe = OllamaEmbeddingClient(BASE_URL, EMBEDDING, expected_dimension=768)

    async def ollama_is_running() -> bool:
        try:
            await probe.installed_models()
        except GrcError:  # unreachable: what this test needs
            return False
        finally:
            await probe.aclose()
        return True

    if asyncio.run(ollama_is_running()):
        pytest.fail("Ollama is running. Stop it first, then run this test again.")
    items = [i for i in load_eval_set(EVAL_SET).items if i.split == "dev"][:12]

    async def scenario() -> list[Row]:
        async with live_service(build=False) as parts:
            print(ROW_HEADER, flush=True)
            return await run_eval(items, parts.service, on_row=show)

    rows = asyncio.run(scenario())
    asked = [row for row in rows if row.outcome.sources > 0]
    print(f"\n{len(asked)} of {len(rows)} questions had a context and so reached the model gate")
    for row in rows:
        result = row.outcome.result
        assert result.status == "NEEDS_REVIEW" and result.answer is None, row.item.id
    for row in asked:
        assert row.outcome.model_unavailable is True, row.item.id
        assert "model_unavailable" in row.outcome.result.reasons, row.item.id
