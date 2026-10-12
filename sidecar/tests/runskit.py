"""Fakes and builders for the questionnaire run and route tests (ADR-014).

Nothing here touches Ollama or the network. The answerer is scripted, so a test decides what each
question "answers" and can hold a run still (with an event) to test what happens meanwhile.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from grc_sidecar.errors import GenerationUnavailableError, ModelsUnavailableError
from grc_sidecar.llm.ollama_embeddings import ModelEntry
from grc_sidecar.questionnaire.mapping import QuestionMapping
from grc_sidecar.questionnaire.models import AnswerResult, Citation
from grc_sidecar.questionnaire.runs import QuestionnaireRuns, RunDependencies
from grc_sidecar.questionnaire.service import AnswerOutcome
from grc_sidecar.questionnaire.status import blank_result

from .clockkit import ManualClock
from .xlsxkit import simple_workbook, to_bytes

FILE_NAME = "Vendor Questionnaire.xlsx"
MAPPING = QuestionMapping(sheet="Questions", header_row=1, question_col=2, id_col=1)
FILE_HASH = "a" * 64
MODEL = "llama3.1:8b"
EMBEDDING = "nomic-embed-text"
DIGEST = "sha256:digest-one"
CANARY = "ALIBI-RUN-CANARY-7f3a91"


def citation() -> Citation:
    return Citation(
        doc_id="ENC-001",
        source_path="policies/encryption-policy.md",
        start_line=12,
        end_line=13,
        quote="Data at rest is encrypted with AES-256.",
        file_sha256=FILE_HASH,
    )


def supported(answer: str = "Yes. Data at rest is encrypted.") -> AnswerResult:
    return AnswerResult(
        status="SUPPORTED",
        reasons=(),
        answer=answer,
        model_confidence=0.95,
        citation=citation(),
    )


def outcome(result: AnswerResult, *, unavailable: bool = False) -> AnswerOutcome:
    return AnswerOutcome(
        result=result,
        attempts=1,
        prompt_eval_count=900,
        eval_count=40,
        prompt_chars=3_000,
        done_reasons=("stop",),
        retrieval_mode="hybrid",
        sources=3,
        duration=1.0,
        model_unavailable=unavailable,
    )


def unavailable_outcome() -> AnswerOutcome:
    return outcome(blank_result("model_unavailable"), unavailable=True)


@dataclass
class ScriptedAnswerer:
    """Answers each question from a script, or SUPPORTED when the script is used up.

    `hold` makes every call wait until the event is set, so a test can look at the app while a run
    is in the middle of a question. `before_each(index)` runs before each answer.
    """

    script: list[AnswerOutcome | Exception] = field(default_factory=list)
    hold: asyncio.Event | None = None
    before_each: Callable[[int], None] | None = None
    questions: list[str] = field(default_factory=list)
    entered: asyncio.Event = field(default_factory=asyncio.Event)

    async def answer(self, question: str) -> AnswerOutcome:
        self.questions.append(question)
        self.entered.set()
        if self.before_each is not None:
            self.before_each(len(self.questions))
        if self.hold is not None:
            await self.hold.wait()
        step: AnswerOutcome | Exception = (
            self.script[len(self.questions) - 1]
            if len(self.questions) <= len(self.script)
            else outcome(supported())
        )
        if isinstance(step, Exception):
            raise step
        return step


class FakeVault:
    def __init__(self, withheld: tuple[str, ...] = (), error: Exception | None = None) -> None:
        self.withheld = withheld
        self.error = error
        self.calls = 0

    async def vault_withheld(self) -> tuple[str, ...]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.withheld


class FakeModelReader:
    """Digests for the start and the end of a run; an error makes the model unreadable."""

    def __init__(self, *digests: str | Exception) -> None:
        self.digests = list(digests) or [DIGEST]
        self.calls = 0

    async def read_model_entry(self) -> ModelEntry:
        step = self.digests[min(self.calls, len(self.digests) - 1)]
        self.calls += 1
        if isinstance(step, Exception):
            raise step
        return ModelEntry(names=frozenset({MODEL}), digest=step, remote=False)


class FakeGate:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.required: list[tuple[str, ...]] = []

    async def require(self, *models: str) -> None:
        self.required.append(models)
        if self.error is not None:
            raise self.error


@dataclass
class Setup:
    runs: QuestionnaireRuns
    deps: RunDependencies
    answerer: ScriptedAnswerer
    vault: FakeVault
    reader: FakeModelReader
    gate: FakeGate
    questionnaire_root: Path
    export_root: Path
    questions: int

    def exports(self) -> list[str]:
        return sorted(p.name for p in self.export_root.iterdir())


def make_workbook(count: int, *, prefix: str = "Do you encrypt data at rest") -> bytes:
    rows = [(f"Q-{n}", f"{prefix}, question {n}?") for n in range(1, count + 1)]
    return to_bytes(simple_workbook(rows))


def setup(
    tmp_path: Path,
    *,
    count: int = 3,
    answerer: ScriptedAnswerer | None = None,
    vault: FakeVault | None = None,
    reader: FakeModelReader | None = None,
    gate: FakeGate | None = None,
    data: bytes | None = None,
    current_sha256: Callable[[str], str | None] | None = None,
    run_ids: list[str] | None = None,
) -> Setup:
    questionnaire_root = tmp_path / "questionnaires"
    export_root = tmp_path / "exports"
    questionnaire_root.mkdir(exist_ok=True)
    export_root.mkdir(exist_ok=True)
    (questionnaire_root / FILE_NAME).write_bytes(data if data is not None else make_workbook(count))
    answerer = answerer or ScriptedAnswerer()
    vault = vault or FakeVault()
    reader = reader or FakeModelReader()
    gate = gate or FakeGate()
    # The output name uses the first 8 characters of the run id, so each fake id must differ there
    # (a real id is a random uuid4 hex string).
    ids = iter(run_ids or [f"{n:08x}" + "0" * 24 for n in range(1, 1000)])
    deps = RunDependencies(
        questionnaire_root=questionnaire_root,
        export_root=export_root,
        answerer=answerer,
        vault=vault,
        model_reader=reader,
        gate=gate,
        required_models=(EMBEDDING, MODEL),
        model_name=MODEL,
        clock=ManualClock(),
        current_sha256=current_sha256 or (lambda _path: FILE_HASH),
        template_sha256="t" * 64,
        new_run_id=lambda: next(ids),
    )
    return Setup(
        runs=QuestionnaireRuns(deps),
        deps=deps,
        answerer=answerer,
        vault=vault,
        reader=reader,
        gate=gate,
        questionnaire_root=questionnaire_root,
        export_root=export_root,
        questions=count,
    )


def models_unavailable() -> ModelsUnavailableError:
    return ModelsUnavailableError((MODEL,))


def generation_timeout() -> GenerationUnavailableError:
    """What the client raises for a read timeout (and for any other connection-level failure)."""
    return GenerationUnavailableError()
