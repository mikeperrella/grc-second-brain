"""Helpers for the retrieval tests: a vault, an index, a model gate and a Retriever, all offline.

Everything runs against the deterministic fake embedder and a fake model listing, so no Ollama is
needed. A test calls `async with stack(...)`; the repositories are closed afterwards.
"""

import json
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from grc_sidecar.errors import EmbeddingUnavailableError
from grc_sidecar.llm.model_gate import ModelGate
from grc_sidecar.packs.loader import PACK_FILE, content_sha256, index_packs
from grc_sidecar.repositories.sqlite_vec_repo import SqliteVecRepository
from grc_sidecar.retrieval.service import Retriever
from grc_sidecar.vault.indexer import Indexer

from .clockkit import ManualClock
from .fakes import FakeEmbeddingClient

EMBEDDING = "nomic-embed-text"
GENERATION = "llama3.1:8b"
BOTH_INSTALLED = (f"{EMBEDDING}:latest", GENERATION)


def write_pack(packs_dir: Path, pack_id: str, files: dict[str, str]) -> Path:
    """A valid synthetic pack: the given Markdown files plus a pack.json that matches them."""
    pack_dir = packs_dir / pack_id
    pack_dir.mkdir(parents=True)
    for name, text in files.items():
        (pack_dir / name).write_text(text, encoding="utf-8", newline="\n")
    metadata = {
        "pack_id": pack_id,
        "title": "Synthetic test pack",
        "version": "1",
        "license": "CC0-1.0 (synthetic test data)",
        "upstream_license": "none (synthetic test data)",
        "source_url": "https://example.invalid/synthetic",
        "retrieved_on": "2026-10-10",
        "source_sha256": "0" * 64,
        "content_sha256": content_sha256({n: t.encode("utf-8") for n, t in files.items()}),
        "origin": "converted_public_domain",
        "content_kind": "full_text",
        "redistributable": True,
        "converter_version": "test",
        "id_list_verified_against": "unverified",
    }
    (pack_dir / PACK_FILE).write_text(json.dumps(metadata), encoding="utf-8", newline="\n")
    return packs_dir


class FakeLister:
    """The model listing the gate asks for. `models=None` means Ollama is unreachable."""

    def __init__(self, models: tuple[str, ...] | None = BOTH_INSTALLED) -> None:
        self.models = models

    async def installed_models(self) -> frozenset[str]:
        if self.models is None:
            raise EmbeddingUnavailableError
        return frozenset(self.models)


@dataclass
class Stack:
    retriever: Retriever
    vault_repo: SqliteVecRepository
    packs_repo: SqliteVecRepository | None
    indexer: Indexer
    embedder: FakeEmbeddingClient
    gate: ModelGate
    clock: ManualClock
    vault_dir: Path
    index_dir: Path


@asynccontextmanager
async def stack(
    vault_dir: Path,
    index_dir: Path,
    *,
    today: date = date(2026, 10, 10),
    packs_dir: Path | None = None,
    models: tuple[str, ...] | None = BOTH_INSTALLED,
    embedder: FakeEmbeddingClient | None = None,
    index_embedder: FakeEmbeddingClient | None = None,
    sync: bool = True,
    reuse_packs_index: Path | None = None,
) -> AsyncIterator[Stack]:
    """Index `vault_dir` (and `packs_dir`) with `index_embedder`, then build a Retriever that
    embeds questions with `embedder`. They differ only in tests about a changed model.

    `reuse_packs_index` is a `packs.sqlite3` built earlier: it is copied in instead of indexing
    the packs again (indexing all of them takes about 20 seconds)."""
    index_dir.mkdir(parents=True, exist_ok=True)
    embedder = embedder or FakeEmbeddingClient()
    index_embedder = index_embedder or embedder
    clock = ManualClock(fixed=today)

    vault_repo = SqliteVecRepository(index_dir / "vault.sqlite3")
    packs_repo: SqliteVecRepository | None = None
    if packs_dir is not None:
        if reuse_packs_index is not None:
            shutil.copyfile(reuse_packs_index, index_dir / "packs.sqlite3")
        packs_repo = SqliteVecRepository(index_dir / "packs.sqlite3")
        if sync and reuse_packs_index is None:
            await index_packs(packs_dir, packs_repo, index_embedder)
            await packs_repo.close()  # the retriever opens it itself, like a fresh process would
            packs_repo = SqliteVecRepository(index_dir / "packs.sqlite3")
    indexer = Indexer(vault_repo, index_embedder, vault_dir)
    if sync:
        await indexer.sync()
        await vault_repo.close()
        vault_repo = SqliteVecRepository(index_dir / "vault.sqlite3")
        indexer = Indexer(vault_repo, index_embedder, vault_dir)

    gate = ModelGate(FakeLister(models), (EMBEDDING, GENERATION), clock)
    retriever = Retriever(
        vault_repo=vault_repo,
        indexer=indexer,
        embedder=embedder,
        gate=gate,
        embedding_model=EMBEDDING,
        clock=clock,
        packs_repo=packs_repo,
    )
    try:
        yield Stack(
            retriever=retriever,
            vault_repo=vault_repo,
            packs_repo=packs_repo,
            indexer=indexer,
            embedder=embedder,
            gate=gate,
            clock=clock,
            vault_dir=vault_dir,
            index_dir=index_dir,
        )
    finally:
        await vault_repo.close()
        if packs_repo is not None:
            await packs_repo.close()
