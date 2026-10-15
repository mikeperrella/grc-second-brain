"""Per-item scoring for live check C (ADR-019 decision 16), tests only.

Kept apart from `test_contradiction_live.py` so that an ordinary offline test
(`test_contradiction_live_harness.py`) runs exactly this code with a scripted asker, and the live
test only has to connect a real Ollama. Nothing here makes a model call by itself: the asker is
passed in.

**Per-item scoring is a choice made here.** A whole-vault run would cut pairs at the pair cap, and
it would measure retrieval as much as the model. So each evaluation item gets its own detector over
a temporary copy of ONLY the notes that item uses, with a cap too high to cut anything. The model
is therefore measured on pairs the code found; it says nothing about whether code would find them
in a large vault (that is the offline count of decision 15 (a)).

**The model is asked about the item's own pair ONLY** (the pair whose two quotes hold the item's
two phrases). The other pairs the code finds in the copy are counted (`other_pairs`) and never
sent to the model. If the item's own pair is not a code-found pair, the record says
"pair not found" and no call is made.

What is recorded for each item: whether code found the pair (yes/no), the model's RAW verdict
(`conflict`, `relation`) for that pair, the final status, the `model_status` for that pair and
the number of model replies (attempts). No quote, no vault text and no model text is ever
written: codes and counts only. The results are counts, never percentages, and no target number
exists.
"""

import re
import shutil
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from grc_sidecar.contradictions.models import ChunkText, ModelUse, Pair
from grc_sidecar.contradictions.service import ContradictionDetector, _model_use
from grc_sidecar.contradictions.verdict import PairAsker, VerdictOutcome
from grc_sidecar.repositories.sqlite_vec_repo import SqliteVecRepository
from grc_sidecar.vault.indexer import Indexer

from .contradictionkit import Item, Place, find_finding, load_set, norm
from .fakes import FakeEmbeddingClient
from .vaultkit import CONTRADICTIONS_VAULT, REPO_ROOT

# Larger than any two notes can make, so the pair of an item is never cut. (The route allows 100;
# the detector itself takes any positive cap.)
NEVER_CUT_CAP = 1000
KNOWN_CATEGORY = "known_case"  # EV-021 and EV-113, scored apart from both splits
NOT_FOUND = "pair not found"  # the item's own pair is not a code-found pair: no model call
DROPPED = "asked, pair dropped"  # the model was asked, but the final report has no such finding
RESULT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.txt")


@dataclass(frozen=True)
class Case:
    """One thing to score: two statements in notes, and the label the set gives them."""

    id: str
    category: str
    label: str  # conflict | no_conflict | "-" (known cases carry no label in the set)
    expected_status: str  # the set's expected_status, or "-"
    vault_root: Path
    chunk_a: Place
    chunk_b: Place


@dataclass(frozen=True)
class Record:
    case: Case
    code_found: bool
    conflict: bool | None  # the model's raw verdict for this pair, None = no valid verdict
    relation: str | None
    status: str | None  # VALUE_CONFLICT or CANDIDATE; None when code did not find the pair
    model_status: str  # the fixed word for how the model fared on this pair, or "pair not found"
    attempts: int  # replies received for this pair (0 when it was not asked)
    other_pairs: int  # other code-found pairs in the copy: counted, never sent to the model


def item_cases(split: str) -> list[Case]:
    """The labelled items of one split, in file order (the held-out run order is the file order)."""
    items: list[Item] = [i for i in load_set().items if i.split == split]
    return [
        Case(
            i.id,
            i.category,
            i.label,
            i.expected_status,
            CONTRADICTIONS_VAULT,
            i.chunk_a,
            i.chunk_b,
        )
        for i in items
    ]


def known_cases() -> list[Case]:
    """EV-021 and EV-113, with the demo-vault notes they live in (copied per case, never edited)."""
    return [
        Case(k.id, KNOWN_CATEGORY, "-", "-", REPO_ROOT / k.vault, k.chunk_a, k.chunk_b)
        for k in load_set().known_cases
    ]


class OwnPairAsker:
    """Asks the real asker about the item's OWN pair only: the pair whose two quotes hold the
    item's two phrases (in either order). Every other pair the code finds in the copy gets a
    "not asked" answer without any call, and is counted in `not_asked`."""

    def __init__(self, inner: PairAsker, phrase_a: str, phrase_b: str) -> None:
        self._inner = inner
        self._a = norm(phrase_a)
        self._b = norm(phrase_b)
        self.own: VerdictOutcome | None = None
        self.not_asked = 0

    def _is_own(self, pair: Pair) -> bool:
        a, b = pair.a.quote, pair.b.quote
        return (self._a in a and self._b in b) or (self._b in a and self._a in b)

    async def ask(self, pair: Pair, texts: Mapping[str, ChunkText]) -> VerdictOutcome:
        if self.own is None and self._is_own(pair):
            self.own = await self._inner.ask(pair, texts)
            return self.own
        self.not_asked += 1
        return VerdictOutcome(None, False, (), None, 0)  # no call is made


def model_status_of(outcome: VerdictOutcome) -> ModelUse:
    """The word a run holding only this pair would report (the detector's own rule)."""
    return _model_use(outcome.failure, 1, 0 if outcome.verdict is None else 1)


def copy_notes(case: Case, vault: Path) -> None:
    """Only the notes this case uses, byte for byte, into `vault` (which must be empty)."""
    vault.mkdir(parents=True, exist_ok=True)
    for name in sorted({case.chunk_a.path, case.chunk_b.path}):
        source = case.vault_root / name
        if not source.is_file() or Path(name).name != name:
            raise ValueError(f"{case.id}: not a plain note name in the vault: {name}")
        shutil.copyfile(source, vault / name)


async def score_case(case: Case, make_asker: Callable[[], PairAsker], work: Path) -> Record:
    """Index the case's notes (the fake embedder: the detector reads chunks and keywords only),
    run the detector with the cap that cuts nothing, and read off this case's own pair."""
    vault = work / "vault"
    index = work / "index"
    copy_notes(case, vault)
    index.mkdir(parents=True, exist_ok=True)
    repo = SqliteVecRepository(index / "vault.sqlite3")
    try:
        await Indexer(repo, FakeEmbeddingClient(), vault).sync()
        await repo.close()
        repo = SqliteVecRepository(index / "vault.sqlite3")  # reopened like a fresh process
        own_only = OwnPairAsker(make_asker(), case.chunk_a.phrase, case.chunk_b.phrase)
        report = await ContradictionDetector(repo, own_only).detect(cap=NEVER_CUT_CAP)
    finally:
        await repo.close()
    finding = find_finding(report, case.chunk_a.phrase, case.chunk_b.phrase)
    outcome = own_only.own
    if outcome is None:  # the pair was never asked about: not a code-found pair, no call was made
        return Record(case, False, None, None, None, NOT_FOUND, 0, len(report.findings))
    verdict = outcome.verdict
    # Asked, but the final report has no finding for it (for example the in-prompt context check
    # dropped it): the call was made, so its real attempts and raw verdict stay on the record.
    return Record(
        case=case,
        code_found=True,
        conflict=None if verdict is None else verdict.conflict,
        relation=None if verdict is None else verdict.relation,
        status=None if finding is None else finding.status,
        model_status=DROPPED if finding is None else model_status_of(outcome),
        attempts=outcome.replies,
        other_pairs=own_only.not_asked,
    )


ROW_HEADER = (
    f"{'id':<7} {'category':<30} {'label':<11} {'expected':<15} {'code_found':<10} "
    f"{'raw_verdict':<46} {'status':<15} {'model_status':<19} {'attempts':>8} "
    f"{'other_pairs':>11}"
)


def verdict_code(record: Record) -> str:
    if not record.code_found:
        return NOT_FOUND
    if record.conflict is None:
        return "none"
    return f"conflict={str(record.conflict).lower()} relation={record.relation}"


def format_row(record: Record) -> str:
    case = record.case
    return (
        f"{case.id:<7} {case.category:<30} {case.label:<11} {case.expected_status:<15} "
        f"{'yes' if record.code_found else 'no':<10} {verdict_code(record):<46} "
        f"{record.status or '-':<15} {record.model_status:<19} {record.attempts:>8} "
        f"{record.other_pairs:>11}"
    )


async def run_cases(
    cases: Sequence[Case],
    make_asker: Callable[[], PairAsker],
    work_root: Path,
    *,
    on_record: Callable[[Record], None] | None = None,
) -> list[Record]:
    """Score every case in order, each in its own folder under `work_root`."""
    records: list[Record] = []
    for number, case in enumerate(cases):
        record = await score_case(case, make_asker, work_root / f"case-{number:02d}-{case.id}")
        records.append(record)
        if on_record is not None:
            on_record(record)
    return records


def category_counts(records: Sequence[Record]) -> list[str]:
    """Counts by category, in first-seen (file) order. Codes and counts only."""
    out: list[str] = []
    for category in dict.fromkeys(r.case.category for r in records):
        group = [r for r in records if r.case.category == category]
        status = Counter(r.status for r in group)
        out.append(
            f"  {category:<30} items={len(group)} code_found={sum(r.code_found for r in group)} "
            f"VALUE_CONFLICT={status['VALUE_CONFLICT']} CANDIDATE={status['CANDIDATE']} "
            f"conflict_true={sum(r.conflict is True for r in group)} "
            f"conflict_false={sum(r.conflict is False for r in group)} "
            f"no_verdict={sum(r.code_found and r.conflict is None for r in group)}"
        )
    return out


def format_report(
    records: Sequence[Record], known: Sequence[Record] = (), *, header: str = ""
) -> str:
    """The whole run as text: one line per item, then counts. No percentages, no pass or fail."""
    out = [header] if header else []
    out.append(ROW_HEADER)
    out.extend(format_row(r) for r in records)
    out.append("")
    out.append("COUNTS BY CATEGORY (statuses and raw verdicts are reported separately)")
    out.extend(category_counts(records))
    conflicts = [r for r in records if r.case.expected_status == "VALUE_CONFLICT"]
    kept = sum(r.status == "VALUE_CONFLICT" for r in conflicts)
    out.append(f"expected VALUE_CONFLICT items ending VALUE_CONFLICT: {kept} of {len(conflicts)}")
    wrong = [r for r in records if r.case.label == "no_conflict" and r.status == "VALUE_CONFLICT"]
    unconf = [r for r in records if r.case.label == "no_conflict"]
    out.append(
        f"no_conflict items ending VALUE_CONFLICT (wrongly kept): {len(wrong)} of {len(unconf)}"
        + (f" ({', '.join(r.case.id for r in wrong)})" if wrong else "")
    )
    found = sum(r.code_found for r in records)
    planted = [r for r in records if r.case.expected_status in ("VALUE_CONFLICT", "CANDIDATE")]
    out.append(
        f"planted pairs found by code in this per-item setup: "
        f"{sum(r.code_found for r in planted)} of {len(planted)}; items with a pair: {found}"
    )
    verdicts = Counter(verdict_code(r) for r in records if r.code_found)
    out.append(
        "raw verdicts (code-found items): "
        + (", ".join(f"[{k}]={v}" for k, v in sorted(verdicts.items())) or "none")
    )
    asked = [r for r in records if r.code_found]
    out.append(
        f"model calls: one pair per item, {len(asked)} items asked, "
        f"{sum(r.attempts for r in asked)} replies in all; "
        f"other code-found pairs in the copies, counted and NOT sent: "
        f"{sum(r.other_pairs for r in records)}"
    )
    model = Counter(r.model_status for r in records)
    out.append("model_status: " + ", ".join(f"{k}={v}" for k, v in sorted(model.items())))
    attempts = Counter(r.attempts for r in records if r.code_found)
    out.append(
        "attempts per code-found item: "
        + (", ".join(f"{k}={v}" for k, v in sorted(attempts.items())) or "none")
    )
    if known:
        out.append("")
        out.append("KNOWN CASES (scored apart from the split; demo-vault notes copied per case)")
        out.append(ROW_HEADER)
        out.extend(format_row(r) for r in known)
    return "\n".join(out) + "\n"


def results_path(folder: str, name: str) -> Path:
    """Where this run's results go: an existing folder the owner named, and a plain `.txt` name.
    The name is checked so it cannot be a path; the file itself is created exclusively later."""
    base = Path(folder)
    if not folder or not base.is_dir():
        raise ValueError("the results folder must be an existing folder")
    if not RESULT_NAME.fullmatch(name):
        raise ValueError("the results file name must be a plain name ending in .txt")
    return base.resolve(strict=True) / name


def write_results(path: Path, text: str) -> None:
    """Exclusive create: an existing file is never overwritten, so no run can replace another."""
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
