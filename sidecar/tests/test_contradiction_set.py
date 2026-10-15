"""Integrity of the contradiction evaluation set and its vault (ADR-019 decision 12).

Not the freeze: the hashes are pinned in chunk 4c. This only proves that every label points at real
text, that every note is labelled synthetic, and that the categories the ADR lists are covered."""

from collections import Counter

from .contradictionkit import Place, load_set, norm
from .vaultkit import CONTRADICTIONS_VAULT

REQUIRED_CATEGORIES = {
    "value_mismatch_days",
    "value_mismatch_years",
    "value_mismatch_hours",
    "value_mismatch_percent",
    "scope_difference",
    "same_number_different_subject",
    "days_vs_months",
    "number_words",
    "frequency",
    "table_row",
    "same_document",
    "framework_against_policy",
    "injection_report_no_conflicts",
    "injection_report_a_conflict",
    "unrelated",
}


def lines_of(where: Place) -> str:
    text = (CONTRADICTIONS_VAULT / where.path).read_text(encoding="utf-8")
    return " ".join(text.split("\n")[where.start_line - 1 : where.end_line])


def test_every_phrase_is_on_the_lines_the_set_names() -> None:
    for item in load_set().items:
        for where in (item.chunk_a, item.chunk_b):
            assert norm(where.phrase) in norm(lines_of(where)), (item.id, where.path)


def test_ids_are_unique_and_the_splits_are_the_planned_size() -> None:
    items = load_set().items
    ids = [i.id for i in items]
    assert len(ids) == len(set(ids))
    assert Counter(i.split for i in items) == {"dev": 16, "heldout": 17}


def test_the_categories_of_the_adr_are_covered() -> None:
    assert {i.category for i in load_set().items} == REQUIRED_CATEGORIES
    unrelated = [i for i in load_set().items if i.category == "unrelated"]
    assert len(unrelated) >= 5


def test_every_note_in_the_fixture_vault_is_labelled_synthetic() -> None:
    notes = sorted(CONTRADICTIONS_VAULT.glob("*.md"))
    assert notes
    for note in notes:
        assert "SYNTHETIC FIXTURE" in note.read_text(encoding="utf-8"), note.name


def test_every_note_is_used_by_an_item() -> None:
    used = {w.path for i in load_set().items for w in (i.chunk_a, i.chunk_b)}
    assert used == {n.name for n in CONTRADICTIONS_VAULT.glob("*.md")}


def test_no_item_expects_a_value_conflict_offline() -> None:
    # The code half alone can never produce one (N5): the offline column says so for every item.
    assert {i.offline_status for i in load_set().items} <= {"CANDIDATE", "NOT_PAIRED", "EXCLUDED"}


def test_a_value_conflict_is_expected_only_for_conflict_labels() -> None:
    for item in load_set().items:
        if item.expected_status == "VALUE_CONFLICT":
            assert item.label == "conflict", item.id
        if item.label == "no_conflict":
            assert item.expected_status != "VALUE_CONFLICT", item.id
