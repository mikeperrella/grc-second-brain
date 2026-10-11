"""The M5 evaluation set: strict loading and a checker that proves the labels point at real text.

The set (`fixtures/eval/eval-set.json`) is committed before any prompt code exists. Every label is
only as good as its evidence reference, so `eval_problems` checks that each referenced line range
exists, lies in the body (not the frontmatter), and really contains the phrase the label claims.
The same loader is used by the live answer test (M5f), so a malformed file fails there too.
"""

from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from grc_sidecar.retrieval.identifiers import identify
from grc_sidecar.vault.clock import is_stale
from grc_sidecar.vault.models import ParsedDocument
from grc_sidecar.vault.scan import scan_vault

Split = Literal["dev", "heldout"]
Category = Literal[
    "paraphrase",
    "exact_value",
    "absent",
    "framework_bait",
    "identifier_only",
    "conflict",
    "overdue",
    "near_miss",
    "injection",
]
Status = Literal["SUPPORTED", "PARTIAL", "NEEDS_REVIEW"]

CATEGORIES: tuple[Category, ...] = (
    "paraphrase",
    "exact_value",
    "absent",
    "framework_bait",
    "identifier_only",
    "conflict",
    "overdue",
    "near_miss",
    "injection",
)
MAX_QUESTION_CHARS = 1000  # the cap the prompt builder will apply to a questionnaire cell (M5d)


class EvidenceRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    phrase: str = Field(min_length=1)


class EvalItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    split: Split
    category: Category
    question: str = Field(min_length=1)
    expected: Literal["answer", "blank"]
    acceptable_statuses: list[Status] = Field(min_length=1)
    expected_reasons: list[str] = Field(default_factory=list)
    acceptable_evidence: list[EvidenceRef]


class EvalSet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    synthetic: Literal[True]
    description: str
    clock_today: date
    vault_roots: list[str] = Field(min_length=1)
    packs_indexed: bool
    fields: dict[str, str]
    items: list[EvalItem]


def load_eval_set(path: Path) -> EvalSet:
    return EvalSet.model_validate_json(path.read_text(encoding="utf-8"))


def load_eval_documents(repo_root: Path, roots: Sequence[str]) -> dict[str, ParsedDocument]:
    """Every document of every vault root, by doc_id. A doc_id in two roots is an error."""
    docs: dict[str, ParsedDocument] = {}
    for root in roots:
        result = scan_vault(repo_root / root)
        if not result.ok or result.issues:
            raise AssertionError(f"{root} does not parse cleanly: {result.issues}")
        for doc in result.documents:
            if doc.frontmatter.doc_id in docs:
                raise AssertionError(f"doc_id {doc.frontmatter.doc_id} appears in two roots")
            docs[doc.frontmatter.doc_id] = doc
    return docs


def normalize(text: str) -> str:
    """Whitespace normalization only (the same rule the grounding check will use, ADR-013)."""
    return " ".join(text.split())


def _body_text(doc: ParsedDocument) -> str:
    return "\n".join(doc.lines[doc.body_start_line - 1 :])


def _evidence_problems(item: EvalItem, docs: Mapping[str, ParsedDocument]) -> list[str]:
    problems: list[str] = []
    for ref in item.acceptable_evidence:
        doc = docs.get(ref.doc_id)
        if doc is None:
            problems.append(f"{item.id}: evidence names unknown doc_id {ref.doc_id}")
            continue
        if ref.start_line > ref.end_line or ref.end_line > len(doc.lines):
            problems.append(
                f"{item.id}: lines {ref.start_line}-{ref.end_line} are outside {ref.doc_id}"
                f" ({len(doc.lines)} lines)"
            )
            continue
        if ref.start_line < doc.body_start_line:
            problems.append(
                f"{item.id}: lines {ref.start_line}-{ref.end_line} start in the frontmatter"
            )
            continue
        text = normalize(" ".join(doc.lines[ref.start_line - 1 : ref.end_line]))
        if normalize(ref.phrase) not in text:
            problems.append(f"{item.id}: phrase not found in {ref.doc_id}:{ref.start_line}")
    return problems


def eval_problems(evalset: EvalSet, docs: Mapping[str, ParsedDocument]) -> list[str]:
    """Everything wrong with the labels, as readable lines. An empty list means the set is sound."""
    problems: list[str] = []
    ids = [item.id for item in evalset.items]
    for duplicate in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(f"duplicate id {duplicate}")
    questions: dict[str, Split] = {}
    for item in evalset.items:
        prefix = "EV-0" if item.split == "dev" else "EV-1"
        if not item.id.startswith(prefix):
            problems.append(f"{item.id}: id does not match split {item.split}")
        key = normalize(item.question).casefold()
        if key in questions and questions[key] != item.split:
            problems.append(f"{item.id}: question also appears in the other split")
        elif key in questions:
            problems.append(f"{item.id}: duplicate question")
        questions[key] = item.split
        if len(item.question) > MAX_QUESTION_CHARS:
            problems.append(f"{item.id}: question longer than {MAX_QUESTION_CHARS} characters")
        problems.extend(_consistency_problems(item, docs, evalset.clock_today))
        problems.extend(_evidence_problems(item, docs))
        if item.category == "identifier_only":
            problems.extend(_identifier_problems(item, docs))
    for split in ("dev", "heldout"):
        present = {i.category for i in evalset.items if i.split == split}
        for category in CATEGORIES:
            if category not in present:
                problems.append(f"split {split} has no {category} item")
    return problems


def _consistency_problems(
    item: EvalItem, docs: Mapping[str, ParsedDocument], today: date
) -> list[str]:
    problems: list[str] = []
    if item.expected == "blank":
        if item.acceptable_statuses != ["NEEDS_REVIEW"]:
            problems.append(f"{item.id}: a blank item must accept only NEEDS_REVIEW")
        if item.acceptable_evidence or item.expected_reasons:
            problems.append(f"{item.id}: a blank item has no evidence and no required reasons")
        return problems
    if "NEEDS_REVIEW" in item.acceptable_statuses:
        problems.append(f"{item.id}: an answer item cannot accept NEEDS_REVIEW")
    if not item.acceptable_evidence:
        problems.append(f"{item.id}: an answer item needs acceptable evidence")
    cited = [docs[r.doc_id] for r in item.acceptable_evidence if r.doc_id in docs]
    overdue = [is_stale(d.frontmatter.review_by, today) for d in cited]
    if item.category == "overdue":
        if not overdue or not all(overdue):
            problems.append(f"{item.id}: an overdue item must cite only overdue documents")
        if item.acceptable_statuses != ["PARTIAL"] or item.expected_reasons != ["review_overdue"]:
            problems.append(f"{item.id}: an overdue item expects PARTIAL with review_overdue")
    else:
        if any(overdue):
            problems.append(f"{item.id}: only an overdue item may cite an overdue document")
        if item.expected_reasons:
            problems.append(f"{item.id}: required reasons are only used by overdue items")
    return problems


def _identifier_problems(item: EvalItem, docs: Mapping[str, ParsedDocument]) -> list[str]:
    """An identifier-only item is labeled blank because no honest answer exists, so no vault
    body may contain any written form of that identifier (a plain substring check: stricter than
    the whole-identifier match the answer service uses, so it can only over-report)."""
    identifier = identify(item.question.strip())
    if identifier is None:
        return [f"{item.id}: an identifier-only question must be a control identifier"]
    problems: list[str] = []
    for doc in docs.values():
        body = _body_text(doc).casefold()
        for form in identifier.forms:
            if form.casefold() in body:
                problems.append(f"{item.id}: {form} is written in {doc.frontmatter.doc_id}")
    return problems
