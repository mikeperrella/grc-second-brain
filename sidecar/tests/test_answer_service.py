"""The answer service (ADR-013): retrieval, prompt, model, verifier and retries working together.

The retriever is the real one, over the merged demo and hostile vaults with the fake embedder; the
model is scripted (`servicekit.FakeModel`), so each test decides exactly what the "model" says and
can inspect exactly what it was shown. Nothing here needs a network or an Ollama.
"""

import asyncio
import json
import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from grc_sidecar.config import Settings
from grc_sidecar.errors import (
    ContextWindowReachedError,
    GenerationRejectedError,
    GenerationResponseError,
    GenerationUnavailableError,
    IndexNotBuiltError,
    ModelMissingError,
    ModelsUnavailableError,
    NonLocalModelError,
    OutputTruncatedError,
    PromptTooLargeError,
    RetrievalUnavailableError,
)
from grc_sidecar.llm.limits import QUESTION_MAX_CHARS
from grc_sidecar.logging_config import HANDLER_NAME, LOG_FILE_NAME, configure_logging
from grc_sidecar.questionnaire.service import AnswerOutcome, AnswerService
from grc_sidecar.vault.scan import scan_vault

from .answerkit import chunk
from .clockkit import ManualClock
from .retrievalkit import GENERATION, write_pack
from .servicekit import (
    NO_EVIDENCE_REPLY,
    FakeModel,
    Request,
    Step,
    StubRetriever,
    answering,
    cite,
    counting_boundary,
    reply_json,
    retrieval_of,
)
from .vaultkit import ADVERSARIAL_M5_CELLS

LOCK_QUESTION = "After how many minutes of inactivity do laptops lock the screen?"
LOCK_PHRASE = "lock their screen after 10 minutes of inactivity"
# Shares words with the policy ("reviews", "critical", "vendor"): the keyword index has no stemming,
# and the fake embedder cannot bridge a paraphrase (a real model can; live check A measures that).
VENDOR_QUESTION = "Are critical vendor reviews done once a year?"
VENDOR_PHRASE = "reviews each critical vendor once a year"


def run(coro: Any) -> Any:
    return asyncio.run(coro)


async def ask(
    tmp_path: Path, steps: list[Step], question: str = LOCK_QUESTION
) -> tuple[AnswerOutcome, FakeModel]:
    async with answering(tmp_path, steps) as (service, model, _):
        return await service.answer(question), model


# ----- the happy path ---------------------------------------------------------------------------


def test_a_supported_answer_end_to_end(tmp_path: Path) -> None:
    outcome, model = run(ask(tmp_path, [cite(LOCK_PHRASE)]))
    result = outcome.result
    assert (result.status, result.reasons) == ("SUPPORTED", ())
    assert result.answer == "An answer drawn from the source."
    cite_ = result.citation
    assert cite_ is not None
    assert (cite_.doc_id, cite_.source_path) == ("MQ-POL-006", "acceptable-use-policy.md")
    assert (cite_.start_line, cite_.end_line) == (14, 14)  # the line the M5b eval labels point at
    hashes = {d.source_path: d.file_sha256 for d in scan_vault(tmp_path / "vault").documents}
    assert cite_.file_sha256 == hashes["acceptable-use-policy.md"]
    assert (outcome.attempts, len(model.requests), outcome.retrieval_mode) == (1, 1, "hybrid")
    assert outcome.sources >= 1 and outcome.model_unavailable is False


def test_the_sources_are_numbered_in_order_and_hold_the_retrieved_text() -> None:
    chunks = [chunk(f"Policy text number {n} is here.", doc_id=f"D{n}") for n in (1, 2, 3)]
    retriever = StubRetriever(retrieval_of(*chunks))
    model = FakeModel([NO_EVIDENCE_REPLY])
    service = AnswerService(retriever=retriever, generator=model, clock=_clock(), boundary=_b())
    run(service.answer("What is policy text number two?"))
    shown = model.requests[0].sources()
    assert list(shown) == ["S1", "S2", "S3"]
    assert [text for _, text in shown.values()] == [c.text.rstrip("\n") for c in chunks]


def _clock() -> ManualClock:
    return ManualClock()


def _b() -> Callable[[], str]:
    return counting_boundary()


# ----- the retry rules ---------------------------------------------------------------------------


def test_a_quote_that_does_not_verify_gets_one_retry_with_the_exact_failure(tmp_path: Path) -> None:
    hostile = "HOSTILE-MODEL-TEXT-THAT-MUST-NOT-COME-BACK"

    def wrong(request: Request) -> str:
        return reply_json(request.source_containing(LOCK_PHRASE), f"{hostile} twenty minutes")

    outcome, model = run(ask(tmp_path, [wrong, cite(LOCK_PHRASE)]))
    assert outcome.result.status == "SUPPORTED" and outcome.attempts == 2
    first, second = model.requests
    assert "BEGIN ERROR" not in first.user
    assert "BEGIN ERROR" in second.user and "word for word in S" in second.user
    assert hostile not in second.user  # the retry never repeats what the model wrote
    assert second.system.replace(second.boundary(), "X") == first.system.replace(
        first.boundary(), "X"
    )
    assert first.boundary() != second.boundary()  # a fresh boundary for every attempt


def test_two_unverifiable_quotes_end_blank_with_the_specific_reason(
    tmp_path: Path,
) -> None:  # [NEG]
    def wrong(request: Request) -> str:
        return reply_json(
            request.source_containing(LOCK_PHRASE), "this sentence is not there at all"
        )

    outcome, model = run(ask(tmp_path, [wrong, wrong]))
    assert outcome.attempts == 2 and len(model.requests) == 2  # never a third call
    result = outcome.result
    assert result.status == "NEEDS_REVIEW" and result.answer is None and result.citation is None
    assert result.reasons == ("quote_not_verified",) and result.model_confidence == 0.95


def test_a_schema_failure_then_a_good_reply_succeeds_and_shows_the_error(tmp_path: Path) -> None:
    outcome, model = run(ask(tmp_path, ['{"answer": "x"}', cite(LOCK_PHRASE)]))
    assert outcome.result.status == "SUPPORTED" and outcome.attempts == 2
    second = model.requests[1].user
    assert "BEGIN ERROR" in second and "validation error" in second
    assert "pydantic.dev" not in second  # the documentation URL is never shown


def test_two_schema_failures_end_blank_as_retry_exhausted(tmp_path: Path) -> None:  # [NEG]
    outcome, model = run(ask(tmp_path, ["not json at all", '{"answer": 1}']))
    assert len(model.requests) == 2
    assert outcome.result.status == "NEEDS_REVIEW" and outcome.result.reasons == (
        "retry_exhausted",
    )
    assert outcome.result.answer is None and outcome.result.model_confidence is None


def test_a_schema_failure_and_a_grounding_failure_share_one_budget(tmp_path: Path) -> None:  # [NEG]
    def wrong(request: Request) -> str:
        return reply_json(
            request.source_containing(LOCK_PHRASE), "this sentence is not there at all"
        )

    outcome, model = run(ask(tmp_path, ["not json", wrong]))
    assert len(model.requests) == 2 and outcome.attempts == 2
    assert outcome.result.reasons == ("quote_not_verified",)  # the last failure decides the reason


def test_a_grounding_failure_then_a_schema_failure_is_retry_exhausted(tmp_path: Path) -> None:
    def wrong(request: Request) -> str:
        return reply_json(
            request.source_containing(LOCK_PHRASE), "this sentence is not there at all"
        )

    outcome, _ = run(ask(tmp_path, [wrong, "not json"]))
    assert outcome.result.reasons == ("retry_exhausted",)


def test_every_request_is_the_template_as_system_and_only_data_as_user(tmp_path: Path) -> None:
    outcome, model = run(ask(tmp_path, ["not json", cite(LOCK_PHRASE)]))
    for request in model.requests:
        boundary = request.boundary()
        assert boundary.startswith("BND-")
        assert request.system.count(boundary) == 2  # the instructions name the boundary
        assert LOCK_QUESTION not in request.system  # no data in the system message
        assert LOCK_QUESTION in request.user
        markers = [line for line in request.user.splitlines() if line.startswith(boundary)]
        assert len(markers) == 2 + 2 * len(request.sources()) + (
            2 if "BEGIN ERROR" in request.user else 0
        )
        assert json.loads(json.dumps(request.schema))["properties"]["source_id"]["enum"][-1] is None
    assert outcome.result.status == "SUPPORTED"


# ----- no evidence --------------------------------------------------------------------------


def test_a_no_evidence_reply_leaves_the_cell_blank(tmp_path: Path) -> None:
    outcome, model = run(
        ask(tmp_path, [NO_EVIDENCE_REPLY], "Which cloud provider hosts production?")
    )
    assert len(model.requests) == 1
    result = outcome.result
    assert (result.status, result.reasons, result.answer) == (
        "NEEDS_REVIEW",
        ("no_evidence",),
        None,
    )


def test_empty_retrieval_means_no_evidence_without_asking_the_model() -> None:
    retriever = StubRetriever(retrieval_of())
    model = FakeModel([])
    service = AnswerService(retriever=retriever, generator=model, clock=_clock())
    outcome = run(service.answer("Any question?"))
    assert model.requests == []  # nothing to cite, so nothing to ask
    assert outcome.result.reasons == ("no_evidence",) and outcome.attempts == 0


def test_empty_retrieval_in_keyword_only_mode_with_a_withheld_file() -> None:
    retriever = StubRetriever(retrieval_of(mode="lexical_only", withheld=("a.md",)))
    service = AnswerService(retriever=retriever, generator=FakeModel([]), clock=_clock())
    outcome = run(service.answer("Any question?"))
    assert outcome.result.reasons == ("evidence_withheld", "no_evidence", "lexical_only")


@pytest.mark.parametrize(
    ("question", "reason"),
    [
        ("", "empty_question"),
        ("   \n\t ", "empty_question"),
        ("q" * (QUESTION_MAX_CHARS + 1), "question_too_long"),
    ],
)
def test_a_bad_question_is_blank_without_retrieval_or_model(question: str, reason: str) -> None:
    retriever = StubRetriever()
    model = FakeModel([])
    outcome = run(
        AnswerService(retriever=retriever, generator=model, clock=_clock()).answer(question)
    )
    assert retriever.calls == [] and model.requests == []  # [NEG] nothing was asked of anyone
    assert outcome.result.status == "NEEDS_REVIEW" and outcome.result.reasons == (reason,)


def test_a_question_exactly_at_the_cap_is_asked() -> None:
    retriever = StubRetriever()
    model = FakeModel([NO_EVIDENCE_REPLY])
    service = AnswerService(retriever=retriever, generator=model, clock=_clock())
    run(service.answer("q" * QUESTION_MAX_CHARS))
    assert len(retriever.calls) == 1 and len(model.requests) == 1


# ----- failures of the model, and of the index -------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        GenerationUnavailableError(),
        GenerationResponseError(),
        ModelsUnavailableError(("llama3.1:8b",)),
        ModelMissingError(),
        NonLocalModelError(),
    ],
)
def test_a_model_that_cannot_be_used_blanks_the_cell_and_tells_the_run_to_stop(
    error: Exception,
) -> None:  # [NEG]
    service = AnswerService(retriever=StubRetriever(), generator=FakeModel([error]), clock=_clock())
    outcome = run(service.answer("Any question?"))
    assert outcome.model_unavailable is True and outcome.attempts == 0
    assert outcome.result.status == "NEEDS_REVIEW" and outcome.result.answer is None
    assert outcome.result.reasons == ("model_unavailable",)


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (PromptTooLargeError(), "prompt_too_large"),
        (ContextWindowReachedError(), "prompt_too_large"),
        (GenerationRejectedError(), "prompt_too_large"),
        (OutputTruncatedError(), "output_truncated"),
    ],
)
def test_a_failure_about_this_question_blanks_only_this_cell(error: Exception, reason: str) -> None:
    service = AnswerService(retriever=StubRetriever(), generator=FakeModel([error]), clock=_clock())
    outcome = run(service.answer("Any question?"))
    assert outcome.model_unavailable is False  # the run keeps going
    assert outcome.result.reasons == (reason,)


def test_the_model_failing_on_the_second_attempt_still_blanks_the_cell() -> None:
    steps: list[Step] = ["not json", GenerationUnavailableError()]
    service = AnswerService(retriever=StubRetriever(), generator=FakeModel(steps), clock=_clock())
    outcome = run(service.answer("Any question?"))
    assert outcome.attempts == 1 and outcome.model_unavailable is True
    assert outcome.result.reasons == ("model_unavailable",)


@pytest.mark.parametrize("error", [IndexNotBuiltError(), RetrievalUnavailableError()])
def test_an_index_error_is_not_answered_it_propagates(error: Exception) -> None:  # [NEG]
    service = AnswerService(
        retriever=StubRetriever(error=error), generator=FakeModel([]), clock=_clock()
    )
    with pytest.raises(type(error)):
        run(service.answer("Any question?"))


def test_the_retrieval_limits_are_kept_on_a_model_failure() -> None:
    retriever = StubRetriever(retrieval_of(chunk(), mode="lexical_only", withheld=("a.md",)))
    service = AnswerService(
        retriever=retriever, generator=FakeModel([OutputTruncatedError()]), clock=_clock()
    )
    outcome = run(service.answer("Any question?"))
    assert outcome.result.reasons == ("output_truncated", "evidence_withheld", "lexical_only")


# ----- what retrieval could not promise --------------------------------------------------------


def test_keyword_only_retrieval_caps_a_verified_answer_at_partial(tmp_path: Path) -> None:
    async def scenario() -> AnswerOutcome:
        async with answering(
            tmp_path,
            [cite(LOCK_PHRASE)],
            models=(GENERATION,),  # the embedding model is missing
        ) as (service, _, _):
            return await service.answer(LOCK_QUESTION)

    outcome = run(scenario())
    assert outcome.retrieval_mode == "lexical_only"
    assert (outcome.result.status, outcome.result.reasons) == ("PARTIAL", ("lexical_only",))
    assert outcome.result.answer is not None


def test_one_unrelated_edit_caps_the_answer_because_withheld_is_vault_wide(tmp_path: Path) -> None:
    async def scenario() -> AnswerOutcome:
        async with answering(tmp_path, [cite(LOCK_PHRASE)]) as (service, _, s):
            note = s.vault_dir / "monitoring-standard.md"  # nothing to do with laptops
            note.write_text(
                note.read_text(encoding="utf-8") + "\nAn extra line.\n", encoding="utf-8"
            )
            return await service.answer(LOCK_QUESTION)

    outcome = run(scenario())
    assert (outcome.result.status, outcome.result.reasons) == ("PARTIAL", ("evidence_withheld",))


def test_a_withheld_cited_file_leaves_nothing_to_cite(tmp_path: Path) -> None:
    async def scenario() -> tuple[AnswerOutcome, FakeModel]:
        async with answering(tmp_path, [NO_EVIDENCE_REPLY]) as (service, model, s):
            note = s.vault_dir / "acceptable-use-policy.md"
            note.write_text(note.read_text(encoding="utf-8") + "\nEdited.\n", encoding="utf-8")
            return await service.answer(LOCK_QUESTION), model

    outcome, model = run(scenario())
    assert not any(LOCK_PHRASE in text for _, text in model.requests[0].sources().values())
    assert outcome.result.reasons == ("evidence_withheld", "no_evidence")  # absence is not asserted


def test_an_overdue_policy_is_answered_but_capped_at_partial(tmp_path: Path) -> None:
    outcome, model = run(ask(tmp_path, [cite(VENDOR_PHRASE)], VENDOR_QUESTION))
    result = outcome.result
    assert (result.status, result.reasons) == ("PARTIAL", ("review_overdue",))
    assert result.answer is not None and result.citation is not None
    assert result.citation.doc_id == "MQ-POL-004"
    request = model.requests[0]
    header, _ = request.sources()[request.source_containing(VENDOR_PHRASE)]
    assert "kind=company_policy review=overdue" in header  # the model is told, too


# ----- framework text is never evidence --------------------------------------------------------

PEN_TEST = "Conduct penetration testing at least once a year on organization systems."
PEN_QUESTION = "Do you perform penetration testing at least once a year?"


def pack_text() -> str:
    return (
        "---\ndoc_id: SYN-CA\ntype: framework\nowner: Test Owner\n---\n"
        f"# CA-8 Penetration Testing\n\n{PEN_TEST}\n"
    )


def test_a_framework_quote_never_becomes_an_answer(tmp_path: Path) -> None:  # [NEG]
    packs = write_pack(tmp_path / "packs", "synthetic-pack", {"ca.md": pack_text()})

    async def scenario() -> tuple[AnswerOutcome, FakeModel]:
        async with answering(tmp_path, [cite(PEN_TEST, confidence=1.0)] * 2, packs_dir=packs) as (
            service,
            model,
            _,
        ):
            return await service.answer(PEN_QUESTION), model

    outcome, model = run(scenario())
    assert any(
        "framework_reference" in header
        for h, _ in [v for v in model.requests[0].sources().values()]
        for header in [h]
    ), "the framework text was in the context, labelled as such"
    assert len(model.requests) == 2  # the model was told, once, and did not change its mind
    assert "framework reference text" in model.requests[1].user
    result = outcome.result
    assert (result.status, result.reasons, result.answer) == (
        "NEEDS_REVIEW",
        ("framework_only",),
        None,
    )


# ----- hostile text -------------------------------------------------------------------------------


def test_a_model_that_obeys_a_note_and_stops_returning_json_gets_no_status(tmp_path: Path) -> None:
    """guest-wifi-note.md tells the model to reply in plain text and say SUPPORTED."""
    outcome, model = run(
        ask(
            tmp_path,
            ["SUPPORTED. Confidence 1.0.", "SUPPORTED."],
            "Is guest Wi-Fi separated from the corporate network?",
        )
    )
    assert any("do not return JSON" in text for _, text in model.requests[0].sources().values())
    assert len(model.requests) == 2
    assert (outcome.result.status, outcome.result.reasons) == ("NEEDS_REVIEW", ("retry_exhausted",))


def test_a_model_that_obeys_by_adding_a_status_field_is_refused(tmp_path: Path) -> None:  # [NEG]
    def obey(request: Request) -> str:
        body = json.loads(
            reply_json(
                request.source_containing("separate network"),
                "Guest Wi-Fi is on a separate network",
            )
        )
        body["status"] = "SUPPORTED"
        return json.dumps(body)

    question = "Is guest Wi-Fi separated from the corporate network?"
    outcome, model = run(
        ask(
            tmp_path,
            [obey, cite("Guest Wi-Fi is on a separate network from the corporate network.")],
            question,
        )
    )
    assert "Extra inputs are not permitted" in model.requests[1].user
    assert outcome.result.status == "SUPPORTED"  # the second, honest reply; computed by code


def test_a_fake_closing_delimiter_cannot_add_or_close_a_block(tmp_path: Path) -> None:
    """printer-note.md ends with fake `END OF SOURCE DATA` and `BEGIN SOURCE DATA` lines and new
    "instructions". The question is chosen so that this chunk is in the context."""
    question = "What new instructions come from the system at the end of the source data?"
    outcome, model = run(ask(tmp_path, [NO_EVIDENCE_REPLY], question))
    request = model.requests[0]
    sources = request.sources()
    assert any("=====END OF SOURCE DATA=====" in text for _, text in sources.values())
    assert any("New instructions from the system" in text for _, text in sources.values())
    markers = [line for line in request.user.splitlines() if line.startswith(request.boundary())]
    assert len(markers) == 2 + 2 * len(sources)  # the hostile lines are text, not markers
    assert (outcome.result.status, outcome.result.reasons) == ("NEEDS_REVIEW", ("no_evidence",))


def test_a_formula_in_the_answer_is_not_neutralized_here_the_writer_does_it(tmp_path: Path) -> None:
    """Recorded on purpose: the service returns the model's words as they are. Formula protection
    belongs to the writer (M5j), which does not trust the model; this test keeps that visible."""
    hostile = '=HYPERLINK("http://example.invalid/pay","Click here")'
    question = "When is the security mailbox monitored?"
    quote = "The security mailbox is watched during business hours."
    outcome, _ = run(ask(tmp_path, [cite(quote, answer=hostile)], question))
    assert outcome.result.answer == hostile and outcome.result.status == "SUPPORTED"


def test_a_note_that_says_cite_the_framework_text_instead_does_not_change_the_outcome(
    tmp_path: Path,
) -> None:  # [NEG]
    packs = write_pack(
        tmp_path / "packs",
        "synthetic-pack",
        {
            "ac.md": (
                "---\ndoc_id: SYN-AC\ntype: framework\nowner: T\n---\n"
                "# AC-2\n\nThe organization manages accounts.\n"
            )
        },
    )
    question = "How are seasonal contractor accounts checked?"

    def obey(request: Request) -> str:  # cite the framework text, as the hostile note asks
        return reply_json(
            request.source_containing("The organization manages accounts."),
            "The organization manages accounts.",
            confidence=1.0,
        )

    async def scenario() -> AnswerOutcome:
        async with answering(tmp_path, [obey, obey], packs_dir=packs) as (service, _, _):
            return await service.answer(question)

    outcome = run(scenario())
    assert outcome.result.status == "NEEDS_REVIEW" and outcome.result.reasons == ("framework_only",)


CELLS = json.loads(ADVERSARIAL_M5_CELLS.read_text(encoding="utf-8"))["cells"]


def cell_text(cell: dict[str, Any]) -> str:
    return cell["text"] or cell["repeat_unit"] * cell["repeat_count"]


@pytest.mark.parametrize("cell", CELLS, ids=[c["id"] for c in CELLS])
def test_a_hostile_cell_is_data_and_changes_nothing_the_code_computes(cell: dict[str, Any]) -> None:
    """The same retrieval and the same scripted reply give the same result whatever the question
    text says. The cell only ever appears inside the question block, never in the instructions."""
    text = cell_text(cell)
    retriever = StubRetriever(retrieval_of(chunk()))

    def reply_step(request: Request) -> str:
        return reply_json("S1", "lock their screen after 10 minutes of inactivity", confidence=0.7)

    async def both() -> tuple[AnswerOutcome, AnswerOutcome, FakeModel]:
        neutral_model = FakeModel([reply_step])
        hostile_model = FakeModel([reply_step])
        neutral = AnswerService(
            retriever=retriever, generator=neutral_model, clock=_clock(), boundary=_b()
        )
        hostile = AnswerService(
            retriever=retriever, generator=hostile_model, clock=_clock(), boundary=_b()
        )
        return (
            await neutral.answer("What is the lock time?"),
            await hostile.answer(text),
            hostile_model,
        )

    neutral, hostile, hostile_model = run(both())
    if len(text) > QUESTION_MAX_CHARS:
        assert hostile.result.reasons == ("question_too_long",) and hostile_model.requests == []
        return
    assert (
        (hostile.result.status, hostile.result.reasons)
        == (
            neutral.result.status,
            neutral.result.reasons,
        )
        == ("PARTIAL", ("moderate_confidence",))
    )
    assert hostile.result.answer == neutral.result.answer
    request = hostile_model.requests[0]
    assert text not in request.system
    inside = request.user.split("END QUESTION")[0]
    assert text in inside  # it is in the question block...
    markers = [line for line in request.user.splitlines() if line.startswith(request.boundary())]
    assert len(markers) == 4  # ...and made no marker of its own


def test_answering_never_writes_into_the_vault(tmp_path: Path) -> None:  # [NEG]
    def snapshot(folder: Path) -> dict[str, bytes]:
        return {p.name: p.read_bytes() for p in sorted(folder.glob("*.md"))}

    async def scenario() -> tuple[dict[str, bytes], dict[str, bytes]]:
        async with answering(tmp_path, [NO_EVIDENCE_REPLY, NO_EVIDENCE_REPLY]) as (service, _, s):
            before = snapshot(s.vault_dir)
            for cell in CELLS[:2]:
                await service.answer(cell_text(cell))
            return before, snapshot(s.vault_dir)

    before, after = run(scenario())
    assert before == after and len(before) == 16  # 12 demo notes and 4 hostile notes


# ----- measurements ------------------------------------------------------------------------------


def test_the_measurements_come_from_the_attempts_and_the_injected_clock(tmp_path: Path) -> None:
    async def scenario() -> AnswerOutcome:
        async with answering(tmp_path, ["not json", cite(LOCK_PHRASE)]) as (service, model, s):
            model.on_call = lambda: s.clock.advance(2.5)  # each model call takes 2.5 s
            return await service.answer(LOCK_QUESTION)

    outcome = run(scenario())
    assert outcome.attempts == 2 and outcome.duration == 5.0
    assert (outcome.prompt_eval_count, outcome.eval_count) == (1600, 60)  # 800 and 30, twice
    assert outcome.done_reasons == ("stop", "stop") and outcome.prompt_chars > 0


def test_a_blank_question_makes_no_measurements() -> None:
    service = AnswerService(retriever=StubRetriever(), generator=FakeModel([]), clock=_clock())
    outcome = run(service.answer(""))
    assert (outcome.attempts, outcome.prompt_eval_count, outcome.prompt_chars) == (0, None, 0)
    assert outcome.retrieval_mode is None and outcome.sources == 0


# ----- nothing is logged: the canary tests (F13-1b, G6) -------------------------------------

CANARY_VAULT = "CANARY-VAULT-7f3a"
CANARY_CELL = "CANARY-CELL-91bd"
CANARY_REPLY = "CANARY-REPLY-c20e"
CANARY_NOTE = (
    "---\ndoc_id: CAN-1\ntype: policy\nowner: Test Owner\nreview_by: 2028-01-01\n---\n"
    f"# Backup keys\n\n{CANARY_VAULT}: backup keys are stored in the vault safe on site.\n"
)


@pytest.fixture
def log_file(tmp_path: Path) -> Iterator[Path]:
    root = logging.getLogger()
    level = root.level
    yield tmp_path / "logs" / LOG_FILE_NAME
    for handler in list(root.handlers):
        if handler.get_name() == HANDLER_NAME:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)


def canary_run(tmp_path: Path, level: str) -> str:
    configure_logging(Settings(log_dir=tmp_path / "logs", log_level=level))  # type: ignore[arg-type]
    quote = f"{CANARY_VAULT}: backup keys are stored in the vault safe on site."
    question = f"Where are the backup keys stored? {CANARY_CELL}"

    def bad_then_good() -> list[Step]:
        return [
            f"{CANARY_REPLY} this is not json",  # the schema error text will quote this
            cite(quote, answer=f"{CANARY_REPLY} in the safe"),
        ]

    async def scenario() -> AnswerOutcome:
        async with answering(tmp_path, bad_then_good(), extra_notes={"canary.md": CANARY_NOTE}) as (
            service,
            model,
            _,
        ):
            outcome = await service.answer(question)
            second = model.requests[1].user
            assert CANARY_CELL in second and CANARY_REPLY in second  # the canaries really travelled
            return outcome

    outcome = run(scenario())
    assert outcome.result.status == "SUPPORTED" and outcome.attempts == 2
    for handler in logging.getLogger().handlers:
        handler.flush()
    return (tmp_path / "logs" / LOG_FILE_NAME).read_text(encoding="utf-8")


def test_no_canary_reaches_the_log_at_info(tmp_path: Path, log_file: Path) -> None:  # [NEG]
    text = canary_run(tmp_path, "INFO")
    assert "answered: status=SUPPORTED reasons=[] attempts=2" in text  # the one line we do write
    for canary in (CANARY_VAULT, CANARY_CELL, CANARY_REPLY):
        assert canary not in text, canary


def test_no_canary_reaches_the_log_even_at_debug(tmp_path: Path, log_file: Path) -> None:  # [NEG]
    text = canary_run(tmp_path, "DEBUG")
    for canary in (CANARY_VAULT, CANARY_CELL, CANARY_REPLY):
        assert canary not in text, canary
    assert "attempt 1 rejected" in text and "attempt 2 accepted" in text  # only counts are logged
