"""The labeled evaluation set is well formed and every label points at real text.

The set is committed before any prompt code exists (M5b). Two kinds of test:

* the real file passes `eval_problems` with no problems;
* each rule of the checker is proven to be able to fail, by feeding it a copy of the real set with
  one deliberate defect (a checker that accepts everything would make the first kind hollow).
"""

from pathlib import Path

import pytest

from grc_sidecar.vault.models import ParsedDocument

from .evalkit import (
    CATEGORIES,
    EvalItem,
    EvalSet,
    EvidenceRef,
    eval_problems,
    load_eval_documents,
    load_eval_set,
)
from .vaultkit import EVAL_SET, REPO_ROOT


@pytest.fixture(scope="module")
def evalset() -> EvalSet:
    return load_eval_set(EVAL_SET)


@pytest.fixture(scope="module")
def docs(evalset: EvalSet) -> dict[str, ParsedDocument]:
    return load_eval_documents(REPO_ROOT, evalset.vault_roots)


def with_item(evalset: EvalSet, item_id: str, **changes: object) -> EvalSet:
    """A copy of the set in which one item is changed."""
    items = [i.model_copy(update=changes) if i.id == item_id else i for i in evalset.items]
    return evalset.model_copy(update={"items": items})


def item_of(evalset: EvalSet, item_id: str) -> EvalItem:
    return next(i for i in evalset.items if i.id == item_id)


# ----- the real file ---------------------------------------------------------------------------


def test_the_real_eval_set_has_no_problems(
    evalset: EvalSet, docs: dict[str, ParsedDocument]
) -> None:
    assert eval_problems(evalset, docs) == []


def test_the_set_has_the_planned_shape(evalset: EvalSet) -> None:
    dev = [i for i in evalset.items if i.split == "dev"]
    held = [i for i in evalset.items if i.split == "heldout"]
    assert (len(dev), len(held)) == (30, 18)
    for split_items in (dev, held):
        assert {i.category for i in split_items} == set(CATEGORIES)
    assert evalset.packs_indexed is True
    assert evalset.clock_today.isoformat() == "2026-10-10"
    assert evalset.vault_roots == ["vault-demo/vault", "fixtures/adversarial-m5/vault"]


def test_splits_are_disjoint_in_ids_and_questions(evalset: EvalSet) -> None:
    dev = {i.id for i in evalset.items if i.split == "dev"}
    held = {i.id for i in evalset.items if i.split == "heldout"}
    assert not dev & held

    def questions(split: str) -> set[str]:
        return {" ".join(i.question.split()).casefold() for i in evalset.items if i.split == split}

    assert not questions("dev") & questions("heldout")


def test_the_two_conflicts_are_independent_across_the_splits(evalset: EvalSet) -> None:
    """Dev tests the security-log conflict, held-out the backup conflict, so tuning on one
    cannot leak into the other (see vault-demo/README.md)."""
    dev = next(i for i in evalset.items if i.split == "dev" and i.category == "conflict")
    held = next(i for i in evalset.items if i.split == "heldout" and i.category == "conflict")
    assert "security logs" in dev.question and "backups" in held.question


def test_every_blank_item_is_labeled_blank_and_counted(evalset: EvalSet) -> None:
    blanks = [i for i in evalset.items if i.expected == "blank"]
    assert {i.category for i in blanks} == {
        "absent",
        "framework_bait",
        "identifier_only",
        "conflict",
        "near_miss",
        "injection",
    }
    # dev: 4 absent + 3 bait + 3 identifier + 1 conflict + 4 near miss + 1 injection
    # held-out: 2 absent + 2 bait + 2 identifier + 1 conflict + 2 near miss + 0 injection
    assert sum(i.split == "dev" for i in blanks) == 16
    assert sum(i.split == "heldout" for i in blanks) == 9


# ----- the checker can fail ---------------------------------------------------------------------


def test_a_wrong_line_range_is_caught(evalset: EvalSet, docs: dict[str, ParsedDocument]) -> None:
    item = item_of(evalset, "EV-003")  # [NEG] off by one line: the phrase is on the next line
    ref = item.acceptable_evidence[0]
    moved = ref.model_copy(update={"start_line": ref.start_line + 1, "end_line": ref.end_line + 1})
    problems = eval_problems(with_item(evalset, "EV-003", acceptable_evidence=[moved]), docs)
    assert any("EV-003" in p and "phrase not found" in p for p in problems)


def test_a_range_past_the_end_of_the_file_is_caught(
    evalset: EvalSet, docs: dict[str, ParsedDocument]
) -> None:  # [NEG]
    ref = item_of(evalset, "EV-003").acceptable_evidence[0]
    far = ref.model_copy(update={"start_line": 500, "end_line": 501})
    problems = eval_problems(with_item(evalset, "EV-003", acceptable_evidence=[far]), docs)
    assert any("EV-003" in p and "outside" in p for p in problems)


def test_evidence_inside_the_frontmatter_is_caught(
    evalset: EvalSet, docs: dict[str, ParsedDocument]
) -> None:  # [NEG] frontmatter is never chunked, so it can never be cited
    ref = item_of(evalset, "EV-003").acceptable_evidence[0]
    front = ref.model_copy(update={"start_line": 2, "end_line": 2, "phrase": "doc_id"})
    problems = eval_problems(with_item(evalset, "EV-003", acceptable_evidence=[front]), docs)
    assert any("EV-003" in p and "frontmatter" in p for p in problems)


def test_an_unknown_document_is_caught(evalset: EvalSet, docs: dict[str, ParsedDocument]) -> None:
    ghost = EvidenceRef(doc_id="NO-SUCH-DOC", start_line=1, end_line=1, phrase="x")
    problems = eval_problems(with_item(evalset, "EV-003", acceptable_evidence=[ghost]), docs)
    assert any("EV-003" in p and "unknown doc_id" in p for p in problems)  # [NEG]


def test_a_blank_item_with_evidence_is_caught(
    evalset: EvalSet, docs: dict[str, ParsedDocument]
) -> None:  # [NEG]
    evidence = item_of(evalset, "EV-003").acceptable_evidence
    problems = eval_problems(with_item(evalset, "EV-011", acceptable_evidence=evidence), docs)
    assert any("EV-011" in p and "blank item" in p for p in problems)


def test_an_answer_item_that_accepts_needs_review_is_caught(
    evalset: EvalSet, docs: dict[str, ParsedDocument]
) -> None:  # [NEG]
    changes: dict[str, object] = {"acceptable_statuses": ["SUPPORTED", "NEEDS_REVIEW"]}
    problems = eval_problems(with_item(evalset, "EV-003", **changes), docs)
    assert any("EV-003" in p and "cannot accept NEEDS_REVIEW" in p for p in problems)


def test_a_duplicate_id_is_caught(evalset: EvalSet, docs: dict[str, ParsedDocument]) -> None:
    clone = item_of(evalset, "EV-003").model_copy(update={"question": "A different question?"})
    broken = evalset.model_copy(update={"items": [*evalset.items, clone]})
    assert "duplicate id EV-003" in eval_problems(broken, docs)  # [NEG]


def test_a_question_shared_by_both_splits_is_caught(
    evalset: EvalSet, docs: dict[str, ParsedDocument]
) -> None:  # [NEG] the held-out split must stay unseen
    dev_question = item_of(evalset, "EV-011").question
    problems = eval_problems(with_item(evalset, "EV-107", question=dev_question), docs)
    assert any("EV-107" in p and "other split" in p for p in problems)


def test_a_split_missing_a_category_is_caught(
    evalset: EvalSet, docs: dict[str, ParsedDocument]
) -> None:  # [NEG]
    kept = [i for i in evalset.items if not (i.split == "heldout" and i.category == "conflict")]
    problems = eval_problems(evalset.model_copy(update={"items": kept}), docs)
    assert "split heldout has no conflict item" in problems


def test_an_overdue_document_cited_by_a_normal_item_is_caught(
    evalset: EvalSet, docs: dict[str, ParsedDocument]
) -> None:  # [NEG] it would then be expected SUPPORTED, which the overdue rule forbids
    overdue_ref = item_of(evalset, "EV-022").acceptable_evidence
    problems = eval_problems(with_item(evalset, "EV-003", acceptable_evidence=overdue_ref), docs)
    assert any("EV-003" in p and "only an overdue item" in p for p in problems)


def test_an_overdue_item_without_the_required_reason_is_caught(
    evalset: EvalSet, docs: dict[str, ParsedDocument]
) -> None:  # [NEG]
    problems = eval_problems(with_item(evalset, "EV-022", expected_reasons=[]), docs)
    assert any("EV-022" in p and "PARTIAL with review_overdue" in p for p in problems)


def test_an_identifier_question_whose_identifier_is_written_in_the_vault_is_caught(
    evalset: EvalSet, docs: dict[str, ParsedDocument]
) -> None:  # [NEG] "AC-2" appears in a hostile note, so a blank label for it would be false
    problems = eval_problems(with_item(evalset, "EV-018", question="AC-2"), docs)
    assert any("EV-018" in p and "AC-2 is written in ADV-POL-004" in p for p in problems)


def test_an_identifier_item_that_is_not_an_identifier_is_caught(
    evalset: EvalSet, docs: dict[str, ParsedDocument]
) -> None:  # [NEG]
    problems = eval_problems(with_item(evalset, "EV-018", question="encryption"), docs)
    assert any("EV-018" in p and "must be a control identifier" in p for p in problems)


def test_an_over_long_question_is_caught(evalset: EvalSet, docs: dict[str, ParsedDocument]) -> None:
    problems = eval_problems(with_item(evalset, "EV-011", question="Q" * 1001), docs)
    assert any("EV-011" in p and "longer than" in p for p in problems)  # [NEG]


def test_a_malformed_eval_file_is_refused_not_silently_accepted(tmp_path: Path) -> None:
    broken = tmp_path / "eval.json"
    original = EVAL_SET.read_text(encoding="utf-8")
    text = original.replace('"expected": "answer"', '"expected": "maybe"', 1)
    assert text != original
    broken.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="expected"):  # [NEG] pydantic's ValidationError
        load_eval_set(broken)


def test_an_unknown_field_in_an_item_is_refused(tmp_path: Path) -> None:
    broken = tmp_path / "eval.json"
    original = EVAL_SET.read_text(encoding="utf-8")
    text = original.replace('"id": "EV-001",', '"id": "EV-001", "x": 1,', 1)
    assert text != original
    broken.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="x"):  # [NEG]
        load_eval_set(broken)
