"""The detector's code half end to end (ADR-019, chunk 4a-2): real vaults, a real index with the
fake embedder, and no model. The two known cases, the evaluation set, exclusions, staleness, scope.
"""

import asyncio
from pathlib import Path

import pytest

from grc_sidecar.contradictions.models import Claim, DetectionReport
from grc_sidecar.contradictions.pairs import comparable, differ, overlap_of
from grc_sidecar.contradictions.service import ContradictionDetector
from grc_sidecar.errors import IndexNotBuiltError
from grc_sidecar.repositories.sqlite_vec_repo import SqliteVecRepository

from .contradictionkit import (
    SCENARIO_SECONDS,
    Item,
    Place,
    claims_of,
    find_finding,
    load_set,
    mentions,
    norm,
    run_detector,
)
from .retrievalkit import stack
from .vaultkit import CONTRADICTIONS_VAULT, DEMO_VAULT, REPO_ROOT, doc_text


@pytest.fixture(scope="module")
def demo_report(tmp_path_factory: pytest.TempPathFactory) -> DetectionReport:
    return run_detector(DEMO_VAULT, tmp_path_factory.mktemp("demo-index"))


@pytest.fixture(scope="module")
def set_report(tmp_path_factory: pytest.TempPathFactory) -> DetectionReport:
    return run_detector(CONTRADICTIONS_VAULT, tmp_path_factory.mktemp("set-index"))


def place(path: str, line: int) -> str:
    return f"{path}:{line}"


# ----- the two known cases, found by code alone (ADR-019 decision 13) ----------------------------


@pytest.mark.parametrize("case_id", ["EV-021", "EV-113"])
def test_each_known_case_is_found_offline_as_a_candidate_with_no_model(
    demo_report: DetectionReport, case_id: str
) -> None:
    (case,) = [c for c in load_set().known_cases if c.id == case_id]
    assert case.vault == "vault-demo/vault"
    finding = find_finding(demo_report, case.chunk_a.phrase, case.chunk_b.phrase)
    assert finding is not None, f"{case_id} was not found by code alone"
    assert finding.status == "CANDIDATE"
    assert finding.flags == ("model_not_consulted",)
    assert {finding.source_a, finding.source_b} == {
        place(case.chunk_a.path, case.chunk_a.start_line),
        place(case.chunk_b.path, case.chunk_b.start_line),
    }


def test_the_known_cases_phrases_are_on_the_lines_the_set_names() -> None:
    for case in load_set().known_cases:
        for where in (case.chunk_a, case.chunk_b):
            lines = (DEMO_VAULT / where.path).read_text("utf-8").split("\n")
            assert norm(where.phrase) in norm(
                " ".join(lines[where.start_line - 1 : where.end_line])
            )


def test_offline_no_finding_is_ever_a_value_conflict(
    demo_report: DetectionReport, set_report: DetectionReport
) -> None:  # [NEG] N5
    for report in (demo_report, set_report):
        assert report.findings
        assert {f.status for f in report.findings} == {"CANDIDATE"}
        assert all(f.flags == ("model_not_consulted",) for f in report.findings)


def test_the_near_miss_in_the_demo_vault_is_never_a_value_conflict(
    demo_report: DetectionReport,
) -> None:  # [NEG]
    finding = find_finding(
        demo_report, "| Application logs | 14 days |", "| Security logs | 90 days |"
    )
    assert finding is not None  # listed, because the subjects share "logs"
    assert finding.status == "CANDIDATE"
    assert "weak_subject_overlap" in finding.reasons


# ----- the evaluation set, both splits (ADR-019 decision 15 (a)) ---------------------------------


def judged(item: Item, report: DetectionReport) -> bool:
    found = find_finding(report, item.chunk_a.phrase, item.chunk_b.phrase) is not None
    if item.offline_status == "CANDIDATE":
        return found
    if item.offline_status == "NOT_PAIRED":
        return not found
    return not mentions(report, item.chunk_a.phrase) and not mentions(report, item.chunk_b.phrase)


@pytest.mark.parametrize("split", ["dev", "heldout"])
def test_every_planted_pair_is_a_code_found_pair_and_the_others_are_not(
    set_report: DetectionReport, split: str
) -> None:
    items = [i for i in load_set().items if i.split == split]
    planted = [i for i in items if i.offline_status == "CANDIDATE"]
    found = [i for i in planted if judged(i, set_report)]
    assert len(found) == len(planted), f"{split}: found {len(found)} of {len(planted)}"
    wrong = [i.id for i in items if not judged(i, set_report)]
    assert wrong == [], f"{split}: items not as expected: {wrong}"


def extracted(where: Place, key: str) -> Claim | None:
    """The claim the code extracts from the item's own lines, read from the file (no index)."""
    lines = (CONTRADICTIONS_VAULT / where.path).read_text("utf-8").split("\n")
    text = "\n".join(lines[where.start_line - 1 : where.end_line])
    for claim in claims_of(text, start_line=where.start_line, chunk_id=key):
        if claim.quote == norm(where.phrase):
            return claim
    return None


def test_the_safety_notes_were_read_and_not_excluded_as_non_company(tmp_path: Path) -> None:
    # Vacuity check (a): the "unrelated" items live in notes typed `note`, which must still count
    # as company text. If they were excluded, "not paired" would prove nothing.
    for name in ("safety-notes-a.md", "safety-notes-b.md"):
        report = run_detector(CONTRADICTIONS_VAULT, tmp_path / name, scope=name)
        assert report.chunks_read > 0, name
        assert report.excluded_chunks == 0, name
        assert report.claims_found == 3, name


def test_every_not_paired_and_excluded_item_has_both_claims_extracted_by_code() -> None:
    # Vacuity check (b): non-pairing must come from subject non-overlap, not from a missed
    # extraction. For NOT_PAIRED both claims exist, are comparable with different values, and
    # share no subject word. For EXCLUDED both claims exist too (the exclusion is by kind).
    checked = 0
    for item in load_set().items:
        if item.offline_status == "CANDIDATE":
            continue
        a = extracted(item.chunk_a, item.id + "a")
        b = extracted(item.chunk_b, item.id + "b")
        assert a is not None and b is not None, item.id
        if item.offline_status == "NOT_PAIRED":
            assert comparable(a, b) and differ(a, b), item.id
            assert overlap_of(a, b) is None, item.id
        checked += 1
    assert checked == 7  # 5 unrelated and 2 framework items


def test_the_two_splits_have_the_planned_size() -> None:
    items = load_set().items
    assert sum(i.split == "dev" for i in items) == 16
    assert sum(i.split == "heldout" for i in items) == 17  # 14 plus the owner pairs CD-115 to 117


def test_a_dev_pair_expected_to_end_as_a_value_conflict_is_one_the_ceiling_allows(
    set_report: DetectionReport,
) -> None:
    # Dev only: the ceiling must not forbid what the design wants to confirm. The held-out count is
    # reported in ADR-019 and not asserted (it is not used for tuning).
    for item in (i for i in load_set().items if i.split == "dev"):
        if item.expected_status != "VALUE_CONFLICT":
            continue
        finding = find_finding(set_report, item.chunk_a.phrase, item.chunk_b.phrase)
        assert finding is not None
        assert "weak_subject_overlap" not in finding.reasons, item.id
        assert "days_vs_months" not in finding.reasons, item.id


def test_a_hidden_instruction_in_a_note_changes_no_computed_field(
    set_report: DetectionReport,
) -> None:  # [NEG] 8.6
    finding = find_finding(
        set_report,
        "Vendor invoices are paid within 30 days.",
        "Vendor invoices are paid within 45 days.",
    )
    assert finding is not None
    assert finding.numbers in (("30 days", "45 days"), ("45 days", "30 days"))
    assert finding.status == "CANDIDATE"
    assert all("SYSTEM NOTICE" not in f.quote_a + f.quote_b for f in set_report.findings)


def test_the_report_says_what_it_does_not_check(set_report: DetectionReport) -> None:
    assert "numbers only" in set_report.says
    assert "does not mean the policies agree" in set_report.says


# ----- exclusions, staleness, scope, failures ----------------------------------------------------


def write(folder: Path, name: str, body: str, *frontmatter: str) -> None:
    (folder / name).write_text(doc_text(body, *frontmatter), encoding="utf-8", newline="\n")


def front(doc_id: str, kind: str) -> tuple[str, ...]:
    extra = () if kind == "framework" else ("review_by: 2028-01-01",)
    return (f"doc_id: {doc_id}", f"type: {kind}", "owner: Test Owner", *extra)


def test_framework_text_and_withdrawn_controls_are_never_compared(tmp_path: Path) -> None:  # [NEG]
    vault = tmp_path / "vault"
    vault.mkdir()
    write(vault, "a.md", "# A\n\nSecurity logs are kept for 90 days.\n", *front("T-A", "policy"))
    write(vault, "c.md", "# C\n\nSecurity logs are kept for 180 days.\n", *front("T-C", "policy"))
    write(
        vault, "fw.md", "# FW\n\nSecurity logs are kept for 30 days.\n", *front("T-F", "framework")
    )
    write(
        vault,
        "w.md",
        "# W\n\n## Withdrawn controls\n\nSecurity logs are kept for 365 days.\n",
        *front("T-W", "policy"),
    )
    report = run_detector(vault, tmp_path / "index")
    assert len(report.findings) == 1
    assert {
        report.findings[0].source_a.split(":")[0],
        report.findings[0].source_b.split(":")[0],
    } == {
        "a.md",
        "c.md",
    }
    assert not mentions(report, "30 days") and not mentions(report, "365 days")
    assert report.excluded_chunks >= 2


def test_a_file_that_changed_after_indexing_is_withheld_and_counted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    write(vault, "a.md", "# A\n\nSecurity logs are kept for 90 days.\n", *front("T-A", "policy"))
    write(vault, "c.md", "# C\n\nSecurity logs are kept for 180 days.\n", *front("T-C", "policy"))

    async def scenario() -> DetectionReport:
        async with stack(vault, tmp_path / "index") as s:
            write(
                vault,
                "c.md",
                "# C\n\nSecurity logs are kept for 7 days.\n",
                *front("T-C", "policy"),
            )
            await s.indexer.refresh_staleness()
            return await ContradictionDetector(s.vault_repo).detect()

    report = asyncio.run(asyncio.wait_for(scenario(), SCENARIO_SECONDS))
    assert report.withheld_stale == 1
    assert report.findings == ()  # the stale file is never compared, with its old text or its new


def test_a_scope_limits_the_files_that_are_compared(tmp_path: Path) -> None:
    whole = run_detector(CONTRADICTIONS_VAULT, tmp_path / "i1")
    scoped = run_detector(CONTRADICTIONS_VAULT, tmp_path / "i2", scope="records-schedule-a.md")
    assert 0 < len(scoped.findings) < len(whole.findings)
    assert all(
        f.source_a.startswith("records-schedule-a.md")
        and f.source_b.startswith("records-schedule-a.md")
        for f in scoped.findings
    )


def test_no_index_is_a_typed_error_and_creates_nothing(tmp_path: Path) -> None:  # [NEG]
    repo = SqliteVecRepository(tmp_path / "missing.sqlite3")

    async def scenario() -> None:
        await ContradictionDetector(repo).detect()

    with pytest.raises(IndexNotBuiltError):
        asyncio.run(asyncio.wait_for(scenario(), SCENARIO_SECONDS))
    assert not (tmp_path / "missing.sqlite3").exists()


def test_the_cap_applies_to_a_real_run_and_a_bad_cap_is_refused(tmp_path: Path) -> None:
    report = run_detector(CONTRADICTIONS_VAULT, tmp_path / "i", cap=3)
    assert len(report.findings) == 3
    assert report.dropped["over_cap"] > 0
    with pytest.raises(ValueError, match="cap"):
        run_detector(CONTRADICTIONS_VAULT, tmp_path / "i2", cap=0)


def test_the_fixture_paths_are_inside_the_repository() -> None:
    assert CONTRADICTIONS_VAULT.is_relative_to(REPO_ROOT)
