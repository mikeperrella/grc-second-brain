"""The order candidates are considered in (ADR-010, M4f).

For an ordinary question the order is the Reciprocal Rank Fusion order: score, then chunk_id.

For a question that is ONLY identifiers (`03.01.01`, `AC-2`, `what is CC6.1?`) that is not good
enough, for two measured reasons. An embedding blurs an identifier (ADR-010 context), so the
nearest vectors of `03.01.01` are unrelated text, yet under RRF the one real keyword hit and the
nearest vector tie at rank 1 and chunk_id decides. And BM25 prefers short chunks, so for `AC-2` the
short enhancement chunks (`AC-2(4) ...`) beat the control's own chunk.

So for an identifiers-only question the candidates are tiered, then ordered by the RRF order inside
each tier:

0. the chunk whose own heading IS the identifier (`## AC-2 Account Management` for `AC-2`; not
   `AC-2(4) ...`, which is a different control);
1. any other chunk the keyword search found;
2. chunks found only by the vector search.

This only reorders candidates; the slots, the budget and the framework quota still apply after it.
"""

from collections.abc import Sequence
from typing import Final

from grc_sidecar.retrieval.query import LexicalQuery
from grc_sidecar.retrieval.selection import Candidate

_HEADING_SEPARATOR: Final = " > "


def heading_names_identifier(heading_path: str, forms: set[str]) -> bool:
    """True if the last heading segment starts with one of `forms` as a whole word.

    `AC-2 Account Management` names `AC-2`; `AC-2(4) Automated Audit Actions` and `AC-20 ...` do
    not (the identifier must be followed by a space or the end).
    """
    heading = heading_path.split(_HEADING_SEPARATOR)[-1].strip().casefold()
    for form in forms:
        if heading.startswith(form) and (len(heading) == len(form) or heading[len(form)].isspace()):
            return True
    return False


def _tier(candidate: Candidate, forms: set[str]) -> int:
    if heading_names_identifier(candidate.item.chunk.heading_path, forms):
        return 0
    return 1 if candidate.item.lexical_rank is not None else 2


def order_candidates(candidates: Sequence[Candidate], query: LexicalQuery) -> list[Candidate]:
    """Candidates in the order the selection should consider them (see the module docstring)."""
    if query.identifiers_only:
        forms = {form.casefold() for group in query.identifier_forms for form in group}
        return sorted(
            candidates,
            key=lambda c: (_tier(c, forms), -c.item.score, c.item.chunk.chunk_id),
        )
    return sorted(candidates, key=lambda c: (-c.item.score, c.item.chunk.chunk_id))
