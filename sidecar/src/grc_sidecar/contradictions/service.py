"""The detector: read the index, find claims, make pairs, verify, ask the model, set the status.

The code half (claims, pairs, verification, the cap) never needs a model. With no model (`asker` is
None, or it is unavailable) every finding that survives verification is a `CANDIDATE` flagged
`model_not_consulted`, and none is ever `VALUE_CONFLICT`. With a model, each kept pair gets one
question (`verdict.py`) and the status is computed by `status.py`: the verdict can keep a finding at
the ceiling the code facts set, or lower it, and nothing it says can add a pair or change a fact.

Reading goes through the existing `VectorRepository` and nothing else: the chunks come from
`chunks_for`, their kind from the `documents` row (`source_kind_of`, the rule retrieval uses), and
each claim's lexical neighbours from the same FTS5 search retrieval uses. Framework text and
withdrawn controls are never read into the comparison. A stale document has no rows in the index,
so a file that changed after indexing is never compared; the report counts those documents.
"""

import logging
from collections.abc import Mapping, Sequence

from grc_sidecar.contradictions.claims import extract_claims
from grc_sidecar.contradictions.models import (
    NUMERIC_ONLY_STATEMENT,
    CandidateReason,
    ChunkText,
    Claim,
    DetectionReport,
    Finding,
    FindingStatus,
    Flag,
    ModelUse,
    ModelVerdictView,
    Pair,
)
from grc_sidecar.contradictions.pairs import (
    DEFAULT_PAIR_CAP,
    NEIGHBOURS_PER_CLAIM,
    apply_cap,
    generate_pairs,
)
from grc_sidecar.contradictions.status import decide, decide_from_verdict
from grc_sidecar.contradictions.verdict import Failure, PairAsker, VerdictOutcome
from grc_sidecar.contradictions.verify import Verification, verify_pair
from grc_sidecar.errors import IndexNotBuiltError
from grc_sidecar.repositories.base import VectorRepository
from grc_sidecar.retrieval.query import build_lexical_query
from grc_sidecar.retrieval.service import source_kind_of
from grc_sidecar.retrieval.withdrawn import is_withdrawn_section

_log = logging.getLogger(__name__)

# A model that cannot be used at all: once one pair hits this, no further pair is asked.
_SYSTEMIC: tuple[Failure, ...] = ("unavailable", "model_missing", "non_local_refused")


def _place(path: str, lines: tuple[int, int]) -> str:
    first, last = lines
    return f"{path}:{first}" if first == last else f"{path}:{first}-{last}"


def _finding(
    pair: Pair,
    check: Verification,
    status: FindingStatus,
    reasons: tuple[CandidateReason, ...],
    flags: tuple[Flag, ...],
    verdict: ModelVerdictView | None = None,
) -> Finding:
    # A verified pair always has its located lines; the claim's own lines are only a fallback.
    lines_a = check.lines_a or (pair.a.start_line, pair.a.end_line)
    lines_b = check.lines_b or (pair.b.start_line, pair.b.end_line)
    return Finding(
        finding_id=pair.pair_id,
        status=status,
        quote_a=pair.a.quote,
        source_a=_place(pair.a.source_path, lines_a),
        quote_b=pair.b.quote,
        source_b=_place(pair.b.source_path, lines_b),
        subject=pair.a.subject,
        numbers=(pair.a.number.text, pair.b.number.text),
        flags=flags,
        reasons=reasons,
        verdict=verdict,
    )


def _drop(dropped: dict[str, int], reasons: Sequence[str]) -> None:
    for reason in reasons:
        dropped[reason] = dropped.get(reason, 0) + 1


def _prepare(
    texts: Mapping[str, ChunkText],
    claims: Sequence[Claim],
    neighbours: Mapping[str, frozenset[str]],
    cap: int,
) -> tuple[int, list[tuple[Pair, Verification]], dict[str, int], int]:
    """Pairs, then verification, then the cap: (pairs made, kept pairs, drops by reason, left out).

    Verification runs before the cap, so dropped pairs do not use the cap up."""
    pairs = generate_pairs(claims, neighbours)
    context_ids = frozenset(texts)
    dropped: dict[str, int] = {}
    verified: list[tuple[Pair, Verification]] = []
    for pair in pairs:
        check = verify_pair(pair, texts, context_ids)
        if check.ok:
            verified.append((pair, check))
        _drop(dropped, check.reasons)
    kept, left_out = apply_cap(verified, cap)
    if left_out:
        dropped["over_cap"] = left_out
    return len(pairs), kept, dropped, left_out


def build_report(
    texts: Mapping[str, ChunkText],
    claims: Sequence[Claim],
    neighbours: Mapping[str, frozenset[str]],
    *,
    cap: int,
    withheld_stale: int = 0,
    excluded_chunks: int = 0,
) -> DetectionReport:
    """The code half only. Pure: no index and no model, so every finding is a `CANDIDATE`."""
    made, kept, dropped, left_out = _prepare(texts, claims, neighbours, cap)
    findings = []
    for pair, check in kept:
        status, reasons, flags = decide(pair, keeps_conflict=None)
        findings.append(_finding(pair, check, status, reasons, flags))
    return DetectionReport(
        findings=tuple(findings),
        dropped=dropped,
        chunks_read=len(texts),
        excluded_chunks=excluded_chunks,
        claims_found=len(claims),
        pairs_generated=made,
        withheld_stale=withheld_stale,
        says=NUMERIC_ONLY_STATEMENT,
        over_cap=left_out,
        model_status="unavailable",
        no_verdict=len(kept),
    )


def _model_use(failure: Failure | None, asked: int, answered: int) -> ModelUse:
    """The fixed word for how the model fared. `partial` is some pairs with a verdict and some
    without; a model that could not be used at all is named by why."""
    if failure is not None and failure in _SYSTEMIC:
        if answered:
            return "partial"
        if failure == "model_missing":
            return "model_missing"
        if failure == "non_local_refused":
            return "non_local_refused"
        return "unavailable"
    if asked and answered < asked:
        return "partial"
    return "available"


async def build_report_with_model(
    texts: Mapping[str, ChunkText],
    claims: Sequence[Claim],
    neighbours: Mapping[str, frozenset[str]],
    *,
    cap: int,
    asker: PairAsker,
    withheld_stale: int = 0,
    excluded_chunks: int = 0,
) -> DetectionReport:
    """The code half, then one question per kept pair, strongest first (the cap's order).

    Pairs are asked one after another. When the model cannot be used at all, the remaining pairs
    are not asked and have no verdict. Every valid reply is counted by relation and `conflict`,
    even when the ceiling caps the status (decision 9; the live-check measure of CD-112)."""
    made, kept, dropped, left_out = _prepare(texts, claims, neighbours, cap)
    findings: list[Finding] = []
    counts: dict[str, dict[str, int]] = {}
    answered = no_verdict = replies = 0
    failure: Failure | None = None
    for pair, check in kept:
        if failure in _SYSTEMIC:
            outcome = VerdictOutcome(None, False, (), failure, 0)  # not asked
        else:
            outcome = await asker.ask(pair, texts)
            replies += outcome.replies
            if outcome.failure in _SYSTEMIC:
                failure = outcome.failure
        verdict = outcome.verdict
        if verdict is not None:
            answered += 1
            tally = counts.setdefault(verdict.relation, {"conflict_true": 0, "conflict_false": 0})
            tally["conflict_true" if verdict.conflict else "conflict_false"] += 1
        else:
            no_verdict += 1
        if outcome.shown_chunk_ids:
            # "Cited in context" means the chunks actually in THIS prompt (ADR-019, chunk 4b).
            shown = verify_pair(pair, texts, frozenset(outcome.shown_chunk_ids))
            if not shown.ok:
                _drop(dropped, shown.reasons)
                continue
        status, reasons, flags = decide_from_verdict(
            pair, verdict, quote_invalid=outcome.quote_invalid
        )
        view = (
            None
            if verdict is None
            else ModelVerdictView(
                conflict=verdict.conflict,
                relation=verdict.relation,
                explanation=verdict.explanation,
                model_confidence=verdict.model_confidence,
            )
        )
        findings.append(_finding(pair, check, status, reasons, flags, view))
    model_status = _model_use(failure, len(kept), answered)
    _log.info(
        "contradictions: pairs=%d kept=%d answered=%d no_verdict=%d over_cap=%d model=%s",
        made,
        len(kept),
        answered,
        no_verdict,
        left_out,
        model_status,
    )
    return DetectionReport(
        findings=tuple(findings),
        dropped=dropped,
        chunks_read=len(texts),
        excluded_chunks=excluded_chunks,
        claims_found=len(claims),
        pairs_generated=made,
        withheld_stale=withheld_stale,
        says=NUMERIC_ONLY_STATEMENT,
        over_cap=left_out,
        model_status=model_status,
        model_calls=replies,
        verdict_counts=counts,
        no_verdict=no_verdict,
    )


def neighbour_query(claim: Claim) -> str:
    """The FTS5 match for a claim's lexical neighbours: each subject word and its plural forms.

    The index does not stem, so `account` would never find `Accounts`; the claim's words are light
    stems (`claims.stem`), and the query adds the plain, `s` and `ies` forms of each.
    """
    forms: list[str] = []
    for word in sorted(claim.subject_words):
        forms.extend((word, word + "s"))
        if word.endswith("y"):
            forms.append(word[:-1] + "ies")
    return build_lexical_query(" ".join(forms)).match


def _in_scope(path: str, scope: str | None) -> bool:
    return scope is None or path == scope or path.startswith(scope.rstrip("/") + "/")


class ContradictionDetector:
    def __init__(self, repo: VectorRepository, asker: PairAsker | None = None) -> None:
        """`asker` asks the model about each kept pair; None means no model is consulted."""
        self._repo = repo
        self._asker = asker

    async def detect(
        self, *, scope: str | None = None, cap: int = DEFAULT_PAIR_CAP
    ) -> DetectionReport:
        """Compare the numbers in the vault's company-policy text. `scope` is an optional
        vault-relative path prefix (a string match on index paths; no file is opened).
        Raises IndexNotBuiltError if there is no index. Reading needs no embedding model."""
        if cap < 1:
            raise ValueError("cap must be at least 1")
        if self._repo.mode == "closed" and not await self._repo.open_for_deletions():
            raise IndexNotBuiltError
        states = await self._repo.document_states()
        stale = sum(1 for state in states.values() if state.stale)
        paths = sorted(p for p, s in states.items() if not s.stale and _in_scope(p, scope))
        info = await self._repo.documents_info(paths)
        texts: dict[str, ChunkText] = {}
        excluded = 0
        for path in paths:
            document = info.get(path)
            if document is None:
                continue
            company = source_kind_of(document.collection, document.type) == "company_policy"
            for chunk in await self._repo.chunks_for(path):
                if not company or is_withdrawn_section(chunk.heading_path):
                    excluded += 1
                    continue
                texts[chunk.chunk_id] = ChunkText(
                    chunk_id=chunk.chunk_id,
                    source_path=chunk.source_path,
                    start_line=chunk.start_line,
                    text=chunk.text,
                )
        claims = [claim for text in texts.values() for claim in extract_claims(text)]
        neighbours: dict[str, frozenset[str]] = {}
        by_match: dict[str, frozenset[str]] = {}
        for claim in claims:
            match = neighbour_query(claim)
            if match not in by_match:
                hits = await self._repo.lexical_search(match, NEIGHBOURS_PER_CLAIM) if match else []
                by_match[match] = frozenset(hit.chunk.chunk_id for hit in hits)
            neighbours[claim.claim_id] = by_match[match]
        if self._asker is None:
            return build_report(
                texts,
                claims,
                neighbours,
                cap=cap,
                withheld_stale=stale,
                excluded_chunks=excluded,
            )
        return await build_report_with_model(
            texts,
            claims,
            neighbours,
            cap=cap,
            asker=self._asker,
            withheld_stale=stale,
            excluded_chunks=excluded,
        )
