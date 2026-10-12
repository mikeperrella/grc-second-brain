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


# Reasons that come from a deterministic check in our code, as opposed to the model's own verdict.
CODE_REASONS = (
    "identifier_not_in_source",
    "framework_only",
    "quote_not_verified",
    "quote_too_short",
    "source_not_in_context",
    "source_changed",
)


def blanked_by(outcome: AnswerOutcome) -> str:
    """What made a result blank: a check in our code, the model's own 'no evidence', or nothing
    (the item was answered). `other` covers limits such as a model failure."""
    result = outcome.result
    if result.answer is not None:
        return "ANSWERED"
    code = [reason for reason in result.reasons if reason in CODE_REASONS]
    if code:
        return "code: " + "+".join(code)
    if "no_evidence" in result.reasons:
        return "model: no_evidence"
    return "other: " + (",".join(result.reasons) or "-")


def value_found(row: Row) -> bool | None:
    """For an answered exact_value item: does the answer text contain the expected value?
    None for any other item. A measurement only: it never changes a status or the acceptance."""
    item, result = row.item, row.outcome.result
    if item.category != "exact_value" or not item.expected_value or result.answer is None:
        return None
    return (
        " ".join(item.expected_value.split()).casefold()
        in " ".join(result.answer.split()).casefold()
    )


def _side_by_side(rows: Sequence[Row]) -> list[str]:
    """Every control next to the item it controls (EV-030 and its bug-bounty control)."""
    by_id = {row.item.id: row for row in rows}
    out: list[str] = []
    for row in rows:
        target = by_id.get(row.item.control_of or "")
        if target is None:
            continue
        out.append(f"  {'':<8}{'id':<8}{'status':<13}{'blanked by':<34}question")
        for label, pair in (("control", row), ("tested", target)):
            result = pair.outcome.result
            out.append(
                f"  {label:<8}{pair.item.id:<8}{result.status:<13}{blanked_by(pair.outcome):<34.34}"
                f"{pair.item.question[:60]}"
            )
        same = target.outcome.result.status == row.outcome.result.status
        out.append(f"  -> {'same outcome' if same else 'DIFFERENT outcomes'}")
    return out


def _identifier_section(rows: Sequence[Row]) -> list[str]:
    identifier = [row for row in rows if row.item.category == "identifier_only"]
    out = [
        "IDENTIFIER-ONLY items (a code check should blank these; reported apart from the model):"
    ]
    if not identifier:
        return [*out, "  none in this split"]
    for row in identifier:
        result = row.outcome.result
        reasons = ",".join(result.reasons) or "-"
        out.append(
            f"  {row.item.id:<8}{row.item.question:<10.10}{result.status:<13}"
            f"{blanked_by(row.outcome):<48}reasons={reasons}"
        )
    counts = Counter(blanked_by(row.outcome).split(":")[0] for row in identifier)
    out.append(
        f"  blanked by a code check: {counts.get('code', 0)} of {len(identifier)}; "
        f"by the model: {counts.get('model', 0)}; answered: {counts.get('ANSWERED', 0)}; "
        f"other: {counts.get('other', 0)}"
    )
    return out


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
    out.append("")
    out.extend(_identifier_section(rows))
    others = [
        r for r in rows if r.item.expected == "blank" and r.item.category != "identifier_only"
    ]
    how = Counter(blanked_by(row.outcome).split(":")[0] for row in others)
    out.append(
        "MODEL-DEPENDENT blank-labeled items (absent, bait, conflict, near miss, injection): "
        f"{len(others)}; ended blank by the model's own no_evidence: {how.get('model', 0)}, "
        f"by a code check: {how.get('code', 0)}, answered: {how.get('ANSWERED', 0)}, "
        f"other: {how.get('other', 0)}"
    )
    pairs = _side_by_side(rows)
    if pairs:
        out.append("")
        out.append("CONTROL PAIRS (an injection item next to its control with no injection text):")
        out.extend(pairs)
    exact = [row for row in rows if row.item.category == "exact_value"]
    if exact:
        answered_exact = [row for row in exact if value_found(row) is not None]
        found = [row for row in answered_exact if value_found(row)]
        out.append("")
        out.append(
            "EXACT VALUE (a measurement, not a status gate and not an acceptance criterion): "
            f"answer text contains the expected value in {len(found)} of {len(answered_exact)} "
            f"answered exact_value items ({len(exact) - len(answered_exact)} ended blank)"
        )
        out.extend(
            f"  {row.item.id}: expected {row.item.expected_value!r}, "
            f"{'found' if value_found(row) else 'NOT found'} in the answer"
            for row in answered_exact
        )
    out.append("")
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
