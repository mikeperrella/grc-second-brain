"""Assembling the real parts a contradiction run uses, from the settings (ADR-019).

Nothing here talks to Ollama while it is built: the generation client only connects when a run
asks it to. It lives outside `contradictions/` on purpose: that package may import the llm package
only from `verdict.py` (a test and a Semgrep rule keep it so), and building the real client is this
module's job. With the vault folder not configured there is no runtime, and the route refuses with
a typed error instead of guessing a folder.

The detector reads its own connection to the vault index (read-only use) and its own generation
client; the slot is the one shared with questionnaire runs.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from grc_sidecar.config import Settings, resolve_index_dir
from grc_sidecar.contradictions.runs import ContradictionRuns
from grc_sidecar.contradictions.service import ContradictionDetector
from grc_sidecar.contradictions.verdict import ModelAsker
from grc_sidecar.jobs import JobSlot
from grc_sidecar.llm.generation_client import GenerationClient
from grc_sidecar.llm.model_gate import ModelGate
from grc_sidecar.questionnaire.runtime import VAULT_INDEX_FILE
from grc_sidecar.repositories.sqlite_vec_repo import SqliteVecRepository
from grc_sidecar.security.net import AsyncBaseTransport


@dataclass
class ContradictionRuntime:
    """The runs, and what has to be closed when the app stops."""

    runs: ContradictionRuns
    closers: list[Callable[[], Awaitable[None]]] = field(default_factory=list)

    async def aclose(self) -> None:
        for close in self.closers:
            await close()


def build_contradiction_runtime(
    settings: Settings,
    gate: ModelGate,
    slot: JobSlot,
    *,
    generator_transport: AsyncBaseTransport | None = None,
) -> ContradictionRuntime | None:
    """The runtime for these settings, or None when the vault folder is not configured.

    `generator_transport` is for tests: it replaces the network under the generation client."""
    if settings.vault_dir is None:
        return None
    repo = SqliteVecRepository(resolve_index_dir(settings) / VAULT_INDEX_FILE)
    generator = GenerationClient(
        settings.ollama_base_url, settings.generation_model, gate, transport=generator_transport
    )
    asker = ModelAsker(generator, model_name=settings.generation_model, gate=gate)
    runs = ContradictionRuns(
        ContradictionDetector(repo, asker), slot, model_name=settings.generation_model
    )
    return ContradictionRuntime(runs=runs, closers=[repo.close, generator.aclose])
