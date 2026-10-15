"""The live-check harness of chunk 4c, run offline with a scripted model (ADR-019, decision 16).

The live test (`test_contradiction_live.py`, marker `ollama`) needs a real Ollama and is run by
the owner. Everything it does besides calling the model is in `contradictionlivekit.py`, and this
file runs that code here with the scripted `FakeModel`, so a mistake in the per-item scoring or the
report is found offline and not on the day the models are on. No test here reaches port 11434.
"""

import asyncio
import hashlib
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path

import pytest

from grc_sidecar.contradictions.models import ChunkText, Pair
from grc_sidecar.contradictions.verdict import ModelAsker, PairAsker, VerdictOutcome

from .contradictionkit import load_set, norm
from .contradictionlivekit import (
    Case,
    Record,
    copy_notes,
    format_report,
    format_row,
    item_cases,
    known_cases,
    results_path,
    run_cases,
    write_results,
)
from .contradictionmodelkit import asker_for, focus_of, repeat, reply
from .servicekit import FakeModel
from .vaultkit import CONTRADICTIONS_VAULT

LIMIT_SECONDS = 120  # a whole split, one tiny index per item


def note_hashes() -> dict[str, str]:
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in CONTRADICTIONS_VAULT.glob("*.md")
    }


def keeps() -> ModelAsker:
    return asker_for(repeat(reply(True, "same_subject_value_mismatch"), 200))[0]


def lowers() -> ModelAsker:
    return asker_for(repeat(reply(False, "scope_difference"), 200))[0]


def score(cases: list[Case], make: Callable[[], PairAsker], tmp: Path) -> list[Record]:
    return asyncio.run(asyncio.wait_for(run_cases(cases, make, tmp), LIMIT_SECONDS))


@pytest.fixture(scope="module")
def keeps_dev(tmp_path_factory: pytest.TempPathFactory) -> list[Record]:
    return score(item_cases("dev"), keeps, tmp_path_factory.mktemp("dev"))


@pytest.fixture(scope="module")
def keeps_heldout(tmp_path_factory: pytest.TempPathFactory) -> list[Record]:
    return score(item_cases("heldout"), keeps, tmp_path_factory.mktemp("heldout"))


def test_the_harness_scores_every_item_of_a_split_in_file_order(
    keeps_dev: list[Record], keeps_heldout: list[Record]
) -> None:
    assert [r.case.id for r in keeps_dev] == [c.id for c in item_cases("dev")]
    assert len(keeps_dev) == 16 and len(keeps_heldout) == 17
    assert [r.case.id for r in keeps_heldout][-3:] == ["CD-115", "CD-116", "CD-117"]


@pytest.mark.parametrize("split", ["dev", "heldout"])
def test_per_item_code_found_matches_the_offline_expectation_and_unpaired_items_cost_no_call(
    split: str, keeps_dev: list[Record], keeps_heldout: list[Record]
) -> None:
    records = keeps_dev if split == "dev" else keeps_heldout
    offline = {i.id: i.offline_status for i in load_set().items}
    for record in records:
        planted = offline[record.case.id] == "CANDIDATE"
        assert record.code_found is planted, record.case.id
        if planted:
            assert record.attempts == 1, record.case.id  # one scripted reply, no retry
            assert record.conflict is True and record.model_status == "available"
        else:  # not paired or excluded: the model is never asked about it
            assert record.attempts == 0 and record.conflict is None and record.status is None


def test_the_model_is_asked_only_about_the_items_own_pair(tmp_path: Path) -> None:  # [NEG]
    """The harness fails this if it asks about more than the item's pair: one request per code-found
    item, none for an item whose pair code did not find, and the request holds the item's own two
    statements (not those of another pair in the copy)."""
    models: list[FakeModel] = []

    def make() -> ModelAsker:
        asker, model = asker_for(repeat(reply(True), 200))
        models.append(model)
        return asker

    cases = item_cases("dev") + known_cases()
    records = score(cases, make, tmp_path)
    assert len(models) == len(cases)
    for case, record, model in zip(cases, records, models, strict=True):
        assert len(model.requests) == (1 if record.code_found else 0), case.id
        assert record.attempts == len(model.requests), case.id
        if model.requests:
            s1, s2 = (norm(focus_of(model.requests[0], s)) for s in ("S1", "S2"))
            a, b = norm(case.chunk_a.phrase), norm(case.chunk_b.phrase)  # a phrase may be part
            assert (a in s1 and b in s2) or (b in s1 and a in s2), case.id  # of its sentence
    # The other pairs are counted, not asked: CD-003 has 6 pairs in its copy, 1 is its own.
    by_id = {r.case.id: r for r in records}
    assert by_id["CD-003"].other_pairs == 5
    assert sum(len(m.requests) for m in models) == 14  # 12 dev items and the 2 known cases


def test_an_item_whose_pair_is_not_found_is_recorded_so_and_makes_no_call(
    keeps_dev: list[Record],
) -> None:
    record = next(r for r in keeps_dev if r.case.id == "CD-014")
    assert not record.code_found and record.attempts == 0 and record.status is None
    assert record.model_status == "pair not found"
    assert "pair not found" in format_row(record)


class DroppingAsker:
    """The real scripted asker, but its answer says the prompt held a chunk that is not in the
    detector's texts, so the in-prompt context check drops the pair after the call was made."""

    def __init__(self) -> None:
        self._inner = asker_for(repeat(reply(True), 5))[0]

    async def ask(self, pair: Pair, texts: Mapping[str, ChunkText]) -> VerdictOutcome:
        outcome = await self._inner.ask(pair, texts)
        return replace(outcome, shown_chunk_ids=("a-chunk-that-was-never-read",))


def test_an_asked_pair_that_the_final_report_drops_keeps_its_real_attempts(
    tmp_path: Path,
) -> None:  # [NEG]
    case = next(c for c in item_cases("dev") if c.id == "CD-001")
    (record,) = score([case], DroppingAsker, tmp_path)
    assert record.model_status == "asked, pair dropped"  # not "pair not found": a call was made
    assert record.attempts == 1 and record.status is None
    assert record.code_found and record.conflict is True  # the raw verdict stays on the record
    assert "asked, pair dropped" in format_row(record)
    assert "pair not found" not in format_row(record)


def test_the_raw_verdict_is_recorded_even_when_the_ceiling_caps_the_status(
    keeps_heldout: list[Record],
) -> None:
    by_id = {r.case.id: r for r in keeps_heldout}
    weak = by_id["CD-101"]  # a weak overlap: code caps it at CANDIDATE (ADR-019, C1)
    assert weak.conflict is True and weak.status == "CANDIDATE"
    strong = by_id["CD-117"]  # the owner's value mismatch: a strong overlap
    assert strong.conflict is True and strong.status == "VALUE_CONFLICT"
    for owner_pair in ("CD-115", "CD-116", "CD-117"):
        assert by_id[owner_pair].code_found, owner_pair


def test_a_model_that_lowers_gives_candidate_and_the_raw_verdict_is_still_shown(
    tmp_path: Path,
) -> None:
    cases = [c for c in item_cases("heldout") if c.id in ("CD-115", "CD-117")]
    records = score(cases, lowers, tmp_path)
    for record in records:
        assert record.status == "CANDIDATE", record.case.id
        assert record.conflict is False and record.relation == "scope_difference"


def test_the_known_cases_are_scored_as_items_from_copies_of_the_demo_notes(
    tmp_path: Path,
) -> None:
    cases = known_cases()
    assert [c.id for c in cases] == ["EV-021", "EV-113"]
    records = score(cases, keeps, tmp_path)
    assert all(r.code_found and r.attempts == 1 for r in records)
    work = tmp_path / "case-00-EV-021" / "vault"
    assert sorted(p.name for p in work.glob("*.md")) == [
        "data-retention-policy.md",
        "incident-response-plan.md",
    ]


def test_each_item_gets_a_copy_of_only_its_own_notes_and_the_original_is_untouched(
    tmp_path: Path,
) -> None:  # [NEG]
    before = note_hashes()
    case = next(c for c in item_cases("heldout") if c.id == "CD-117")
    copy_notes(case, tmp_path / "vault")
    assert sorted(p.name for p in (tmp_path / "vault").glob("*")) == [
        "housekeeping-notes-a.md",
        "housekeeping-notes-b.md",
    ]
    assert before == note_hashes()


def test_a_note_name_that_is_a_path_is_refused(tmp_path: Path) -> None:  # [NEG]
    case = item_cases("dev")[0]
    hostile = Case(
        case.id,
        case.category,
        case.label,
        case.expected_status,
        case.vault_root,
        case.chunk_a.model_copy(update={"path": "../eval/eval-set.json"}),
        case.chunk_b,
    )
    with pytest.raises(ValueError):
        copy_notes(hostile, tmp_path / "vault")


def test_the_report_has_codes_and_counts_only(keeps_heldout: list[Record], tmp_path: Path) -> None:
    known = score(known_cases(), keeps, tmp_path)
    report = format_report(keeps_heldout, known, header="=== header")
    lines = report.splitlines()
    assert sum(1 for line in lines if line.startswith(("CD-", "EV-"))) == 17 + 2
    assert "%" not in report  # counts, never percentages
    flat = norm(report)
    for item in load_set().items:
        for phrase in (item.chunk_a.phrase, item.chunk_b.phrase):
            assert norm(phrase) not in flat, item.id
    assert "The two statements set different values." not in report  # no model text
    assert "COUNTS BY CATEGORY" in report and "KNOWN CASES" in report
    assert "wrongly kept" in report and "raw verdicts" in report


def test_row_lines_name_the_status_and_the_verdict_codes(keeps_heldout: list[Record]) -> None:
    row = format_row(next(r for r in keeps_heldout if r.case.id == "CD-117"))
    assert "VALUE_CONFLICT" in row and "conflict=true relation=same_subject_value_mismatch" in row
    assert "available" in row


def test_results_go_to_an_existing_folder_under_a_plain_name(tmp_path: Path) -> None:  # [NEG]
    assert results_path(str(tmp_path), "dev-run.txt") == tmp_path.resolve() / "dev-run.txt"
    for bad in ("../x.txt", "a/b.txt", "a\\b.txt", "x.md", "", ".txt", "C:evil.txt"):
        with pytest.raises(ValueError):
            results_path(str(tmp_path), bad)
    with pytest.raises(ValueError):
        results_path(str(tmp_path / "no-such-folder"), "run.txt")
    with pytest.raises(ValueError):
        results_path("", "run.txt")


def test_a_results_file_is_never_overwritten(tmp_path: Path) -> None:  # [NEG]
    path = results_path(str(tmp_path), "heldout-1.txt")
    write_results(path, "first run\n")
    with pytest.raises(FileExistsError):
        write_results(path, "second run\n")
    assert path.read_text(encoding="utf-8") == "first run\n"
