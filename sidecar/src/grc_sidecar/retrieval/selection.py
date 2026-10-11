"""Which chunks make it into the context: slots, the character budget, the framework quota (M4f).

All numbers live here, as named constants, with the reason for each (ADR-010).

**Size.** The client cannot count tokens for the generation model, so the budget is in CHARACTERS,
from a deliberately pessimistic ratio. Starting point: the OWASP RAG Security Cheat Sheet's
3 to 5 chunks and 2,000 to 4,000 tokens. 4,000 tokens x 1.38 characters per token = 5,520
characters, where 1.38 is the worst ratio measured for dense control text in this project
(ADR-009: identifier-dense text measured 1.38 to 1.41; ordinary prose 5.55). It is an
approximation that errs toward a smaller context, never a larger one, and it is a request
parameter (`char_budget`), so M5 can change it after measuring. A chunk is never truncated to fit
(that would break the verbatim-quote check of CLAUDE.md 8.4): one that does not fit is skipped and
the next is tried.

**Slots.** `max_chunks` (default 5) is split into one reserved EXPANSION slot and the rest for
seeds. The expansion slot is released to a company seed when no linked chunk qualifies, so it is
never wasted. Among the seeds, framework text is capped at 2 chunks and 40% of the character
budget, and company policy is guaranteed at least 2 seed slots. Why a quota and not "company
first": vector search always returns neighbours, so strict company-first would let irrelevant
company text starve the one framework control a user named. Framework text is never evidence that
the company does something (`source_kind`), which is why it is capped at all.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from grc_sidecar.repositories.base import DocInfo
from grc_sidecar.retrieval.fusion import Fused
from grc_sidecar.retrieval.models import SourceKind

DEFAULT_MAX_CHUNKS: Final = 5
DEFAULT_TOKENS: Final = 4000
WORST_CHARS_PER_TOKEN: Final = 1.38
DEFAULT_CHAR_BUDGET: Final = int(DEFAULT_TOKENS * WORST_CHARS_PER_TOKEN)  # 5,520

EXPANSION_SLOTS: Final = 1
FRAMEWORK_MAX_CHUNKS: Final = 2
# 40%, as a fraction so the cap is exact integer arithmetic: char_budget * 2 // 5.
FRAMEWORK_SHARE_NUMERATOR: Final = 2
FRAMEWORK_SHARE_DENOMINATOR: Final = 5
COMPANY_MIN_SEEDS: Final = 2
DEFAULT_EXPANSION_DEPTH: Final = 1
MAX_EXPANSION_DEPTH: Final = 2


@dataclass(frozen=True)
class Candidate:
    """A search result that may be selected. `withdrawn` is a framework stub (see withdrawn.py)."""

    item: Fused
    document: DocInfo
    kind: SourceKind
    withdrawn: bool

    @property
    def size(self) -> int:
        return len(self.item.chunk.text)


@dataclass(frozen=True)
class SeedSelection:
    seeds: tuple[Candidate, ...]
    used_chars: int
    framework_chars: int
    dropped_for_budget: tuple[str, ...]  # chunk_ids skipped because they did not fit


def expansion_slots(max_chunks: int) -> int:
    """One slot is kept for an expanded chunk, unless there is only room for a single chunk."""
    return EXPANSION_SLOTS if max_chunks >= 2 else 0


def seed_slots(max_chunks: int) -> int:
    return max_chunks - expansion_slots(max_chunks)


def framework_chunk_cap(max_chunks: int) -> int:
    """At most FRAMEWORK_MAX_CHUNKS, and always leaving COMPANY_MIN_SEEDS seed slots to company."""
    return max(0, min(FRAMEWORK_MAX_CHUNKS, seed_slots(max_chunks) - COMPANY_MIN_SEEDS))


def framework_char_cap(char_budget: int) -> int:
    return char_budget * FRAMEWORK_SHARE_NUMERATOR // FRAMEWORK_SHARE_DENOMINATOR


def select_seeds(
    ranked: Sequence[Candidate], *, max_chunks: int, char_budget: int
) -> SeedSelection:
    """Pick seeds from `ranked` (best first), within the slots, the budget and the framework quota.

    Walks the list once, in rank order. A candidate is taken if there is a free seed slot, it fits
    in the characters left, and (for framework text) the framework quota still has room;
    otherwise it is skipped and the next is tried. Never truncates.
    """
    slots = seed_slots(max_chunks)
    framework_chunks_allowed = framework_chunk_cap(max_chunks)
    framework_chars_allowed = framework_char_cap(char_budget)

    seeds: list[Candidate] = []
    used = 0
    framework_chars = 0
    framework_count = 0
    dropped: list[str] = []
    for candidate in ranked:
        if len(seeds) == slots:
            break
        if candidate.kind == "framework_reference" and (
            framework_count >= framework_chunks_allowed
            or framework_chars + candidate.size > framework_chars_allowed
        ):
            continue
        if used + candidate.size > char_budget:
            dropped.append(candidate.item.chunk.chunk_id)
            continue
        seeds.append(candidate)
        used += candidate.size
        if candidate.kind == "framework_reference":
            framework_count += 1
            framework_chars += candidate.size
    return SeedSelection(
        seeds=tuple(seeds),
        used_chars=used,
        framework_chars=framework_chars,
        dropped_for_budget=tuple(dropped),
    )
