"""Live retrieval check against a real local Ollama (marker `ollama`; deselected by default).

Run by hand, from sidecar/ (see ADR-010 for what success looks like). Pass the test file and
`-k` so only one runs:

    .venv/Scripts/python -m pytest -m ollama -s -rN tests/test_retrieval_live.py -k real_models
    (stop Ollama, then)
    .venv/Scripts/python -m pytest -m ollama -s -rN tests/test_retrieval_live.py -k is_stopped

Needs `ollama serve` running with `nomic-embed-text` and `llama3.1:8b` installed. Nothing here pulls
a model. The first test indexes vault-sample/ and every pack in packs/ with the real embedding
model into a folder under your temp directory (a few minutes the first time; a re-run only embeds
what changed) and the second test reuses that index, which is why the folder is not removed.

Assertions are structural plus the shared expectations in `retrieval_expectations.py` (the same
table the offline golden tests use). Which rank a document gets under the real model is what the
printed table is for; it is not asserted.
"""

import asyncio
import tempfile
from pathlib import Path

import pytest

from grc_sidecar.errors import GrcError
from grc_sidecar.llm.model_gate import ModelGate
from grc_sidecar.llm.ollama_embeddings import OllamaEmbeddingClient
from grc_sidecar.packs.loader import index_packs
from grc_sidecar.repositories.sqlite_vec_repo import SqliteVecRepository
from grc_sidecar.retrieval.models import RetrievalResult
from grc_sidecar.retrieval.service import Retriever
from grc_sidecar.vault.clock import SystemClock
from grc_sidecar.vault.indexer import Indexer

from .livekit import LIVE_QUERIES, format_result, problems_in
from .vaultkit import REPO_ROOT, SAMPLE_VAULT

pytestmark = pytest.mark.ollama

BASE_URL = "http://127.0.0.1:11434"
EMBEDDING = "nomic-embed-text"
GENERATION = "llama3.1:8b"
PACKS = REPO_ROOT / "packs"
INDEX_DIR = Path(tempfile.gettempdir()) / "grc-live-retrieval-index"


async def ask_all(*, build: bool) -> dict[str, RetrievalResult]:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    client = OllamaEmbeddingClient(BASE_URL, EMBEDDING, expected_dimension=768)
    vault_repo = SqliteVecRepository(INDEX_DIR / "vault.sqlite3")
    packs_repo = SqliteVecRepository(INDEX_DIR / "packs.sqlite3")
    try:
        indexer = Indexer(vault_repo, client, SAMPLE_VAULT)
        if build:
            vault_report = await indexer.sync()
            packs_report = await index_packs(PACKS, packs_repo, client)
            print(
                f"\nindexed vault: {vault_report.chunks_embedded} chunks embedded, "
                f"complete={vault_report.complete}; packs: {packs_report.chunks_embedded} chunks "
                f"embedded, complete={packs_report.complete}"
            )
            assert vault_report.complete and packs_report.complete
            await vault_repo.close()  # reopen like a fresh process: the retriever opens it itself
            await packs_repo.close()
            vault_repo = SqliteVecRepository(INDEX_DIR / "vault.sqlite3")
            packs_repo = SqliteVecRepository(INDEX_DIR / "packs.sqlite3")
            indexer = Indexer(vault_repo, client, SAMPLE_VAULT)
        clock = SystemClock()
        gate = ModelGate(client, (EMBEDDING, GENERATION), clock)
        status = await gate.refresh()
        print(
            f"\nmodel gate: state={status.state} ollama={status.ollama} "
            f"missing={status.models_missing}"
        )
        retriever = Retriever(
            vault_repo=vault_repo,
            indexer=indexer,
            embedder=client,
            gate=gate,
            embedding_model=EMBEDDING,
            clock=clock,
            packs_repo=packs_repo,
        )
        results: dict[str, RetrievalResult] = {}
        for question in LIVE_QUERIES:
            results[question] = await retriever.retrieve(question)
            print(format_result(question, results[question]))
        return results
    finally:
        await vault_repo.close()
        await packs_repo.close()
        await client.aclose()


def test_ranked_results_with_real_models() -> None:
    results = asyncio.run(ask_all(build=True))
    problems = problems_in(results, expect_mode="hybrid")
    print("\nproblems:", problems or "none")
    assert problems == []


def test_lexical_only_when_ollama_is_stopped() -> None:
    if not (INDEX_DIR / "vault.sqlite3").exists() or not (INDEX_DIR / "packs.sqlite3").exists():
        pytest.fail(
            "No live index yet. Run test_ranked_results_with_real_models first (with Ollama "
            "running); it builds the index this test reuses."
        )
    probe = OllamaEmbeddingClient(BASE_URL, EMBEDDING, expected_dimension=768)

    async def ollama_is_running() -> bool:
        try:
            await probe.installed_models()
        except GrcError:  # unreachable (or not answering sensibly): what this test needs
            return False
        finally:
            await probe.aclose()
        return True

    if asyncio.run(ollama_is_running()):
        pytest.fail("Ollama is running. Stop it first, then run this test again.")
    results = asyncio.run(ask_all(build=False))
    problems = problems_in(results, expect_mode="lexical_only")
    print("\nproblems:", problems or "none")
    assert problems == []
