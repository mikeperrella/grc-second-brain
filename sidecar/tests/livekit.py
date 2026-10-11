"""The table and the checks of the live retrieval run (tests only).

Kept apart from `test_retrieval_live.py` so that an ordinary offline test can exercise exactly this
code with the fake embedder (`test_retrieval_golden.py::test_the_live_check_helpers_...`). The live
test then only has to connect real models.
"""

from pathlib import Path

from grc_sidecar.retrieval.models import RetrievalResult, RetrievedChunk

from .retrieval_expectations import (
    DOC_FILES,
    IDENTIFIER_GROUPS,
    OVERDUE,
    QUESTIONS,
    IdentifierGroup,
)

# The eight questions of the live check, in the order they are printed.
LIVE_QUERIES = (
    "multi-factor authentication",
    "log retention",
    "3.1.1",
    "03.01.01",
    "A.5.15",
    "CC6.1",
    "AC-2",
    "vendor review",
)

HEADER = (
    f"{'#':>2}  {'source_kind':<19} {'source_path':<44} {'lines':<9} "
    f"{'score':>8}  {'mode':<12} {'overdue':<7} {'role':<8} {'wd':<2} {'ranks (lex/vec)':<15}"
)


def format_chunk(index: int, chunk: RetrievedChunk, mode: str) -> str:
    lines = f"{chunk.start_line}-{chunk.end_line}"
    ranks = f"{chunk.lexical_rank or '-'}/{chunk.vector_rank or '-'}"
    return (
        f"{index:>2}  {chunk.source_kind:<19} {chunk.source_path:<44.44} {lines:<9} "
        f"{chunk.fused_score:>8.5f}  {mode:<12} {chunk.review_overdue!s:<7} "
        f"{chunk.role:<8} {'W' if chunk.withdrawn else '':<2} {ranks:<15}"
    )


def format_result(question: str, result: RetrievalResult) -> str:
    """One question's ranked results as a text table, with the facts a reader needs under it."""
    out = [f"\n=== {question!r}  mode={result.mode}  degraded_reason={result.degraded_reason}"]
    out.append(HEADER)
    out.extend(format_chunk(i, c, result.mode) for i, c in enumerate(result.chunks, start=1))
    company = sum(1 for c in result.chunks if c.source_kind == "company_policy")
    framework = len(result.chunks) - company
    expanded = sum(1 for c in result.chunks if c.role == "expanded")
    out.append(
        f"    company_policy={company} framework_reference={framework} expanded={expanded} "
        f"used_chars={result.budget.used_chars}/{result.budget.char_budget} "
        f"withheld_unindexed={list(result.withheld_unindexed)}"
    )
    return "\n".join(out)


def _first_framework(result: RetrievalResult) -> RetrievedChunk | None:
    return next((c for c in result.chunks if c.source_kind == "framework_reference"), None)


def _is_control(chunk: RetrievedChunk, group: IdentifierGroup) -> bool:
    return Path(chunk.source_path).name == group.file_name and (
        group.marker in chunk.heading_path or group.marker in chunk.text
    )


def problems_in(results: dict[str, RetrievalResult], *, expect_mode: str) -> list[str]:
    """Everything that does not match the shared expectations, as readable lines (empty = fine).

    `expect_mode` is "hybrid" (models running) or "lexical_only" (Ollama stopped). In keyword-only
    mode a paraphrase such as "log retention" legitimately misses a document that says "security
    logs are retained", so only the mode, the missing vector ranks and the identifier controls are
    required there; the shared question table is a hybrid expectation.
    """
    found: list[str] = []
    for question, result in results.items():
        if result.mode != expect_mode:
            found.append(f"{question!r}: mode is {result.mode}, expected {expect_mode}")
        if expect_mode == "lexical_only":
            if result.degraded_reason is None:
                found.append(f"{question!r}: lexical_only without a degraded_reason")
            if any(c.vector_rank is not None for c in result.chunks):
                found.append(f"{question!r}: a vector rank in keyword-only mode")
    if expect_mode == "hybrid":
        for question, doc_ids in QUESTIONS.items():
            if question not in results:
                continue
            paths = {c.source_path for c in results[question].chunks}
            for doc_id in doc_ids:
                if DOC_FILES[doc_id] not in paths:
                    found.append(f"{question!r}: {DOC_FILES[doc_id]} ({doc_id}) was not returned")
        vendor = results.get("vendor review")
        if vendor is not None:
            for doc_id, overdue in OVERDUE.items():
                flags = [c.review_overdue for c in vendor.chunks if c.doc_id == doc_id]
                if flags and any(flag is not overdue for flag in flags):
                    found.append(f"vendor review: {doc_id} review_overdue is not {overdue}")
    heads: dict[str, tuple[str, str]] = {}
    for group in IDENTIFIER_GROUPS:
        for form in group.forms:
            if form not in results:
                continue
            first = _first_framework(results[form])
            if first is None or not _is_control(first, group):
                found.append(
                    f"{form!r}: the first framework result is not the {group.label} control"
                )
            else:
                heads[form] = (first.source_path, first.heading_path)
        group_heads = {heads[f] for f in group.forms if f in heads}
        if len(group_heads) > 1:
            found.append(f"{group.label}: its written forms led to different controls")
    return found
