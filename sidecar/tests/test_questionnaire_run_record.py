"""The run record (ADR-017 decisions 3 and 15): what a finished run hands to the ledger.

Every hash is checked against the SAVED workbook, read back through the loader, not against a value
the writer computed itself. The recorder is a fake unless a test says it is the real ledger.
"""

import asyncio
import dataclasses
import hashlib
import json
import logging
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TypeVar

import httpx
import pytest
from openpyxl.worksheet.worksheet import Worksheet

from grc_sidecar.errors import GenerationUnavailableError
from grc_sidecar.main import create_app
from grc_sidecar.questionnaire.loader import load_questionnaire
from grc_sidecar.questionnaire.models import AnswerResult, Citation
from grc_sidecar.questionnaire.package import sha256_hex as sha256_bytes
from grc_sidecar.questionnaire.runs import DIGEST_UNAVAILABLE, RunState
from grc_sidecar.questionnaire.runtime import LedgerRunRecorder, build_runtime
from grc_sidecar.questionnaire.sanitize import DIRECTION_MARKER
from grc_sidecar.questionnaire.status import blank_result
from grc_sidecar.repositories.base import NewRunRecord, RunAnswer, RunRecord, StoredRunAnswer
from grc_sidecar.repositories.sqlite_ledger_repo import SqliteLedgerRepository
from grc_sidecar.vault.scan import CitedFile, ReadReason, read_cited_file

from .clockkit import ManualClock
from .runskit import (
    CANARY,
    DIGEST,
    FILE_HASH,
    FILE_NAME,
    MAPPING,
    MODEL,
    FakeModelReader,
    Setup,
    citation,
    make_workbook,
    outcome,
    setup,
    supported,
)
from .test_questionnaire_runtime import TOKEN, folders, parts, refuse_every_connection, settings_for
from .xlsxkit import simple_workbook, to_bytes

T = TypeVar("T")

SCENARIO_TIMEOUT_SECONDS = 30
ANSWER_COL, STATUS_COL, EVIDENCE_COL = 3, 5, 7  # the question sheet has two columns of its own
LINES_HASH = "c" * 64


def run(coroutine: Awaitable[T]) -> T:
    return asyncio.run(asyncio.wait_for(coroutine, SCENARIO_TIMEOUT_SECONDS))  # type: ignore[arg-type]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class FakeRecorder:
    """Keeps what it is given, and notes which files existed in the export folder at each call."""

    export_root: Path
    error: Exception | None = None
    records: list[tuple[NewRunRecord, list[RunAnswer]]] = field(default_factory=list)
    files_at_call: list[list[str]] = field(default_factory=list)

    async def record(self, record: NewRunRecord, answers: Sequence[RunAnswer]) -> None:
        self.files_at_call.append(sorted(p.name for p in self.export_root.iterdir()))
        if self.error is not None:
            raise self.error
        self.records.append((record, list(answers)))


def recorded_setup(
    tmp_path: Path, *, count: int = 3, error: Exception | None = None, **kwargs: object
) -> tuple[Setup, FakeRecorder]:
    recorder = FakeRecorder(tmp_path / "exports", error=error)
    return setup(tmp_path, count=count, run_recorder=recorder, **kwargs), recorder  # type: ignore[arg-type]


async def start_and_finish(s: Setup) -> RunState:
    state = await s.runs.start(FILE_NAME, MAPPING)
    _ = [event async for event in s.runs.events(state.run_id)]
    return state


def saved_sheet(s: Setup) -> Worksheet:
    [name] = s.exports()
    return load_questionnaire((s.export_root / name).read_bytes()).workbook["Questions"]


def cell_text(sheet: Worksheet, row: int, column: int) -> str | None:
    value = sheet.cell(row=row, column=column).value
    assert value is None or isinstance(value, str)
    return value


def cited(start: int, end: int, *, quote: str = "Data at rest is encrypted.") -> Citation:
    return dataclasses.replace(citation(), start_line=start, end_line=end, quote=quote)


def answered(
    answer: str, *, status: str = "SUPPORTED", reasons: tuple[str, ...] = (), cite: Citation
) -> AnswerResult:
    return AnswerResult(
        status=status,  # type: ignore[arg-type]
        reasons=reasons,  # type: ignore[arg-type]
        answer=answer,
        model_confidence=0.95,
        citation=cite,
    )


# ----- the cell hashes, against the saved workbook ------------------------------------------


# Everything the writer's cleaning touches: a formula start, a direction override, a control
# character and a Windows line ending. The stored text differs from the model's. The override is
# built with chr() so that no literal direction control sits in this file (a hygiene test checks).
RLO = chr(0x202E)
SANITIZE_HEAVY = "=SUM(1)" + RLO + " hidden\x07 end\r\nsecond line"
DIRECTION_QUOTE = "Data " + RLO + "at rest"


def sanitize_heavy_setup(tmp_path: Path) -> tuple[Setup, FakeRecorder]:
    s, recorder = recorded_setup(tmp_path, count=3)
    s.answerer.script = [
        outcome(answered(SANITIZE_HEAVY, cite=cited(12, 13, quote=DIRECTION_QUOTE))),
        outcome(
            answered("Partly.", status="PARTIAL", reasons=("review_overdue",), cite=cited(5, 6))
        ),
        outcome(blank_result("no_evidence")),
    ]
    return s, recorder


def test_every_recorded_cell_hash_is_the_sha256_of_the_cell_in_the_saved_workbook(
    tmp_path: Path,
) -> None:
    s, recorder = sanitize_heavy_setup(tmp_path)
    run(start_and_finish(s))
    sheet = saved_sheet(s)
    [(_, answers)] = recorder.records
    assert [a.row_index for a in answers] == [2, 3, 4]
    for answer in answers:
        for column, recorded in (
            (ANSWER_COL, answer.answer_cell_sha256),
            (STATUS_COL, answer.status_cell_sha256),
            (EVIDENCE_COL, answer.evidence_cell_sha256),
        ):
            text = cell_text(sheet, answer.row_index, column)
            assert recorded == (None if text is None else sha(text))
    first = answers[0]
    # The sanitize-heavy answer was cleaned before it was stored and before it was hashed.
    stored = cell_text(sheet, 2, ANSWER_COL)
    assert stored is not None and stored != SANITIZE_HEAVY and RLO not in stored
    assert first.answer_cell_sha256 == sha(stored) != sha(SANITIZE_HEAVY)
    evidence = cell_text(sheet, 2, EVIDENCE_COL)
    assert evidence is not None and evidence.startswith(DIRECTION_MARKER)  # marker is in the hash
    assert first.evidence_cell_sha256 == sha(evidence)
    blank = answers[2]
    assert blank.answer_cell_sha256 is None and blank.evidence_cell_sha256 is None
    assert cell_text(sheet, 4, ANSWER_COL) is None and cell_text(sheet, 4, EVIDENCE_COL) is None
    assert blank.status_cell_sha256 == sha("NEEDS_REVIEW")


def test_the_question_hash_is_over_the_normalized_text_of_the_saved_question_cell(
    tmp_path: Path,
) -> None:
    rows = [("Q-1", "  Do you   encrypt\ndata at rest?  "), ("Q-2", "Do you encrypt data at rest?")]
    s, recorder = recorded_setup(tmp_path, data=to_bytes(simple_workbook(rows)), count=2)
    run(start_and_finish(s))
    sheet = saved_sheet(s)
    [(_, answers)] = recorder.records
    for answer in answers:
        question = cell_text(sheet, answer.row_index, 2)
        assert question is not None
        assert answer.question_sha256 == sha(" ".join(question.split()))
    # Two spellings of one question hash alike; the raw text differs.
    assert answers[0].question_sha256 == answers[1].question_sha256
    assert cell_text(sheet, 2, 2) != cell_text(sheet, 3, 2)


def test_an_answer_blanked_by_the_changed_source_check_is_recorded_blank_with_its_final_status(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def second_file_changed(path: str) -> str | None:
        calls.append(path)
        return FILE_HASH if len(calls) == 1 else "d" * 64  # the second answer's file changed

    s, recorder = recorded_setup(tmp_path, count=2, current_sha256=second_file_changed)
    s.answerer.script = [outcome(supported("Yes, AES-256.")), outcome(supported("Yes, again."))]
    run(start_and_finish(s))
    sheet = saved_sheet(s)
    [(_, answers)] = recorder.records
    kept, lowered = answers
    assert kept.status == "SUPPORTED" and kept.answer_cell_sha256 is not None
    assert lowered.status == "NEEDS_REVIEW" and "source_changed" in lowered.reasons
    assert lowered.answer_cell_sha256 is None and cell_text(sheet, 3, ANSWER_COL) is None
    assert lowered.source_path is None and lowered.quote is None  # the final result has no citation
    assert lowered.evidence_cell_sha256 is None
    assert cell_text(sheet, 3, STATUS_COL) == "NEEDS_REVIEW"
    assert lowered.status_cell_sha256 == sha("NEEDS_REVIEW")  # the final status, not SUPPORTED


# ----- the record's own fields ----------------------------------------------------------------


def test_the_record_holds_the_run_facts_and_each_value_in_its_own_field(tmp_path: Path) -> None:
    s, recorder = recorded_setup(tmp_path, reader=FakeModelReader("sha256:before", "sha256:after"))
    state = run(start_and_finish(s))
    [(record, answers)] = recorder.records
    assert state.summary is not None
    assert record == NewRunRecord(
        run_id=state.run_id,
        model=MODEL,
        digest_before="sha256:before",
        digest_after="sha256:after",
        template_sha256="t" * 64,
        input_sha256=sha256_bytes((s.questionnaire_root / FILE_NAME).read_bytes()),
        file_name=state.summary.file_name or "?",
        sheet=MAPPING.sheet,
        header_row=MAPPING.header_row,
        question_col=MAPPING.question_col,
        first_new_column=3,
        question_count=3,
        complete=True,
    )
    assert all(a.run_id == state.run_id for a in answers)


def test_a_digest_that_could_not_be_read_is_recorded_as_a_fixed_word(tmp_path: Path) -> None:
    s, recorder = recorded_setup(
        tmp_path, reader=FakeModelReader(DIGEST, GenerationUnavailableError())
    )
    run(start_and_finish(s))
    [(record, _)] = recorder.records
    assert (record.digest_before, record.digest_after) == (DIGEST, DIGEST_UNAVAILABLE)


# ----- the baseline: one read per cited file, NULL when it cannot be trusted ------------------


def test_a_file_cited_by_several_answers_is_read_once_and_each_row_gets_its_own_lines_hash(
    tmp_path: Path,
) -> None:
    reads: list[tuple[str, list[tuple[int, int]]]] = []

    def read(path: str, ranges: Sequence[tuple[int, int]]) -> CitedFile:
        reads.append((path, list(ranges)))
        return CitedFile(FILE_HASH, tuple(f"{start:02d}".ljust(64, "e") for start, _ in ranges))

    s, recorder = recorded_setup(tmp_path, count=3, read_cited=read)
    s.answerer.script = [outcome(answered("A", cite=cited(n, n + 1))) for n in (10, 20, 30)]
    run(start_and_finish(s))
    assert reads == [("policies/encryption-policy.md", [(10, 11), (20, 21), (30, 31)])]
    [(_, answers)] = recorder.records
    assert [a.cited_lines_sha256 for a in answers] == [f"{n}".ljust(64, "e") for n in (10, 20, 30)]
    assert all(a.cited_file_sha256 == FILE_HASH for a in answers)
    assert [(a.start_line, a.end_line) for a in answers] == [(10, 11), (20, 21), (30, 31)]


@pytest.mark.parametrize("reason", ["not_found", "unsafe_path", "too_large", "not_utf8"])
def test_an_unreadable_cited_file_leaves_the_baseline_null_but_keeps_the_citation(  # [NEG]
    tmp_path: Path, reason: ReadReason
) -> None:
    s, recorder = recorded_setup(tmp_path, count=1, read_cited=lambda _p, _r: reason)
    run(start_and_finish(s))
    [(_, [answer])] = recorder.records
    assert (answer.cited_lines_sha256, answer.cited_file_sha256) == (None, None)
    assert answer.source_path == "policies/encryption-policy.md" and answer.quote is not None
    assert answer.status == "SUPPORTED"


def test_a_cited_file_whose_hash_is_not_the_one_the_answer_carried_has_no_baseline(  # [NEG]
    tmp_path: Path,
) -> None:
    other = CitedFile("f" * 64, (LINES_HASH,))
    s, recorder = recorded_setup(tmp_path, count=1, read_cited=lambda _p, _r: other)
    run(start_and_finish(s))
    [(_, [answer])] = recorder.records
    assert (answer.cited_lines_sha256, answer.cited_file_sha256) == (None, None)


def test_a_range_the_file_does_not_have_gives_no_baseline(tmp_path: Path) -> None:  # [NEG]
    short = CitedFile(FILE_HASH, (None,))
    s, recorder = recorded_setup(tmp_path, count=1, read_cited=lambda _p, _r: short)
    run(start_and_finish(s))
    [(_, [answer])] = recorder.records
    assert (answer.cited_lines_sha256, answer.cited_file_sha256) == (None, None)


def test_a_row_without_a_citation_reads_no_file(tmp_path: Path) -> None:
    reads: list[str] = []

    def read(path: str, _ranges: Sequence[tuple[int, int]]) -> ReadReason:
        reads.append(path)
        return "not_found"

    s, recorder = recorded_setup(tmp_path, count=2, read_cited=read)
    s.answerer.script = [outcome(blank_result("no_evidence")), outcome(blank_result("no_evidence"))]
    run(start_and_finish(s))
    assert reads == []
    [(_, answers)] = recorder.records
    assert all(a.source_path is None and a.cited_file_sha256 is None for a in answers)


# ----- no ledger, a failing ledger, and the order ---------------------------------------------


def test_with_no_ledger_the_run_is_unchanged_but_the_summary_says_no_record_was_kept(
    tmp_path: Path,
) -> None:
    ids = [f"{n:08x}" + "0" * 24 for n in (1, 2)]
    (tmp_path / "plain").mkdir()
    (tmp_path / "rec").mkdir()
    data = make_workbook(2)  # built once: openpyxl stamps the build second into the package
    plain = setup(tmp_path / "plain", count=2, data=data, run_ids=list(ids))
    recorded, _ = recorded_setup(tmp_path / "rec", count=2, data=data, run_ids=list(ids))
    assert plain.deps.run_recorder is None
    without = run(start_and_finish(plain)).summary
    with_record = run(start_and_finish(recorded)).summary
    assert without is not None and with_record is not None
    assert without.warnings == ("run_record_not_kept",)
    assert with_record.warnings == ()
    assert dataclasses.replace(without, warnings=()) == with_record
    assert len(plain.exports()) == 1


def test_a_recorder_that_fails_does_not_fail_the_run_and_its_message_is_not_logged(  # [NEG]
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    s, recorder = recorded_setup(tmp_path, error=RuntimeError(f"disk said {CANARY}"))
    state = run(start_and_finish(s))
    assert state.status == "complete" and state.summary is not None
    assert state.summary.warnings == ("run_record_failed",)
    assert len(s.exports()) == 1  # the answers file was written
    assert "run_record_failed" in caplog.text and "RuntimeError" in caplog.text
    assert CANARY not in caplog.text
    assert recorder.records == []


def test_the_recorder_is_called_once_and_only_after_the_answers_file_exists(tmp_path: Path) -> None:
    s, recorder = recorded_setup(tmp_path)
    state = run(start_and_finish(s))
    assert state.summary is not None and state.summary.file_name is not None
    assert recorder.files_at_call == [[state.summary.file_name]]


def test_a_run_that_fails_before_the_file_is_written_records_nothing(  # [NEG]
    tmp_path: Path,
) -> None:
    s, recorder = recorded_setup(tmp_path)
    original = (s.questionnaire_root / FILE_NAME).read_bytes()

    def change_the_input(_index: int) -> None:
        (s.questionnaire_root / FILE_NAME).write_bytes(original + b"x")

    s.answerer.before_each = change_the_input
    state = run(start_and_finish(s))
    assert state.status == "failed" and state.failure == "write_input_changed"
    assert s.exports() == []
    assert recorder.files_at_call == [] and recorder.records == []


# ----- nothing readable reaches a summary, an event or a log -----------------------------------


def test_a_canary_in_a_question_an_answer_and_a_quote_reaches_no_summary_event_or_log(  # [NEG]
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    rows = [(f"Q-{n}", f"{CANARY} question {n}?") for n in range(1, 3)]
    s, recorder = recorded_setup(tmp_path, count=2, data=to_bytes(simple_workbook(rows)))
    s.answerer.script = [
        outcome(answered(f"answer {CANARY}", cite=cited(1, 2, quote=f"quote {CANARY}")))
    ] * 2

    async def scenario() -> tuple[RunState, list[object]]:
        state = await s.runs.start(FILE_NAME, MAPPING)
        events = [[e.name, e.data] async for e in s.runs.events(state.run_id)]
        return state, events

    state, events = run(scenario())
    assert state.summary is not None
    assert CANARY not in json.dumps(dataclasses.asdict(state.summary), default=str)
    assert CANARY not in json.dumps(events)
    assert CANARY not in caplog.text
    [(record, answers)] = recorder.records  # the recorder got hashes and a quote, by design
    assert CANARY not in repr(record)
    assert all(
        a.answer_cell_sha256 is not None and len(a.answer_cell_sha256) == 64 for a in answers
    )
    assert all(CANARY in (a.quote or "") for a in answers)


# ----- the real ledger, end to end --------------------------------------------------------------


def test_a_run_through_the_real_ledger_and_the_real_vault_read_stores_a_baseline(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    (vault / "policies").mkdir(parents=True)
    text = (
        "---\ndoc_id: ENC-001\n---\n# Encryption\nData at rest is encrypted.\nKeys rotate yearly.\n"
    )
    (vault / "policies" / "encryption-policy.md").write_text(text, encoding="utf-8")
    file_hash = sha(text)
    ledger = SqliteLedgerRepository(tmp_path / "ledger" / "ledger.sqlite3", ManualClock())
    s = setup(
        tmp_path,
        count=2,
        run_recorder=LedgerRunRecorder(ledger),
        read_cited=lambda path, ranges: read_cited_file(vault, path, ranges),
        current_sha256=lambda _path: file_hash,
    )
    real = dataclasses.replace(citation(), start_line=5, end_line=6, file_sha256=file_hash)
    s.answerer.script = [outcome(answered("Yes.", cite=real)), outcome(blank_result("no_evidence"))]

    async def scenario() -> tuple[RunRecord | None, list[StoredRunAnswer]]:
        state = await start_and_finish(s)
        stored = await ledger.get_run_record(state.run_id)
        answers = await ledger.list_run_answers(state.run_id)
        await ledger.close()
        return stored, answers

    stored, answers = run(scenario())
    assert stored is not None and stored.created_on == date(2026, 10, 10)
    first, second = answers
    assert first.cited_lines_sha256 == sha("Data at rest is encrypted.\nKeys rotate yearly.")
    assert first.cited_file_sha256 == file_hash
    assert second.status == "NEEDS_REVIEW" and second.cited_lines_sha256 is None


# ----- wiring ----------------------------------------------------------------------------------


def test_the_runtime_has_a_ledger_recorder_only_when_a_ledger_folder_is_configured(
    tmp_path: Path, clean_grc_env: None
) -> None:
    made = folders(tmp_path)
    transport = httpx.MockTransport(refuse_every_connection)
    plain = settings_for(tmp_path, made)
    gate, embedder = parts(plain)
    runtime = build_runtime(plain, gate, embedder, generator_transport=transport)
    assert runtime is not None and runtime.ledger is None
    assert runtime.runs._deps.run_recorder is None
    asyncio.run(runtime.aclose())

    with_ledger = settings_for(tmp_path, made, ledger_dir=tmp_path / "ledger")
    runtime = build_runtime(with_ledger, gate, embedder, generator_transport=transport)
    assert runtime is not None and runtime.ledger is not None
    assert isinstance(runtime.runs._deps.run_recorder, LedgerRunRecorder)
    asyncio.run(runtime.aclose())  # closes the ledger too, though it was never opened
    assert not (tmp_path / "ledger").exists()  # nothing is created until the first record


def test_create_app_keeps_a_ledger_given_for_tests() -> None:
    marker = object()
    app = create_app(TOKEN, ledger=marker)  # type: ignore[arg-type]
    assert app._app.state.ledger is marker  # type: ignore[attr-defined]
    assert create_app(TOKEN)._app.state.ledger is None  # type: ignore[attr-defined]
