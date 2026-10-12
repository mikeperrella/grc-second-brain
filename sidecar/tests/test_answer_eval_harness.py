"""The grading and the report of the evaluation run, exercised offline (like M4's live helpers).

The live test (`test_answer_live.py`) needs a real Ollama and is run by hand. Everything it does
besides calling the model is in `answerlivekit.py`, and this file runs that code here, so a mistake
in the grading or the report is found in CI and not on the day the models are on.

Part 1 grades hand-made outcomes against labels, each rule on its own. Part 2 runs the real
`run_eval` over real eval items with the real retriever (fake embedder) and a scripted model.
"""

import asyncio
from pathlib import Path

import pytest

from grc_sidecar.questionnaire.models import AnswerResult, Citation
from grc_sidecar.questionnaire.service import AnswerOutcome
from grc_sidecar.questionnaire.status import blank_result

from .answerkit import FILE_SHA
from .answerlivekit import Row, acceptance_problems, format_report, grade, run_eval
from .evalkit import EvalItem, EvalSet, EvidenceRef, load_eval_set
from .servicekit import NO_EVIDENCE_REPLY, Request, Step, answering, cite, reply_json
from .vaultkit import EVAL_SET

EVIDENCE = EvidenceRef(doc_id="MQ-POL-006", start_line=14, end_line=14, phrase="lock their screen")


def item(**changes: object) -> EvalItem:
    base: dict[str, object] = {
        "id": "EV-X",
        "split": "dev",
        "category": "exact_value",
        "question": "q?",
        "expected": "answer",
        "acceptable_statuses": ["SUPPORTED", "PARTIAL"],
        "acceptable_evidence": [EVIDENCE],
    }
    base.update(changes)
    return EvalItem.model_validate(base)


def outcome(result: AnswerResult) -> AnswerOutcome:
    return AnswerOutcome(
        result=result,
        attempts=1,
        prompt_eval_count=900,
        eval_count=40,
        prompt_chars=2_700,
        done_reasons=("stop",),
        retrieval_mode="hybrid",
        sources=3,
        duration=2.0,
        model_unavailable=False,
    )


def answered(
    status: str = "SUPPORTED",
    reasons: tuple[str, ...] = (),
    *,
    doc_id: str = "MQ-POL-006",
    lines: tuple[int, int] = (14, 14),
) -> AnswerOutcome:
    cite_ = Citation(doc_id, "a.md", lines[0], lines[1], "a quote that is long enough", FILE_SHA)
    result = AnswerResult(status, reasons, "An answer.", 0.95, cite_)  # type: ignore[arg-type]
    return outcome(result)


def blank(*reasons: str) -> AnswerOutcome:
    return outcome(blank_result(*reasons))  # type: ignore[arg-type]


# ----- grading -------------------------------------------------------------------------------


def test_a_correct_answer_has_no_problems() -> None:
    assert grade(item(), answered()) == []
    assert grade(item(), answered("PARTIAL", ("lexical_only",))) == []


def test_an_answer_item_that_ended_blank_is_a_problem() -> None:  # [NEG]
    problems = grade(item(), blank("no_evidence"))
    assert problems and "NEEDS_REVIEW" in problems[0]


def test_a_status_the_label_does_not_accept_is_a_problem() -> None:  # [NEG]
    problems = grade(item(acceptable_statuses=["SUPPORTED"]), answered("PARTIAL"))
    assert problems == ["status PARTIAL is not one of ['SUPPORTED']"]


def test_required_reasons_must_all_be_present() -> None:  # [NEG]
    overdue = item(acceptable_statuses=["PARTIAL"], expected_reasons=["review_overdue"])
    assert grade(overdue, answered("PARTIAL", ("review_overdue",))) == []
    assert grade(overdue, answered("PARTIAL", ())) == [
        "missing required reasons ['review_overdue']"
    ]


def test_a_citation_must_lie_inside_the_acceptable_evidence() -> None:  # [NEG]
    assert grade(item(), answered(lines=(14, 14))) == []
    assert "outside" in grade(item(), answered(lines=(15, 15)))[0]  # one line off
    assert "outside" in grade(item(), answered(lines=(13, 14)))[0]  # starts early
    assert "outside" in grade(item(), answered(doc_id="MQ-POL-005"))[0]  # another document


def test_a_wider_acceptable_range_accepts_a_citation_inside_it() -> None:
    wide = EvidenceRef(doc_id="MQ-POL-006", start_line=10, end_line=20, phrase="x")
    assert grade(item(acceptable_evidence=[wide]), answered(lines=(14, 15))) == []


def test_a_blank_labeled_item_must_end_blank() -> None:  # [NEG]
    label = item(expected="blank", acceptable_statuses=["NEEDS_REVIEW"], acceptable_evidence=[])
    assert grade(label, blank("no_evidence")) == []
    assert "was answered" in grade(label, answered())[0]
    assert "was answered" in grade(label, answered("PARTIAL", ("review_overdue",)))[0]


# ----- the report ----------------------------------------------------------------------------


def rows_for(*pairs: tuple[EvalItem, AnswerOutcome]) -> list[Row]:
    return [Row(i, o, tuple(grade(i, o))) for i, o in pairs]


def test_the_report_gives_counts_and_the_acceptance_result() -> None:
    label = item(expected="blank", acceptable_statuses=["NEEDS_REVIEW"], acceptable_evidence=[])
    rows = rows_for(
        (item(id="EV-1"), answered()),
        (item(id="EV-2"), blank("no_evidence")),
        (label.model_copy(update={"id": "EV-3"}), blank("no_evidence")),
        (label.model_copy(update={"id": "EV-4"}), answered()),
    )
    assert acceptance_problems(rows) == ["EV-4"]
    report = format_report(rows, header="HEADER LINE")
    assert report.startswith("HEADER LINE")
    assert "statuses: SUPPORTED=2, PARTIAL=0, NEEDS_REVIEW=2" in report
    assert "label met: 2 of 4" in report
    assert "blank-labeled items that ended blank: 1 of 2" in report
    assert "ACCEPTANCE (every blank-labeled item ends blank): FAIL (answered: EV-4)" in report
    assert "answer-labeled items that ended blank: 1 of 2" in report
    assert "characters per token" in report and "3.00" in report  # 2,700 / 900
    assert "done_reason counts: stop=4" in report
    assert "%" not in report  # counts only, never percentages


def test_the_report_says_pass_when_every_blank_item_ended_blank() -> None:
    label = item(expected="blank", acceptable_statuses=["NEEDS_REVIEW"], acceptable_evidence=[])
    report = format_report(rows_for((label, blank("no_evidence"))))
    assert "ACCEPTANCE (every blank-labeled item ends blank): PASS" in report
    assert "blank-labeled items that ended blank: 1 of 1" in report


# ----- the real run_eval over real items --------------------------------------------------------

# Questions that share words with their policy, so the keyword side finds them offline (the fake
# embedder cannot bridge a paraphrase).
OFFLINE_IDS = ("EV-008", "EV-009", "EV-010", "EV-011", "EV-012")


def cite_whatever_is_there(request: Request) -> str:
    """What a model that makes something up does: quote the first real sentence it was shown."""
    for source_id, (header, text) in request.sources().items():
        if "kind=company_policy" not in header:
            continue
        for line in text.splitlines():
            line = line.strip()
            if len(line.split()) >= 5 and not line.startswith(("#", "|", "-", ">")):
                return reply_json(source_id, line)
    raise AssertionError("no company sentence in the context to quote")


def scripted(request: Request) -> str:
    """An 'ideal' model for these items, except that it makes up an answer for EV-012."""
    user = request.user
    if "recovery time objective" in user and "BEGIN QUESTION\nWhat is" in user:
        return cite("recovery time objective is 8 hours")(request)
    if "Within how many days are critical vulnerabilities fixed" in user:
        return cite("Critical vulnerabilities are fixed within 14 days.")(request)
    if "After how many minutes of inactivity" in user:
        return cite("lock their screen after 10 minutes of inactivity")(request)
    if "How many employees" in user:  # EV-012 is labeled blank, and this model answers it anyway
        return cite_whatever_is_there(request)
    return NO_EVIDENCE_REPLY


def test_run_eval_grades_real_items_and_reports_the_failed_acceptance(tmp_path: Path) -> None:
    evalset: EvalSet = load_eval_set(EVAL_SET)
    items = [i for i in evalset.items if i.id in OFFLINE_IDS]
    assert [i.id for i in items] == list(OFFLINE_IDS)
    seen: list[str] = []
    steps: list[Step] = [scripted] * 20

    async def scenario() -> list[Row]:
        async with answering(tmp_path, steps) as (service, _, _):
            return await run_eval(items, service, on_row=lambda row: seen.append(row.item.id))

    rows = asyncio.run(scenario())
    assert seen == list(OFFLINE_IDS)  # progress was reported one row at a time, in order
    by_id = {row.item.id: row for row in rows}
    for item_id in ("EV-008", "EV-009", "EV-010"):
        assert by_id[item_id].problems == (), (item_id, by_id[item_id].problems)
        assert by_id[item_id].outcome.result.status == "SUPPORTED"
    assert by_id["EV-011"].problems == ()  # a blank-labeled item the model rightly left blank
    assert by_id["EV-012"].problems and "was answered" in by_id["EV-012"].problems[0]
    assert acceptance_problems(rows) == [
        "EV-012"
    ]  # the one that must STOP AND REPORT in a live run
    report = format_report(rows)
    assert "FAIL (answered: EV-012)" in report and "label met: 4 of 5" in report


@pytest.mark.parametrize("split", ["dev", "heldout"])
def test_every_eval_item_can_be_graded_with_a_blank_outcome(split: str) -> None:
    """A smoke test over the whole real set: grading never crashes, and with every cell blank the
    blank-labeled items are all fine and every answer-labeled item is a problem."""
    evalset = load_eval_set(EVAL_SET)
    items = [i for i in evalset.items if i.split == split]
    rows = rows_for(*[(i, blank("no_evidence")) for i in items])
    assert acceptance_problems(rows) == []
    assert all((not r.problems) == (r.item.expected == "blank") for r in rows)
