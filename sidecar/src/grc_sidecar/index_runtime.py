"""Assembling the index job from the settings (ADR-021 decision 6).

Needs only the vault folder: with it unset there is no runtime and the routes refuse with
`vault_root_not_configured`. Like the contradiction runtime it opens its own repository on the same
index file; that is safe because the job holds the shared one-heavy-job slot, so it never runs
beside a questionnaire or contradiction run. Nothing here talks to Ollama while it is built.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from grc_sidecar.config import Settings, resolve_index_dir
from grc_sidecar.jobs import JobSlot
from grc_sidecar.llm.embeddings import EmbeddingClient
from grc_sidecar.llm.model_gate import ModelGate
from grc_sidecar.questionnaire.runtime import VAULT_INDEX_FILE
from grc_sidecar.repositories.sqlite_vec_repo import SqliteVecRepository
from grc_sidecar.vault.index_jobs import IndexJobs
from grc_sidecar.vault.indexer import Indexer


@dataclass
class IndexRuntime:
    """The jobs, and what has to be closed when the app stops."""

    jobs: IndexJobs
    closers: list[Callable[[], Awaitable[None]]] = field(default_factory=list)

    async def aclose(self) -> None:
        await self.jobs.aclose()
        for close in self.closers:
            await close()


def build_index_runtime(
    settings: Settings, gate: ModelGate, embedder: EmbeddingClient, slot: JobSlot
) -> IndexRuntime | None:
    """The runtime for these settings, or None when the vault folder is not configured."""
    vault_dir = settings.vault_dir
    if vault_dir is None:
        return None
    repo = SqliteVecRepository(resolve_index_dir(settings) / VAULT_INDEX_FILE)
    jobs = IndexJobs(Indexer(repo, embedder, vault_dir), slot, gate, settings.embedding_model)
    return IndexRuntime(jobs=jobs, closers=[repo.close])
