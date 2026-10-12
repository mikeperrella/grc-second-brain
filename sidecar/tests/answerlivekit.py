"""Grading and reporting for the evaluation run (tests only; ADR-013, decision 16).

Kept apart from `test_answer_live.py` so that an ordinary offline test can exercise exactly this
code with a scripted model (`test_answer_eval_harness.py`). The live test then only has to connect a
real Ollama.

What is reported: counts, never percentages (with about 30 dev and 18 held-out items every figure is
a small-sample observation), and the acceptance criterion from the product brief: every item labeled
blank must end blank. Nothing here has a target number.
"""

import statistics
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from grc_sidecar.questionnaire.service import AnswerOutcome, AnswerService

from .evalkit import CATEGORIES, EvalItem


@dataclass(frozen=True)
class Row:
    item: EvalItem
    outcome: AnswerOutcome
    problems: tuple[str, ...]  # empty means the label was met


def grade(item: EvalItem, outcome: AnswerOutcome) -> list[str]:
    """Everything wrong with this result against its label, as readable lines (empty = fine)."""
    result = outcome.result
    problems: list[str] = []
    if item.expected == "blank":
        if result.status != "NEEDS_REVIEW" or result.answer is not None:
            problems.append(f"blank-labeled item was answered (status {result.status})")
        return problems
    if result.status not in item.acceptable_statuses:
        problems.append(f"status {result.status} is not one of {item.acceptable_statuses}")
    missing = [reason for reason in item.expected_reasons if reason not in result.reasons]
    if missing:
        problems.append(f"missing required reasons {missing}")
    if result.status != "NEEDS_REVIEW":
        cite = result.citation
        if cite is None:
            problems.append("answered without a citation")
        elif not any(
            ref.doc_id == cite.doc_id
            and ref.start_line <= cite.start_line
            and cite.end_line <= ref.end_line
            for ref in item.acceptable_evidence
        ):
            problems.append(
                f"cited {cite.doc_id}:{cite.start_line}-{cite.end_line}, outside the "
                "acceptable evidence"
            )
    return problems


async def run_eval(
    items: Sequence[EvalItem],
    service: AnswerService,
    *,
    on_row: Callable[[Row], None] | None = None,
) -> list[Row]:
    """Ask every item in order. `on_row` sees each row as it is finished (live progress)."""
    rows: list[Row] = []
    for item in items:
        outcome = await service.answer(item.question)
        row = Row(item, outcome, tuple(grade(item, outcome)))
        rows.append(row)
        if on_row is not None:
            on_row(row)
    return rows


ROW_HEADER = (
    f"{'id':<7} {'category':<15} {'expect':<6} {'status':<12} {'reasons':<34} "
    f"{'cited':<34} {'att':>3} {'secs':>6} {'p_tok':>6} {'o_tok':>5}  verdict"
)


def format_row(row: Row) -> str:
    result = row.outcome.result
    cite = result.citation
    cited = "-" if cite is None else f"{cite.source_path}:{cite.start_line}-{cite.end_line}"
    reasons = ",".join(result.reasons) or "-"
    p_tok = "-" if row.outcome.prompt_eval_count is None else str(row.outcome.prompt_eval_count)
    o_tok = "-" if row.outcome.eval_count is None else str(row.outcome.eval_count)
    verdict = "ok" if not row.problems else "PROBLEM: " + "; ".join(row.problems)
    return (
        f"{row.item.id:<7} {row.item.category:<15} {row.item.expected:<6} {result.status:<12} "
        f"{reasons:<34.34} {cited:<34.34} {row.outcome.attempts:>3} {row.outcome.duration:>6.1f} "
        f"{p_tok:>6} {o_tok:>5}  {verdict}"
    )


def acceptance_problems(rows: Sequence[Row]) -> list[str]:
    """The ids of blank-labeled items that were answered: the product brief's criterion."""
    return [
        row.item.id
        for row in rows
        if row.item.expected == "blank"
        and (row.outcome.result.status != "NEEDS_REVIEW" or row.outcome.result.answer is not None)
    ]


def format_report(rows: Sequence[Row], *, header: str = "") -> str:
    """The whole run as text: a table, then counts, the acceptance result and what was observed."""
    out = [header] if header else []
    out.append(ROW_HEADER)
    out.extend(format_row(row) for row in rows)
    out.append("")
    statuses = Counter(row.outcome.result.status for row in rows)
    out.append(
        "statuses: "
        + ", ".join(f"{s}={statuses.get(s, 0)}" for s in ("SUPPORTED", "PARTIAL", "NEEDS_REVIEW"))
    )
    correct = sum(1 for row in rows if not row.problems)
    out.append(f"label met: {correct} of {len(rows)}")
    for category in CATEGORIES:
        in_category = [row for row in rows if row.item.category == category]
        if in_category:
            met = sum(1 for row in in_category if not row.problems)
            out.append(f"  {category:<16} {met} of {len(in_category)}")
    blanks = [row for row in rows if row.item.expected == "blank"]
    answered = acceptance_problems(rows)
    out.append(
        f"blank-labeled items that ended blank: {len(blanks) - len(answered)} of {len(blanks)}"
    )
    verdict = "PASS" if not answered else f"FAIL (answered: {', '.join(answered)})"
    out.append(f"ACCEPTANCE (every blank-labeled item ends blank): {verdict}")
    answers = [row for row in rows if row.item.expected == "answer"]
    went_blank = [row for row in answers if row.outcome.result.status == "NEEDS_REVIEW"]
    out.append(f"answer-labeled items that ended blank: {len(went_blank)} of {len(answers)}")
    ratios = [
        row.outcome.prompt_chars / row.outcome.prompt_eval_count
        for row in rows
        if row.outcome.prompt_eval_count and row.outcome.prompt_chars
    ]
    if ratios:
        out.append(
            "characters per token (prompt characters / prompt_eval_count, per question): "
            f"min {min(ratios):.2f}, median {statistics.median(ratios):.2f}, "
            f"max {max(ratios):.2f}, over {len(ratios)} questions"
        )
    reasons = Counter(r for row in rows for r in row.outcome.done_reasons)
    out.append(
        "done_reason counts: "
        + (", ".join(f"{k}={v}" for k, v in sorted(reasons.items())) or "none")
    )
    seconds = [row.outcome.duration for row in rows]
    if seconds:
        out.append(
            f"seconds per question: min {min(seconds):.1f}, max {max(seconds):.1f}, "
            f"total {sum(seconds):.1f}"
        )
    problems = [(row.item.id, p) for row in rows for p in row.problems]
    out.append(f"problems: {len(problems)}")
    out.extend(f"  {item_id}: {problem}" for item_id, problem in problems)
    return "\n".join(out)
