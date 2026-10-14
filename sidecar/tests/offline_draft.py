"""The offline draft tool (ADR-017 decision 16): a draft workbook and run record, no model needed.

Run from `sidecar/`:

    .venv/Scripts/python -m tests.offline_draft draft --work-dir W --questionnaire-dir Q \
        --export-dir E --ledger-dir L
    .venv/Scripts/python -m tests.offline_draft import-check --ledger-dir L --export-dir E \
        --run-id RUN --file NAME --new-customer-name "NAME" [--sent-on YYYY-MM-DD]

`draft` runs the REAL questionnaire engine (loader, answer service, retriever and index, writer,
run recorder and the real ledger) over a copy of `vault-demo/vault` and the demo workbook. Only the
model (scripted), the model gate, the digest reader and the embedder are fakes. Every scripted
answer still goes through the real status checks, so the rows it prints are the statuses the engine
really computed. `import-check` calls the same `LedgerService.import_reviewed` the route calls.

Safety: it builds no `GenerationClient` and no `httpx` client, never touches port 11434, writes only
inside the folders it is given, and never overwrites (exclusive create, a vault copy that must be
new, and the ledger, which only ever appends). It prints codes, counts and row numbers, never a
question, an answer, a quote or a document's text.

What it proves: the Excel round trip and the hashes (an unedited reviewed copy imports with every
row `edited_after_draft` false). What it does not: anything about a real model's answers, or the
HTTP route and its authentication (the route has its own tests).
"""

import argparse
import asyncio
import importlib.util
import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath
from types import ModuleType

from openpyxl.utils import get_column_letter
from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict

from grc_sidecar.config import Settings
from grc_sidecar.errors import (
    GrcError,
    ImportRefusedError,
    LedgerRefusedError,
    LedgerStoreError,
    PathNotAllowedError,
    PathNotFoundError,
    QuestionMappingError,
    WorkbookRefusedError,
)
from grc_sidecar.ledger.service import SKIP_CODES, CustomerChoice, LedgerService
from grc_sidecar.llm.prompt import PROMPT_TEMPLATE_SHA256
from grc_sidecar.questionnaire.runs import QuestionnaireRuns, RunDependencies
from grc_sidecar.questionnaire.runtime import LedgerRunRecorder
from grc_sidecar.questionnaire.service import AnswerService
from grc_sidecar.questionnaire.writer import REVIEW
from grc_sidecar.repositories.sqlite_ledger_repo import SqliteLedgerRepository, ledger_path
from grc_sidecar.vault.clock import SystemClock
from grc_sidecar.vault.scan import current_file_sha256, read_cited_file

from .fakes import WORD, FakeEmbeddingClient
from .retrievalkit import EMBEDDING, GENERATION, stack
from .runskit import FakeGate, FakeModelReader
from .servicekit import NO_EVIDENCE_REPLY, FakeModel, Request, reply_json
from .vaultkit import DEMO_VAULT, REPO_ROOT

MODULE = "tests.offline_draft"

# Words the demo embedder treats as the same word. The stock fake embedder is a plain hash of each
# word, so "suppliers" and "vendor" are unrelated to it and the retriever never finds the vendor
# policy for a question about suppliers. A real embedding model would. This small table stands in
# for that. It says nothing about retrieval quality: the status of every row is still worked out by
# the real engine from whatever the retriever really returned.
SAME_WORD: dict[str, str] = {
    "supplier": "vendor",
    "important": "critical",
    "serious": "critical",
    "flaw": "vulnerability",
    "repair": "fix",
    "disaster": "recovery",
    "frequently": "often",
    "person": "staff",
    "employee": "staff",
}


def _stem(word: str) -> str:
    """A very rough stem: enough to match "reviews", "reviewed" and "review"."""
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    for ending, shortest in (("ing", 6), ("ed", 5), ("s", 4)):
        if word.endswith(ending) and len(word) >= shortest and not word.endswith("ss"):
            return word[: -len(ending)]
    return word


def _canonical(text: str) -> str:
    stems = (_stem(word) for word in WORD.findall(text.lower()))
    return " ".join(SAME_WORD.get(stem, stem) for stem in stems)


class DemoEmbedder(FakeEmbeddingClient):
    """The stock fake embedder over stemmed words and the small `SAME_WORD` table."""

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return await super().embed_documents([_canonical(text) for text in texts])

    async def embed_query(self, text: str) -> list[float]:
        return await super().embed_query(_canonical(text))


# The scripted model's answers: question ref -> (vault note, the sentence it quotes). None means the
# model says "no evidence", as the demo questions marked blank in tools/make_demo_xlsx.py expect.
# Every sentence is checked against the copied vault before anything runs. D-11 quotes the vendor
# policy, whose review date is long past, so its row is PARTIAL by the engine's own rule.
QUOTES: dict[str, tuple[str, str] | None] = {
    "D-01": (
        "access-control-policy.md",
        "Multi-factor authentication (MFA) is required for all staff accounts on every system "
        "that stores customer data, and for all remote access.",
    ),
    "D-02": ("access-control-policy.md", "Text-message codes are not an accepted second factor."),
    "D-03": (
        "access-control-policy.md",
        "The Systems team reviews all staff and administrator access every quarter.",
    ),
    "D-04": ("access-control-policy.md", "Access is removed on the employee's last working day."),
    "D-05": (
        "data-retention-policy.md",
        "Customer data is deleted from production within 45 days after a contract ends.",
    ),
    "D-06": None,
    "D-07": (
        "backup-and-recovery-standard.md",
        "The recovery time objective is 8 hours and the recovery point objective is 24 hours.",
    ),
    "D-08": None,
    "D-09": (
        "vulnerability-management-procedure.md",
        "Critical vulnerabilities are fixed within 14 days.",
    ),
    "D-10": (
        "security-awareness-standard.md",
        "All staff complete security awareness training once a year.",
    ),
    "D-11": (
        "vendor-management-policy.md",
        "Procurement reviews each critical vendor once a year and records the result.",
    ),
    "D-12": (
        "incident-response-plan.md",
        "the Security Lead and Legal decide on notice and tell affected customers within 72 hours "
        "of confirming the incident.",
    ),
    "D-13": None,
    "D-14": None,
    "D-15": None,
}


class _FolderSettings(Settings):
    """The real `Settings` and its folder rules, reading no `GRC_*` variable.

    A stray environment variable must not change which folders the rules are applied to: only the
    folders named on the command line count.
    """

    model_config = SettingsConfigDict(env_prefix="ALIBI_OFFLINE_DRAFT_")


class Refused(Exception):
    """A command was refused. `message` is a fixed phrase, never text from any file."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Folders:
    work: Path
    questionnaire: Path
    export: Path
    ledger: Path

    @property
    def vault_copy(self) -> Path:
        return self.work / "vault"

    @property
    def index(self) -> Path:
        return self.work / "index"


def _absolute(text: str) -> Path:
    return Path(os.path.abspath(text))  # absolute but not resolved: the resolved check is separate


def _key(path: Path) -> PurePath:
    return PurePath(os.path.normcase(os.path.normpath(path.resolve())))


def _check_folders(folders: Folders) -> _FolderSettings:
    """Apply the real overlap rules, then the same rule on resolved paths. Creates nothing."""
    try:
        settings = _FolderSettings(
            log_dir=Path(tempfile.gettempdir()),  # required by Settings, never written to
            vault_dir=folders.vault_copy,
            index_dir=folders.index,
            questionnaire_dir=folders.questionnaire,
            export_dir=folders.export,
            ledger_dir=folders.ledger,
        )
    except ValidationError as error:
        names = sorted({str(item["loc"][0]) for item in error.errors() if item["loc"]})
        raise Refused(f"folders overlap or are not usable ({', '.join(names)})") from None
    named = {
        "work-dir": folders.work,
        "questionnaire-dir": folders.questionnaire,
        "export-dir": folders.export,
        "ledger-dir": folders.ledger,
    }
    items = list(named.items())
    for i, (first_name, first) in enumerate(items):
        for second_name, second in items[i + 1 :]:
            a, b = _key(first), _key(second)
            if a.is_relative_to(b) or b.is_relative_to(a):
                raise Refused(f"folders overlap ({first_name}, {second_name})")
    return settings


def _load_demo_tool() -> ModuleType:
    """`tools/make_demo_xlsx.py` as a module (tools/ is not a package)."""
    path = REPO_ROOT / "tools" / "make_demo_xlsx.py"
    spec = importlib.util.spec_from_file_location("make_demo_xlsx", path)
    if spec is None or spec.loader is None:
        raise Refused("the demo workbook generator cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _check_quotes(vault_copy: Path) -> None:
    """Every scripted sentence must be in the copied note, or the run would prove nothing."""
    for ref, found in QUOTES.items():
        if found is None:
            continue
        note, phrase = found
        text = (vault_copy / note).read_text(encoding="utf-8")
        if _normalized(phrase) not in _normalized(text):
            raise Refused(f"the scripted sentence for {ref} is not in {note}")


def _dispatcher(by_question: dict[str, str | None]) -> Callable[[Request], str]:
    """One scripted step for every attempt: it finds which question was asked from its text, so a
    retry can never shift the answers that follow."""

    def step(request: Request) -> str:
        for question, phrase in by_question.items():
            if question not in request.user:
                continue
            if phrase is None:
                return NO_EVIDENCE_REPLY
            for source_id, (_, text) in request.sources().items():
                if _normalized(phrase) in _normalized(text):
                    return reply_json(source_id, phrase)
            return NO_EVIDENCE_REPLY  # retrieval did not put the sentence in context
        return NO_EVIDENCE_REPLY

    return step


# ----- draft -----------------------------------------------------------------------------------


def _prepare(folders: Folders, demo: ModuleType) -> None:
    """Refuse anything that would overwrite, then create the folders. The vault is copied here."""
    if folders.vault_copy.exists() or folders.index.exists():
        raise Refused("the work folder already holds a vault copy or an index")
    if (folders.questionnaire / demo.FILE_NAME).exists():
        raise Refused("the questionnaire folder already holds the demo workbook")
    for folder in (folders.work, folders.questionnaire, folders.export, folders.ledger):
        folder.mkdir(parents=True, exist_ok=True)
    shutil.copytree(DEMO_VAULT, folders.vault_copy)  # the original is only read
    _check_quotes(folders.vault_copy)
    demo.write_demo(folders.questionnaire)  # exclusive create


async def _run_draft(
    folders: Folders, settings: _FolderSettings, demo: ModuleType, out: list[str]
) -> int:
    today = SystemClock().today()
    refs = [question.ref for question in demo.DEMO_QUESTIONS]
    by_question = {
        question.question: (None if QUOTES[question.ref] is None else QUOTES[question.ref][1])  # type: ignore[index]
        for question in demo.DEMO_QUESTIONS
    }
    async with stack(
        folders.vault_copy, folders.index, today=today, embedder=DemoEmbedder()
    ) as built:
        ledger = SqliteLedgerRepository(ledger_path(settings), SystemClock())
        try:
            vault = folders.vault_copy
            deps = RunDependencies(
                questionnaire_root=folders.questionnaire,
                export_root=folders.export,
                answerer=AnswerService(
                    retriever=built.retriever,
                    generator=FakeModel([_dispatcher(by_question)] * (2 * len(refs))),
                    clock=built.clock,
                ),
                vault=built.retriever,
                model_reader=FakeModelReader(),
                gate=FakeGate(),
                required_models=(EMBEDDING, GENERATION),
                model_name=GENERATION,
                clock=built.clock,
                current_sha256=lambda source_path: current_file_sha256(vault, source_path),
                template_sha256=PROMPT_TEMPLATE_SHA256,
                run_recorder=LedgerRunRecorder(ledger),
                read_cited=lambda source_path, ranges: read_cited_file(vault, source_path, ranges),
            )
            runs = QuestionnaireRuns(deps)
            mapping = (await runs.inspect(demo.FILE_NAME)).suggestion
            if mapping is None:
                raise Refused("the demo workbook could not be mapped")
            state = await runs.start(demo.FILE_NAME, mapping)
            _ = [event async for event in runs.events(state.run_id)]
            if state.summary is None or state.status == "failed":
                raise Refused(f"the run failed ({state.failure or 'no summary'})")
            if state.summary.warnings:
                raise Refused(f"the run record was not kept ({', '.join(state.summary.warnings)})")
            record = await ledger.get_run_record(state.run_id)
            answers = await ledger.list_run_answers(state.run_id)
        finally:
            await ledger.close()
    if record is None or len(answers) != len(refs):
        raise Refused("the run record does not match the demo questions")
    review_letter = get_column_letter(record.first_new_column + REVIEW)
    counts = state.summary.counts
    out.append(f"run_id {state.run_id}")
    out.append(f"draft {record.file_name}")
    out.append(
        f"counts SUPPORTED={counts['SUPPORTED']} PARTIAL={counts['PARTIAL']} "
        f"NEEDS_REVIEW={counts['NEEDS_REVIEW']}"
    )
    out.append(f"review_column {review_letter}")
    for ref, answer in zip(refs, answers, strict=True):
        out.append(
            f"row {answer.row_index} {ref} {answer.status} {','.join(answer.reasons) or '-'}"
        )
    out.extend(_next_steps(folders, state.run_id, record.file_name, review_letter))
    return 0


def _next_steps(folders: Folders, run_id: str, file_name: str, review_letter: str) -> list[str]:
    command = (
        f'.venv/Scripts/python -m {MODULE} import-check --ledger-dir "{folders.ledger}" '
        f'--export-dir "{folders.export}" --run-id {run_id} --file "{file_name}" '
        '--new-customer-name "<a customer name>"'
    )
    return [
        "next steps:",
        f'  1. open "{folders.export / file_name}" in Excel',
        f"  2. type approved in column {review_letter} (Review) of the rows you accept",
        "  3. press Save (not Save As) and close Excel",
        f"  4. from sidecar/: {command}",
    ]


def _draft(args: argparse.Namespace, out: list[str]) -> int:
    folders = Folders(
        _absolute(args.work_dir),
        _absolute(args.questionnaire_dir),
        _absolute(args.export_dir),
        _absolute(args.ledger_dir),
    )
    settings = _check_folders(folders)
    demo = _load_demo_tool()
    _prepare(folders, demo)
    return asyncio.run(_run_draft(folders, settings, demo, out))


# ----- import-check ----------------------------------------------------------------------------


async def _run_import_check(
    args: argparse.Namespace, settings: _FolderSettings, out: list[str]
) -> int:
    clock = SystemClock()
    path = ledger_path(settings)
    if not path.is_file():
        raise Refused("no ledger in the ledger folder")
    repo = SqliteLedgerRepository(path, clock)
    try:
        service = LedgerService(repo, settings.export_dir, clock)
        sent_on = args.sent_on or clock.today().isoformat()
        outcome = await service.import_reviewed(
            args.run_id,
            args.file,
            CustomerChoice(new_customer_name=args.new_customer_name, confirm_new=True),
            sent_on,
        )
        promises = {p.promise_id: p for p in await repo.list_promises(outcome.customer_id)}
    finally:
        await repo.close()
    out.append(f"created {outcome.created} (edited_after_draft {outcome.created_edited})")
    out.append("skipped " + " ".join(f"{code}={outcome.skipped[code]}" for code in SKIP_CODES))
    out.append("review " + " ".join(f"{mark}={n}" for mark, n in outcome.review.items()))
    out.append(f"other_values {len(outcome.other_values)} (not shown)")
    out.append(f"customer_created {'yes' if outcome.customer_created else 'no'}")
    for promise_id in outcome.promise_ids:
        promise = promises[promise_id]
        out.append(
            f"promise row {promise.run_row_index} edited_after_draft {promise.edited_after_draft}"
        )
    return 0


def _import_check(args: argparse.Namespace, out: list[str]) -> int:
    ledger, export = _absolute(args.ledger_dir), _absolute(args.export_dir)
    try:
        settings = _FolderSettings(
            log_dir=Path(tempfile.gettempdir()), ledger_dir=ledger, export_dir=export
        )
    except ValidationError:
        raise Refused("folders overlap or are not usable (ledger-dir, export-dir)") from None
    if _key(ledger).is_relative_to(_key(export)) or _key(export).is_relative_to(_key(ledger)):
        raise Refused("folders overlap (ledger-dir, export-dir)")
    return asyncio.run(_run_import_check(args, settings, out))


def _refusal_of(error: Exception) -> str:
    """A fixed code for a refusal. Never the exception's text (it could hold a path or a value)."""
    if isinstance(error, Refused):
        return error.message
    if isinstance(error, ImportRefusedError):
        counts = " ".join(f"{k}={v}" for k, v in sorted(error.counts.items()))
        return f"{error.code}" + (f" ({counts})" if counts else "")
    if isinstance(error, LedgerRefusedError):
        extra = f" (customer_id {error.customer_id})" if error.customer_id else ""
        return f"{error.code}{extra}"
    if isinstance(error, WorkbookRefusedError):
        return f"workbook_refused ({error.code})"
    if isinstance(error, QuestionMappingError):
        return f"mapping_refused ({error.code})"
    if isinstance(error, PathNotAllowedError):
        return "path_not_allowed"
    if isinstance(error, PathNotFoundError):
        return "file_not_found"
    if isinstance(error, LedgerStoreError):
        return "ledger_unusable"
    return f"error ({type(error).__name__})"


# ----- command line ----------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"python -m {MODULE}", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    draft = commands.add_parser("draft", help="make a draft workbook and its run record")
    for name in ("work-dir", "questionnaire-dir", "export-dir", "ledger-dir"):
        draft.add_argument(f"--{name}", required=True)
    check = commands.add_parser("import-check", help="import a reviewed copy of a draft")
    check.add_argument("--ledger-dir", required=True)
    check.add_argument("--export-dir", required=True)
    check.add_argument("--run-id", required=True)
    check.add_argument("--file", required=True)
    check.add_argument("--new-customer-name", required=True)
    check.add_argument("--sent-on", default=None, help="YYYY-MM-DD; default today")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out: list[str] = []
    try:
        code = _draft(args, out) if args.command == "draft" else _import_check(args, out)
    except (Refused, GrcError, FileExistsError, OSError) as error:
        sys.stdout.write("\n".join(out) + ("\n" if out else ""))
        sys.stderr.write(f"refused: {_refusal_of(error)}\n")
        return 1
    sys.stdout.write("\n".join(out) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
