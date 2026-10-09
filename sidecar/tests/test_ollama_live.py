"""Live checks against a real local Ollama (marker `ollama`; deselected by default).

Run by hand, from sidecar/:  .venv/Scripts/python -m pytest -m ollama -v -rA
Needs `ollama serve` running with `nomic-embed-text` installed. Nothing here pulls a model.
"""

import asyncio
import math
from pathlib import Path

import pytest

from grc_sidecar.errors import EmbeddingResponseError, ModelMissingError
from grc_sidecar.llm.ollama_embeddings import OllamaEmbeddingClient
from grc_sidecar.repositories.sqlite_vec_repo import SqliteVecRepository
from grc_sidecar.vault.chunker import EMBED_DOCUMENT_PREFIX, HARD_MAX_EMBED_CHARS
from grc_sidecar.vault.indexer import Indexer

from .vaultkit import SAMPLE_VAULT

pytestmark = pytest.mark.ollama

BASE_URL = "http://127.0.0.1:11434"
MODEL = "nomic-embed-text"
EXPECTED_DIMENSION = 768


def live_client(model: str = MODEL) -> OllamaEmbeddingClient:
    return OllamaEmbeddingClient(BASE_URL, model, expected_dimension=EXPECTED_DIMENSION)


def test_the_model_is_installed_and_has_768_dimensions() -> None:
    async def scenario() -> None:
        client = live_client()
        try:
            profile = await client.ensure_ready()
        finally:
            await client.aclose()
        print(f"\nmodel={profile.model} digest={profile.model_digest} dim={profile.dimension}")
        assert profile.dimension == EXPECTED_DIMENSION

    asyncio.run(scenario())


def test_the_vector_norm_is_printed() -> None:
    async def scenario() -> float:
        client = live_client()
        try:
            await client.ensure_ready()
            vector = (await client.embed_documents([f"{EMBED_DOCUMENT_PREFIX}Access\nreview"]))[0]
        finally:
            await client.aclose()
        return math.sqrt(sum(value * value for value in vector))

    norm = asyncio.run(scenario())
    print(f"\nvector norm = {norm:.6f} (about 1.0 means Ollama returns normalized vectors)")
    assert norm > 0


def test_the_sample_vault_indexes_with_no_errors_and_mfa_is_found(tmp_path: Path) -> None:
    index = tmp_path / "index" / "live.sqlite3"

    async def scenario() -> list[str]:
        client = live_client()
        repo = SqliteVecRepository(index)
        try:
            report = await Indexer(repo, client, SAMPLE_VAULT).sync()
            print(
                f"\nindexed {len(report.indexed)} documents, {report.chunks_embedded} chunks, "
                f"complete={report.complete}, issues={len(report.issues)}"
            )
            assert report.complete and report.issues == () and report.documents_seen == 6
            assert (await repo.verify_integrity()).ok
            vector = await client.embed_query("multi-factor authentication")
            hits = await repo.search(vector, k=3)
            for hit in hits:
                print(f"  {hit.distance:.4f}  {hit.chunk.source_path}  {hit.chunk.heading_path}")
            return [hit.chunk.source_path for hit in hits]
        finally:
            await repo.close()
            await client.aclose()

    top3 = asyncio.run(scenario())
    assert "access-control-and-mfa-policy.md" in top3


def test_an_unknown_model_fails_before_any_database_write(tmp_path: Path) -> None:
    index = tmp_path / "index" / "bogus.sqlite3"

    async def scenario() -> None:
        client = live_client("not-a-real-model")
        repo = SqliteVecRepository(index)
        try:
            with pytest.raises(ModelMissingError):
                await Indexer(repo, client, SAMPLE_VAULT).sync()
        finally:
            await repo.close()
            await client.aclose()

    asyncio.run(scenario())
    assert not index.exists() and not index.parent.exists()  # nothing was written, nothing pulled


# ----- how long may an embedded string be? (HARD_MAX_EMBED_CHARS) --------------------------


def _dense_identifiers() -> str:
    """Control-ID-dense text: about one token per character is the worst case for a tokenizer."""
    tokens = [
        f"{family}-{number}({enhancement})"
        for number in range(1, 26)
        for enhancement in range(1, 13)
        for family in ("AC", "AU", "IA", "SC", "SI", "CM")
    ]
    return " ".join([*tokens, "CC6.1", "A.5.15", "3.1.1", "03.01.01"] * 3)


def _exact_length(body: str, heading: str) -> str:
    """Prefix + heading + body cut so the whole embedded string is HARD_MAX_EMBED_CHARS long."""
    room = HARD_MAX_EMBED_CHARS - len(EMBED_DOCUMENT_PREFIX) - len(heading) - 1
    text = f"{EMBED_DOCUMENT_PREFIX}{heading}\n{body[:room]}"
    assert len(text) == HARD_MAX_EMBED_CHARS
    return text


def test_measure_the_longest_dense_string_ollama_accepts() -> None:
    """Bisect the length of an identifier-dense string; print the limit and chars per token.

    A measurement, not a pass/fail check: it is how HARD_MAX_EMBED_CHARS was chosen.
    """
    heading = "Access Control Policy > AC-2(1) AC-2(2) IA-5(1) CC6.1"
    source = _dense_identifiers()

    async def accepted(client: OllamaEmbeddingClient, length: int) -> int | None:
        text = f"{EMBED_DOCUMENT_PREFIX}{heading}\n{source[: length - len(heading) - 20]}"
        try:
            return (await client.embed_documents_with_stats([text])).prompt_eval_count
        except EmbeddingResponseError:
            return None

    async def scenario() -> tuple[int, int | None]:
        client = live_client()
        try:
            await client.ensure_ready()
            low, high = 500, 6000  # `low` is known to fit, `high` is known not to
            tokens_at_low = await accepted(client, low)
            assert tokens_at_low is not None
            while high - low > 25:
                middle = (low + high) // 2
                tokens = await accepted(client, middle)
                if tokens is None:
                    high = middle
                else:
                    low, tokens_at_low = middle, tokens
            return low, tokens_at_low
        finally:
            await client.aclose()

    longest, tokens = asyncio.run(scenario())
    assert tokens
    print(
        f"\ndense limit: {longest} chars accepted ({tokens} tokens, "
        f"{longest / tokens:.2f} chars per token); the next 25 chars are refused"
    )


def test_worst_case_chunk_fits_context() -> None:
    heading = " > ".join(["Access Control Policy", "AC-2(1) AC-2(2) AC-2(3) IA-5(1) CC6.1"] * 3)
    dense = _exact_length(_dense_identifiers(), heading)
    prose_source = (
        "Accounts are reviewed every quarter by the system owner and exceptions are logged. "
    )
    prose = _exact_length(prose_source * 200, heading)

    async def scenario() -> dict[str, tuple[bool, int | None]]:
        client = live_client()
        outcome: dict[str, tuple[bool, int | None]] = {}
        try:
            await client.ensure_ready()
            for name, text in (("dense", dense), ("prose", prose)):
                try:
                    result = await client.embed_documents_with_stats([text])
                    outcome[name] = (True, result.prompt_eval_count)
                except EmbeddingResponseError:
                    outcome[name] = (False, None)  # Ollama refused it with truncate=false
        finally:
            await client.aclose()
        return outcome

    outcome = asyncio.run(scenario())
    for name, (fits, tokens) in outcome.items():
        ratio = f"{HARD_MAX_EMBED_CHARS / tokens:.2f}" if tokens else "n/a"
        print(
            f"\n{name}: chars={HARD_MAX_EMBED_CHARS} fits={fits} "
            f"prompt_eval_count={tokens} chars_per_token={ratio}"
        )
    assert outcome["dense"][0], "the dense worst case does not fit: lower HARD_MAX_EMBED_CHARS"
    assert outcome["prose"][0]
