"""Assembling the real parts a questionnaire run uses, from the settings (ADR-014).

Nothing here talks to Ollama while it is built: the clients only connect when a run asks them to.
With the vault, questionnaire and export folders not all configured there is no runtime, and the
routes refuse with a typed error instead of guessing a folder.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from grc_sidecar.config import Settings, resolve_index_dir
from grc_sidecar.jobs import JobSlot
from grc_sidecar.llm.generation_client import GenerationClient
from grc_sidecar.llm.model_gate import ModelGate
from grc_sidecar.llm.ollama_embeddings import OllamaEmbeddingClient
from grc_sidecar.llm.prompt import PROMPT_TEMPLATE_SHA256
from grc_sidecar.questionnaire.runs import QuestionnaireRuns, RunDependencies
from grc_sidecar.questionnaire.service import AnswerService
from grc_sidecar.repositories.base import LedgerRepository, NewRunRecord, RunAnswer
from grc_sidecar.repositories.sqlite_ledger_repo import SqliteLedgerRepository, ledger_path
from grc_sidecar.repositories.sqlite_vec_repo import SqliteVecRepository
from grc_sidecar.retrieval.service import Retriever
from grc_sidecar.security.net import AsyncBaseTransport
from grc_sidecar.vault.clock import SystemClock
from grc_sidecar.vault.indexer import Indexer
from grc_sidecar.vault.scan import current_file_sha256, read_cited_file

VAULT_INDEX_FILE = "vault.sqlite3"
PACKS_INDEX_FILE = "packs.sqlite3"


class LedgerRunRecorder:
    """Writes a finished run into the ledger. The ledger file is opened on the first record."""

    def __init__(self, ledger: LedgerRepository) -> None:
        self._ledger = ledger

    async def record(self, record: NewRunRecord, answers: Sequence[RunAnswer]) -> None:
        await self._ledger.open_or_create()  # does nothing once open
        await self._ledger.add_run_record(record, answers)


@dataclass
class QuestionnaireRuntime:
    """The runs, and what has to be closed when the app stops."""

    runs: QuestionnaireRuns
    closers: list[Callable[[], Awaitable[None]]] = field(default_factory=list)
    ledger: LedgerRepository | None = None  # set when GRC_LEDGER_DIR is configured

    async def aclose(self) -> None:
        await self.runs.aclose()
        for close in self.closers:
            await close()


def build_runtime(
    settings: Settings,
    gate: ModelGate,
    embedder: OllamaEmbeddingClient,
    *,
    generator_transport: AsyncBaseTransport | None = None,
    slot: JobSlot | None = None,
) -> QuestionnaireRuntime | None:
    """The runtime for these settings, or None when a required folder is not configured.

    `generator_transport` is for tests: it replaces the network under the generation client, so a
    test never reaches a real Ollama. `slot` is the one-heavy-job slot shared with the contradiction
    detector; without it the runs get a slot of their own.
    """
    vault_dir = settings.vault_dir
    questionnaire_dir = settings.questionnaire_dir
    export_dir = settings.export_dir
    if vault_dir is None or questionnaire_dir is None or export_dir is None:
        return None
    index_dir = resolve_index_dir(settings)
    clock = SystemClock()
    vault_repo = SqliteVecRepository(index_dir / VAULT_INDEX_FILE)
    packs_path = index_dir / PACKS_INDEX_FILE
    packs_repo = SqliteVecRepository(packs_path) if packs_path.exists() else None
    generator = GenerationClient(
        settings.ollama_base_url, settings.generation_model, gate, transport=generator_transport
    )
    retriever = Retriever(
        vault_repo=vault_repo,
        indexer=Indexer(vault_repo, embedder, vault_dir),
        embedder=embedder,
        gate=gate,
        embedding_model=settings.embedding_model,
        clock=clock,
        packs_repo=packs_repo,
    )
    ledger = (
        None
        if settings.ledger_dir is None
        else SqliteLedgerRepository(ledger_path(settings), clock)
    )
    deps = RunDependencies(
        questionnaire_root=questionnaire_dir,
        export_root=export_dir,
        answerer=AnswerService(retriever=retriever, generator=generator, clock=clock),
        vault=retriever,
        model_reader=generator,
        gate=gate,
        required_models=(settings.embedding_model, settings.generation_model),
        model_name=settings.generation_model,
        clock=clock,
        current_sha256=lambda source_path: current_file_sha256(vault_dir, source_path),
        template_sha256=PROMPT_TEMPLATE_SHA256,
        run_recorder=None if ledger is None else LedgerRunRecorder(ledger),
        read_cited=lambda source_path, ranges: read_cited_file(vault_dir, source_path, ranges),
        slot=JobSlot() if slot is None else slot,
    )
    runtime = QuestionnaireRuntime(QuestionnaireRuns(deps), ledger=ledger)
    if ledger is not None:
        runtime.closers.append(ledger.close)
    runtime.closers.append(vault_repo.close)
    if packs_repo is not None:
        runtime.closers.append(packs_repo.close)
    runtime.closers.append(generator.aclose)
    return runtime
