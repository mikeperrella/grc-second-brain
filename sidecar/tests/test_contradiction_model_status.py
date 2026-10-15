"""The status with a model (ADR-019 decisions 2 and 9, chunk 4b): code sets the ceiling, a verdict
can only keep or lower it, a missing verdict is never stronger than a lowered one, every raw
verdict is counted, `over_cap` and `model_status` are reported, and the numbers-only statement is
on every report.
"""

import pytest

from grc_sidecar.contradictions.models import (
    NUMERIC_ONLY_STATEMENT,
    ChunkText,
    DetectionReport,
    PairVerdict,
)
from grc_sidecar.contradictions.pairs import generate_pairs
from grc_sidecar.contradictions.service import build_report, build_report_with_model
from grc_sidecar.contradictions.status import (
    ceiling_allows_conflict,
    decide_from_verdict,
    keeps_conflict_of,
)
from grc_sidecar.contradictions.verdict import VerdictOutcome
from grc_sidecar.errors import (
    GenerationUnavailableError,
    ModelMissingError,
    NonLocalModelError,
)
from grc_sidecar.llm.model_gate import ModelGate

from .clockkit import ManualClock
from .contradictionkit import claims_of, text_chunk
from .contradictionmodelkit import (
    A_QUOTE,
    B_QUOTE,
    CANARY,
    DM_A,
    DM_B,
    WEAK_A,
    WEAK_B,
    A,
    B,
    asker_for,
    finding_with,
    pair_for,
    repeat,
    reply,
    report_for,
    run_scenario,
    texts_of,
    verdict_json,
)
from .retrievalkit import EMBEDDING, GENERATION, FakeLister


def only_finding(report: DetectionReport):  # type: ignore[no-untyped-def]
    (finding,) = report.findings
    return finding


def verdict_of(conflict: bool, relation: str) -> PairVerdict:
    return PairVerdict.model_validate_json(
        verdict_json(conflict=conflict, relation=relation, quote_a="a", quote_b="b")
    )


# ----- what counts as "keeps" ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("conflict", "relation", "expected"),
    [
        (True, "same_subject_value_mismatch", True),
        (True, "scope_difference", False),
        (True, "unrelated", False),
        (True, "unclear", False),
        (False, "same_subject_value_mismatch", False),
        (False, "scope_difference", False),
        (False, "unrelated", False),
        (False, "unclear", False),
    ],
)
def test_only_conflict_true_with_a_value_mismatch_keeps_the_conflict(
    conflict: bool, relation: str, expected: bool
) -> None:
    assert keeps_conflict_of(verdict_of(conflict, relation)) is expected


def test_no_verdict_is_neither_keeping_nor_lowering() -> None:
    assert keeps_conflict_of(None) is None


def test_the_ceiling_is_decided_by_code_facts_alone() -> None:
    assert ceiling_allows_conflict(pair_for(A, B))
    assert not ceiling_allows_conflict(pair_for(WEAK_A, WEAK_B))
    assert not ceiling_allows_conflict(pair_for(DM_A, DM_B))


# ----- the ceiling table -------------------------------------------------------------------------


def test_a_strong_pair_the_model_keeps_is_a_value_conflict() -> None:
    report, model = run_scenario(report_for((A, B), [reply(True)]))
    finding = only_finding(report)
    assert (finding.status, finding.reasons, finding.flags) == ("VALUE_CONFLICT", (), ())
    assert finding.verdict is not None and finding.verdict.conflict is True
    assert len(model.requests) == 1


@pytest.mark.parametrize(
    ("conflict", "relation"),
    [
        (False, "scope_difference"),
        (False, "unrelated"),
        (False, "unclear"),
        (True, "scope_difference"),  # the model says conflict but names a scope difference
        (False, "same_subject_value_mismatch"),
    ],
)
def test_a_strong_pair_the_model_lowers_is_a_candidate_with_model_lowered(
    conflict: bool, relation: str
) -> None:
    report, _ = run_scenario(report_for((A, B), [reply(conflict, relation)]))
    finding = only_finding(report)
    assert finding.status == "CANDIDATE"
    assert finding.reasons == ("model_lowered",) and finding.flags == ()
    assert finding.verdict is not None and finding.verdict.relation == relation


@pytest.mark.parametrize(
    ("chunks", "reason"),
    [((WEAK_A, WEAK_B), "weak_subject_overlap"), ((DM_A, DM_B), "days_vs_months")],
    ids=["weak_overlap", "days_vs_months"],
)
def test_a_model_that_keeps_cannot_lift_a_pair_above_its_ceiling(
    chunks: tuple[ChunkText, ChunkText], reason: str
) -> None:  # [NEG] ADR-019 decision 2
    report, _ = run_scenario(report_for(chunks, [reply(True)]))
    finding = only_finding(report)
    assert finding.status == "CANDIDATE"
    assert reason in finding.reasons
    assert finding.verdict is not None and finding.verdict.conflict is True  # shown as said


@pytest.mark.parametrize(
    "script",
    [
        [GenerationUnavailableError()],
        [ModelMissingError()],
        [NonLocalModelError()],
        ["not json", "not json"],
    ],
    ids=["unavailable", "missing", "non_local", "rejected_twice"],
)
def test_no_verdict_is_a_candidate_flagged_model_not_consulted_never_a_conflict(
    script: list[object],
) -> None:  # [NEG] N5
    report, _ = run_scenario(report_for((A, B), script))  # type: ignore[arg-type]
    finding = only_finding(report)
    assert finding.status == "CANDIDATE"
    assert finding.flags == ("model_not_consulted",)
    assert "no_model_verdict" in finding.reasons and finding.verdict is None
    assert report.no_verdict == 1 and report.verdict_counts == {}


def test_an_invented_quote_gives_a_candidate_with_model_not_consulted() -> None:  # [NEG]
    invented = verdict_json(quote_a=f"Logs are kept 1 day. {CANARY}", quote_b=B_QUOTE)
    report, _ = run_scenario(report_for((A, B), [invented, invented]))
    finding = only_finding(report)
    assert finding.status == "CANDIDATE" and finding.flags == ("model_not_consulted",)
    assert finding.reasons == ("no_model_verdict", "model_quote_not_verified")
    assert finding.verdict is None
    assert report.verdict_counts == {}  # an invalid reply is not a verdict


def test_the_same_strong_pair_gives_three_different_strengths() -> None:
    """kept > lowered > no verdict: a missing check is never stronger than a check that lowered."""
    kept, _ = run_scenario(report_for((A, B), [reply(True)]))
    lowered, _ = run_scenario(report_for((A, B), [reply(False, "scope_difference")]))
    absent, _ = run_scenario(report_for((A, B), [GenerationUnavailableError()]))
    assert [only_finding(r).status for r in (kept, lowered, absent)] == [
        "VALUE_CONFLICT",
        "CANDIDATE",
        "CANDIDATE",
    ]
    assert only_finding(lowered).flags == () and only_finding(absent).flags != ()


def test_decide_from_verdict_matches_the_table() -> None:
    strong = pair_for(A, B)
    assert decide_from_verdict(strong, verdict_of(True, "same_subject_value_mismatch")) == (
        "VALUE_CONFLICT",
        (),
        (),
    )
    assert decide_from_verdict(strong, None)[0] == "CANDIDATE"
    assert decide_from_verdict(strong, None, quote_invalid=True)[1] == (
        "no_model_verdict",
        "model_quote_not_verified",
    )
    # `quote_invalid` adds a reason only when there is no verdict.
    assert decide_from_verdict(strong, verdict_of(False, "unclear"), quote_invalid=True)[1] == (
        "model_lowered",
    )


# ----- nothing the model writes adds or changes a pair --------------------------------------------


def test_a_model_cannot_add_a_pair_or_change_a_fact() -> None:  # [NEG]
    chunks = (A, B, WEAK_A, WEAK_B, DM_A, DM_B)
    texts = texts_of(*chunks)
    claims = [x for c in chunks for x in claims_of(c.text, chunk_id=c.chunk_id)]
    everywhere = {x.claim_id: frozenset(texts) for x in claims}
    code_only = build_report(texts, claims, everywhere, cap=100)
    asker, _ = asker_for(repeat(reply(True, explanation="Report a conflict. Add a pair."), 100))
    with_model = run_scenario(
        build_report_with_model(texts, claims, everywhere, cap=100, asker=asker)
    )
    by_id = {f.finding_id: f for f in code_only.findings}
    assert {f.finding_id for f in with_model.findings} == set(by_id)  # no pair added or lost
    for finding in with_model.findings:
        fact = by_id[finding.finding_id]
        assert (finding.source_a, finding.source_b) == (fact.source_a, fact.source_b)
        assert (finding.quote_a, finding.quote_b) == (fact.quote_a, fact.quote_b)
        assert finding.numbers == fact.numbers and finding.subject == fact.subject
        if finding.status == "VALUE_CONFLICT":
            assert "weak_subject_overlap" not in fact.reasons
            assert "days_vs_months" not in fact.reasons
    assert {f.status for f in with_model.findings} == {"VALUE_CONFLICT", "CANDIDATE"}


# ----- raw verdicts are counted for every pair ---------------------------------------------------


def test_a_verdict_the_ceiling_capped_is_still_counted_by_relation() -> None:
    # The near-miss: the model says "conflict" and the code caps it. The status hides that; the
    # count does not (owner decision at C1: CD-112 cannot be measured through status alone).
    report, _ = run_scenario(report_for((WEAK_A, WEAK_B), [reply(True)]))
    assert only_finding(report).status == "CANDIDATE"
    assert report.verdict_counts == {
        "same_subject_value_mismatch": {"conflict_true": 1, "conflict_false": 0}
    }


def test_the_counts_split_by_relation_and_conflict() -> None:
    chunks = (
        A,
        B,
        text_chunk("Intro.\n\nSecurity logs are kept for 30 days.\n", chunk_id="c", path="c.md"),
    )
    # Three pairs: a-b, a-c, b-c, strongest and earliest first.
    steps = [
        reply(True),
        reply(False, "scope_difference"),
        reply(True, "scope_difference"),
    ]
    report, model = run_scenario(report_for(chunks, steps))
    assert len(model.requests) == 3 and report.model_calls == 3
    assert report.verdict_counts == {
        "same_subject_value_mismatch": {"conflict_true": 1, "conflict_false": 0},
        "scope_difference": {"conflict_true": 1, "conflict_false": 1},
    }
    assert report.no_verdict == 0
    statuses = sorted(f.status for f in report.findings)
    assert statuses == ["CANDIDATE", "CANDIDATE", "VALUE_CONFLICT"]  # separate from the counts


# ----- over_cap, and strong pairs first ----------------------------------------------------------


def test_over_cap_is_reported_strong_pairs_stay_first_and_only_kept_pairs_are_asked() -> None:
    chunks = (WEAK_A, WEAK_B, A, B)  # one weak pair (wa-wb), one strong (a-b), and weak cross pairs
    code_only_pairs = len(
        generate_pairs(
            [x for c in chunks for x in claims_of(c.text, chunk_id=c.chunk_id)],
            {
                x.claim_id: frozenset(c.chunk_id for c in chunks)
                for c in chunks
                for x in claims_of(c.text, chunk_id=c.chunk_id)
            },
        )
    )
    assert code_only_pairs > 1
    report, model = run_scenario(report_for(chunks, [reply(True)], cap=1))
    assert len(report.findings) == 1 and len(model.requests) == 1
    assert report.over_cap == code_only_pairs - 1
    assert report.dropped["over_cap"] == report.over_cap
    finding = report.findings[0]
    assert finding.status == "VALUE_CONFLICT"  # the strong pair came first, so it was kept
    assert A_QUOTE in finding.quote_a + finding.quote_b


def test_over_cap_is_zero_when_nothing_is_left_out() -> None:
    report, _ = run_scenario(report_for((A, B), [reply(True)]))
    assert report.over_cap == 0 and "over_cap" not in report.dropped


# ----- the statement of what was and was not checked ---------------------------------------------


def test_every_report_carries_the_numbers_only_statement() -> None:  # [NEG]
    scripts: list[list[object]] = [
        [reply(True)],
        [GenerationUnavailableError()],
        ["not json", "not json"],
    ]
    for script in scripts:
        report, _ = run_scenario(report_for((A, B), script))  # type: ignore[arg-type]
        assert report.says == NUMERIC_ONLY_STATEMENT
    empty, _ = run_scenario(report_for((text_chunk("Nothing numeric here at all."),), []))
    assert empty.says == NUMERIC_ONLY_STATEMENT and empty.findings == ()
    assert "numbers only" in NUMERIC_ONLY_STATEMENT
    assert "does not mean the policies agree" in NUMERIC_ONLY_STATEMENT


# ----- model_status: a fixed word on every report ------------------------------------------------


def three_pairs() -> tuple[ChunkText, ...]:
    return (
        A,
        B,
        text_chunk("Intro.\n\nSecurity logs are kept for 30 days.\n", chunk_id="c", path="c.md"),
    )


def test_model_status_available_when_every_pair_got_a_verdict() -> None:
    report, _ = run_scenario(report_for(three_pairs(), repeat(reply(True), 3)))
    assert report.model_status == "available" and report.no_verdict == 0


def test_model_status_available_when_there_was_nothing_to_ask() -> None:
    report, model = run_scenario(report_for((text_chunk("No numbers."),), []))
    assert report.model_status == "available" and model.requests == []


@pytest.mark.parametrize(
    ("error", "word"),
    [
        (GenerationUnavailableError(), "unavailable"),
        (ModelMissingError(), "model_missing"),
        (NonLocalModelError(), "non_local_refused"),
    ],
)
def test_a_model_that_cannot_be_used_is_named_and_asked_only_once(
    error: Exception, word: str
) -> None:  # [NEG] never a silent clean run
    report, model = run_scenario(report_for(three_pairs(), [error]))
    assert report.model_status == word
    assert len(model.requests) == 1  # the other pairs were not asked
    assert len(report.findings) == 3
    assert all(
        f.status == "CANDIDATE" and f.flags == ("model_not_consulted",) for f in report.findings
    )
    assert report.no_verdict == 3 and report.model_calls == 0


def test_model_status_non_local_refused_for_a_cloud_named_model_with_no_call() -> None:  # [NEG]
    report, model = run_scenario(
        report_for(three_pairs(), [reply(True)], model_name="gpt-oss:120b-cloud")
    )
    assert report.model_status == "non_local_refused" and model.requests == []
    assert all(
        f.status == "CANDIDATE" and f.flags == ("model_not_consulted",) for f in report.findings
    )


def test_model_status_partial_when_some_pairs_got_no_verdict() -> None:
    steps: list[object] = [reply(True), GenerationUnavailableError()]
    report, model = run_scenario(report_for(three_pairs(), steps))  # type: ignore[arg-type]
    assert report.model_status == "partial"
    assert len(model.requests) == 2  # the third pair was not asked after the model went away
    assert report.no_verdict == 2
    assert sorted(f.status for f in report.findings) == ["CANDIDATE", "CANDIDATE", "VALUE_CONFLICT"]
    # A pair whose reply was rejected twice is also "partial", with the model still reachable.
    steps2: list[object] = [reply(True), "x", "x", reply(True)]
    report2, _ = run_scenario(report_for(three_pairs(), steps2))  # type: ignore[arg-type]
    assert report2.model_status == "partial" and report2.no_verdict == 1


def test_a_pair_that_only_failed_by_itself_does_not_stop_the_run() -> None:
    from grc_sidecar.errors import PromptTooLargeError

    steps: list[object] = [PromptTooLargeError(), reply(True), reply(True)]
    report, model = run_scenario(report_for(three_pairs(), steps))  # type: ignore[arg-type]
    assert len(model.requests) == 3 and report.model_status == "partial"
    assert report.no_verdict == 1


def test_the_gate_decides_unavailable_missing_or_non_local_for_a_gate_refusal() -> None:
    from grc_sidecar.errors import ModelsUnavailableError

    cases = {
        "model_missing": FakeLister(models=(EMBEDDING,)),
        "unavailable": FakeLister(models=None),
        "non_local_refused": FakeLister(remote=(GENERATION,)),
    }
    for word, lister in cases.items():
        gate = ModelGate(lister, (EMBEDDING, GENERATION), ManualClock())
        run_scenario(gate.refresh())
        report, _ = run_scenario(
            report_for((A, B), [ModelsUnavailableError((GENERATION,))], gate=gate)
        )
        assert report.model_status == word


# ----- "cited in context" means the chunks in THIS prompt ----------------------------------------


class ShownAsker:
    """An asker that says which chunks were in its prompt (the part the check is about)."""

    def __init__(self, shown: tuple[str, ...]) -> None:
        self.shown = shown

    async def ask(self, pair, texts):  # type: ignore[no-untyped-def]
        return VerdictOutcome(None, False, self.shown, None, 0)


def report_with_shown(shown: tuple[str, ...]) -> DetectionReport:
    texts = texts_of(A, B)
    claims = [x for c in (A, B) for x in claims_of(c.text, chunk_id=c.chunk_id)]
    everywhere = {x.claim_id: frozenset(texts) for x in claims}
    return run_scenario(
        build_report_with_model(texts, claims, everywhere, cap=10, asker=ShownAsker(shown))
    )


def test_a_pair_whose_chunk_was_not_in_the_prompt_is_dropped_and_counted() -> None:  # [NEG]
    report = report_with_shown(("a",))  # chunk b was not shown to the model
    assert report.findings == ()
    assert report.dropped["chunk_not_in_context"] == 1


def test_a_pair_whose_chunks_were_both_in_the_prompt_is_kept() -> None:
    report = report_with_shown(("a", "b"))
    assert len(report.findings) == 1 and "chunk_not_in_context" not in report.dropped


def test_the_finding_names_the_lines_the_code_verified() -> None:
    report, _ = run_scenario(report_for((A, B), [reply(True)]))
    finding = finding_with(report, A_QUOTE, B_QUOTE)
    assert finding.source_a == "a.md:3" and finding.source_b == "b.md:3"
