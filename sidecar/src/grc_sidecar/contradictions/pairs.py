"""Candidate pairs (ADR-019 decision 7).

A pair is two claims that are comparable, have different values, and have subject words in common.
Pairs are made by code from claims; nothing the model says can add one.

**Comparable.** The same kind, or `days` against `months` (a candidate, never a conflict: a month is
not a fixed number of days). A frequency is never compared with a duration, and business days are
never compared with calendar days.

**Overlap.** `strong` when one claim's subject words are all inside the other's (`Backups` inside
`Database backups`, `Security logs` against `security logs`); `weak` when they only share some
words (`Application logs` against `Security logs` share `logs` and nothing else). Only a strong
pair can ever become a `VALUE_CONFLICT`.
"""

import hashlib
from collections.abc import Mapping, Sequence
from typing import Final, TypeVar

from grc_sidecar.contradictions.models import Claim, Overlap, Pair

_T = TypeVar("_T")

DEFAULT_PAIR_CAP: Final = 30  # an unmeasured starting value, tuned on the dev split only (ADR-019)
NEIGHBOURS_PER_CLAIM: Final = 20  # lexical neighbours (FTS5 hits) each claim is compared with


def comparable(a: Claim, b: Claim) -> bool:
    kinds = {a.number.kind, b.number.kind}
    return len(kinds) == 1 or kinds == {"days", "months"}


def differ(a: Claim, b: Claim) -> bool:
    """Different values after conversion. Days against months always differ: nothing converts."""
    if a.number.kind != b.number.kind:
        return True
    return a.number.value != b.number.value


def overlap_of(a: Claim, b: Claim) -> Overlap | None:
    if not a.subject_words & b.subject_words:
        return None
    if a.subject_words <= b.subject_words or b.subject_words <= a.subject_words:
        return "strong"
    return "weak"


def _order_key(claim: Claim) -> tuple[str, int, str]:
    return (claim.source_path, claim.start_line, claim.claim_id)


def pair_id_of(a: Claim, b: Claim) -> str:
    return hashlib.sha256(f"{a.claim_id}|{b.claim_id}".encode()).hexdigest()[:16]


def generate_pairs(claims: Sequence[Claim], neighbours: Mapping[str, frozenset[str]]) -> list[Pair]:
    """Every pair of comparable, differing, overlapping claims, strongest first.

    `neighbours` maps a claim id to the chunk ids it may be compared with (its lexical
    neighbours); a claim's own chunk is always included. Each unordered pair appears once, with
    its two claims in file order.
    """
    by_chunk: dict[str, list[Claim]] = {}
    for claim in claims:
        by_chunk.setdefault(claim.chunk_id, []).append(claim)
    seen: set[tuple[str, str]] = set()
    pairs: list[Pair] = []
    for first in claims:
        allowed = neighbours.get(first.claim_id, frozenset()) | {first.chunk_id}
        for chunk_id in sorted(allowed):
            for second in by_chunk.get(chunk_id, []):
                if second.claim_id == first.claim_id:
                    continue
                a, b = sorted((first, second), key=_order_key)
                key = (a.claim_id, b.claim_id)
                if key in seen:
                    continue
                seen.add(key)
                if not comparable(a, b) or not differ(a, b):
                    continue
                strength = overlap_of(a, b)
                if strength is None:
                    continue
                pairs.append(Pair(pair_id=pair_id_of(a, b), a=a, b=b, overlap=strength))
    pairs.sort(
        key=lambda p: (p.overlap != "strong", _order_key(p.a), _order_key(p.b)),
    )
    return pairs


def apply_cap(items: Sequence[_T], cap: int) -> tuple[list[_T], int]:
    """The first `cap` items, and how many were left out (counted, never silently lost)."""
    kept = list(items[:cap])
    return kept, len(items) - len(kept)
