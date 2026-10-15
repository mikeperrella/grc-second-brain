"""The detector's code half: read the index, find claims, make pairs, verify, set the status.

No model is called here (chunk 4a-2), so no finding is ever `VALUE_CONFLICT`. Every finding that
survives verification is a `CANDIDATE` flagged `model_not_consulted`.

Reading goes through the existing `VectorRepository` and nothing else: the chunks come from
`chunks_for`, their kind from the `documents` row (`source_kind_of`, the rule retrieval uses), and
each claim's lexical neighbours from the same FTS5 search retrieval uses. Framework text and
withdrawn controls are never read into the comparison. A stale document has no rows in the index,
so a file that changed after indexing is never compared; the report counts those documents.
"""

from collections.abc import Mapping, Sequence

from grc_sidecar.contradictions.claims import extract_claims
from grc_sidecar.contradictions.models import (
    NUMERIC_ONLY_STATEMENT,
    ChunkText,
    Claim,
    DetectionReport,
    Finding,
    Pair,
)
from grc_sidecar.contradictions.pairs import (
    DEFAULT_PAIR_CAP,
    NEIGHBOURS_PER_CLAIM,
    apply_cap,
    generate_pairs,
)
from grc_sidecar.contradictions.status import decide
from grc_sidecar.contradictions.verify import Verification, verify_pair
from grc_sidecar.errors import IndexNotBuiltError
from grc_sidecar.repositories.base import VectorRepository
from grc_sidecar.retrieval.query import build_lexical_query
from grc_sidecar.retrieval.service import source_kind_of
from grc_sidecar.retrieval.withdrawn import is_withdrawn_section


def _place(path: str, lines: tuple[int, int]) -> str:
    first, last = lines
    return f"{path}:{first}" if first == last else f"{path}:{first}-{last}"


def _finding(pair: Pair, check: Verification) -> Finding:
    status, reasons, flags = decide(pair, keeps_conflict=None)
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
    )


def build_report(
    texts: Mapping[str, ChunkText],
    claims: Sequence[Claim],
    neighbours: Mapping[str, frozenset[str]],
    *,
    cap: int,
    withheld_stale: int = 0,
    excluded_chunks: int = 0,
) -> DetectionReport:
    """Pairs, then verification, then the cap. Pure: no index and no model."""
    pairs = generate_pairs(claims, neighbours)
    context_ids = frozenset(texts)
    dropped: dict[str, int] = {}
    verified: list[tuple[Pair, Verification]] = []
    for pair in pairs:
        check = verify_pair(pair, texts, context_ids)
        if check.ok:
            verified.append((pair, check))
        for reason in check.reasons:
            dropped[reason] = dropped.get(reason, 0) + 1
    kept, left_out = apply_cap(verified, cap)
    if left_out:
        dropped["over_cap"] = left_out
    return DetectionReport(
        findings=tuple(_finding(pair, check) for pair, check in kept),
        dropped=dropped,
        chunks_read=len(texts),
        excluded_chunks=excluded_chunks,
        claims_found=len(claims),
        pairs_generated=len(pairs),
        withheld_stale=withheld_stale,
        says=NUMERIC_ONLY_STATEMENT,
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
    def __init__(self, repo: VectorRepository) -> None:
        self._repo = repo

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
        return build_report(
            texts,
            claims,
            neighbours,
            cap=cap,
            withheld_stale=stale,
            excluded_chunks=excluded,
        )
