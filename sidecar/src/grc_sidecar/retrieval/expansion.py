"""Graph expansion: follow explicit links from the seeds (CLAUDE.md 8.2, ADR-010, M4f).

The only edges are the document's own explicit links, as the indexer resolved them: `[[wikilinks]]`
in the body and `links` in the frontmatter. A link counts only if it RESOLVED to exactly one
document; unresolved, ambiguous and rejected links are never followed. Similarity is never an edge,
and neither are `control_mappings`.

Which chunks a link leads to: a body link belongs to the chunk whose line range contains the line
it is written on, a frontmatter link belongs to every chunk of its document. At the target, a
`#Heading` fragment selects the chunks under that heading; otherwise the lead chunk (the first by
line number) is used, which is deterministic and needs no extra query. Known limit: the lead chunk
may not be the most relevant one.

The walk is breadth-first and bounded: `depth` levels (default 1, never more than
MAX_EXPANSION_DEPTH), and a visited set (seeded with the seeds) so a cycle (A links B, B links A)
cannot add a chunk twice or loop. Candidates come out in a fixed order: level, then the rank of the
seed they came from, then target path and line.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from grc_sidecar.repositories.base import VectorRepository
from grc_sidecar.vault.models import Chunk

_SEGMENT_SEPARATOR: Final = " > "


@dataclass(frozen=True)
class Expansion:
    """A chunk reached by following links. `root_seed` is the seed the walk started from."""

    chunk: Chunk
    root_seed: str  # chunk_id of the seed
    level: int  # 1 = linked directly from a seed


def _heading_matches(heading_path: str, fragment: str) -> bool:
    wanted = fragment.strip().casefold()
    return any(part.strip().casefold() == wanted for part in heading_path.split(_SEGMENT_SEPARATOR))


async def _linked_chunks(repo: VectorRepository, chunk: Chunk) -> list[Chunk]:
    """The chunks that `chunk`'s resolved links lead to, in (path, start line) order."""
    reached: list[Chunk] = []
    for resolved in await repo.links_for(chunk.source_path):
        if resolved.status != "resolved" or resolved.target_doc_id is None:
            continue  # unresolved, ambiguous or rejected: never followed
        link = resolved.link
        in_this_chunk = chunk.start_line <= link.source_line <= chunk.end_line
        if link.origin == "body" and not in_this_chunk:
            continue  # a body link belongs to the chunk it is written in
        target_path = await repo.source_path_of_doc_id(resolved.target_doc_id)
        if target_path is None:
            continue
        target_chunks = await repo.chunks_for(target_path)  # ordered by start line
        if not target_chunks:
            continue
        under_heading: list[Chunk] = []
        if link.fragment and not link.fragment.startswith("^"):
            under_heading = [
                c for c in target_chunks if _heading_matches(c.heading_path, link.fragment)
            ]
        reached.extend(under_heading or target_chunks[:1])
    return sorted(reached, key=lambda c: (c.source_path, c.start_line, c.chunk_id))


async def expansion_candidates(
    repo: VectorRepository, seeds: Sequence[Chunk], *, depth: int
) -> list[Expansion]:
    """Every chunk reachable from `seeds` within `depth` levels, in the fixed order above."""
    visited: set[str] = {seed.chunk_id for seed in seeds}
    frontier: list[tuple[Chunk, str]] = [(seed, seed.chunk_id) for seed in seeds]
    found: list[Expansion] = []
    for level in range(1, depth + 1):
        next_frontier: list[tuple[Chunk, str]] = []
        for chunk, root in frontier:  # frontier is in seed-rank order
            for target in await _linked_chunks(repo, chunk):
                if target.chunk_id in visited:
                    continue
                visited.add(target.chunk_id)
                found.append(Expansion(chunk=target, root_seed=root, level=level))
                next_frontier.append((target, root))
        frontier = next_frontier
    return found
