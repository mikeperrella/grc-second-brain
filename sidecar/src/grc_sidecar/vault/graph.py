"""The vault graph: documents and the explicit links between them (ADR-023, CLAUDE.md 8.2).

It is a read-only VAULT SCAN, not the index. The index can be stale; this reads the files as they
are now, through the same scanner and parser the indexer uses (`scan_vault`) and the same link
extraction and resolution (`extract_links`, `ResolutionIndex`, `resolve_link`). Nothing else is
used to make an edge:

- **Explicit links only.** An edge is a `[[wikilink]]` or a frontmatter `links` entry that
  RESOLVED to another document. Never text similarity, never a shared control mapping.
- **Nothing outside the vault is ever followed.** Resolution looks only at the set of documents the
  scanner already accepted (it opens no file), a link written as an absolute path, a drive, a URL
  or with a `..` segment is `rejected` before any lookup, and the scanner refuses a symlink or
  junction (it is neither read nor entered).
- **No document text leaves this module.** Nodes carry the vault-relative path, `doc_id`, `type`
  and a count; edges carry two node indexes, an origin and a count. Unresolved, ambiguous and
  rejected links are only counted.
- **Whole or nothing.** Above `MAX_GRAPH_NODES` nodes or `MAX_GRAPH_EDGES` edges (unmeasured
  starting values) the whole graph is refused with integer counts, never a partial list that looks
  complete.
- **`live_promises` is None, never 0, when no ledger folder is in use.** "0 promises" would read as
  "nothing rests on this document". With a ledger it counts live (not voided) promises whose stored
  `source_path` equals the node's path exactly.

Notes typed `framework` are not company documents (the rule `ControlMap` and retrieval apply), so
they are not nodes; a link into one is counted in `links_not_drawn`, with self-links.
"""

import asyncio
import dataclasses
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from grc_sidecar.errors import ConfigurationError, GraphRefusedError, VaultRootNotConfiguredError
from grc_sidecar.vault.links import ResolutionIndex, extract_links, resolve_link
from grc_sidecar.vault.models import LinkOrigin, ScanResult
from grc_sidecar.vault.scan import scan_vault

MAX_GRAPH_NODES: Final = 500  # starting values, unmeasured (ADR-023 decision 3)
MAX_GRAPH_EDGES: Final = 5_000

SAYS: Final = (
    "Lines are the explicit links written in the files. "
    "A missing line does not mean two documents are unrelated."
)

LiveCounts = Callable[[], Awaitable[Mapping[str, int]]]


@dataclass(frozen=True)
class GraphNode:
    index: int
    path: str  # vault-relative, forward slashes
    doc_id: str
    type: str
    live_promises: int | None  # None: no ledger folder in use (never 0)


@dataclass(frozen=True)
class GraphEdge:
    source: int  # a node index
    target: int  # a node index
    origin: LinkOrigin
    count: int  # how many links with this origin run from `source` to `target`


@dataclass(frozen=True)
class VaultGraph:
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    unresolved_links: int
    ambiguous_links: int
    rejected_links: int
    links_not_drawn: int  # resolved links that are self-links or lead to a framework note
    files_refused: int  # files the scanner refused (a link, bad frontmatter, ...): not nodes
    says: str


def build_graph(scan: ScanResult) -> VaultGraph:
    """The graph of one scan, with `live_promises` None. Raises GraphRefusedError above a cap."""
    documents = scan.documents  # sorted by path; a duplicated doc_id is already left out
    resolution = ResolutionIndex((doc.source_path, doc.frontmatter.doc_id) for doc in documents)
    shown = [doc for doc in documents if doc.frontmatter.type != "framework"]
    node_of = {doc.frontmatter.doc_id: index for index, doc in enumerate(shown)}
    tally: Counter[str] = Counter()
    edges: Counter[tuple[int, int, LinkOrigin]] = Counter()
    for source, doc in enumerate(shown):
        for link in extract_links(doc):
            resolved = resolve_link(link, resolution)
            if resolved.status != "resolved":
                tally[resolved.status] += 1
                continue
            target = None if resolved.target_doc_id is None else node_of.get(resolved.target_doc_id)
            if target is None or target == source:
                tally["not_drawn"] += 1
            else:
                edges[(source, target, link.origin)] += 1
    if len(shown) > MAX_GRAPH_NODES or len(edges) > MAX_GRAPH_EDGES:
        raise GraphRefusedError(
            "graph_too_large",
            {
                "nodes": len(shown),
                "edges": len(edges),
                "node_cap": MAX_GRAPH_NODES,
                "edge_cap": MAX_GRAPH_EDGES,
            },
        )
    return VaultGraph(
        nodes=tuple(
            GraphNode(index, doc.source_path, doc.frontmatter.doc_id, doc.frontmatter.type, None)
            for index, doc in enumerate(shown)
        ),
        edges=tuple(
            GraphEdge(source, target, origin, count)
            for (source, target, origin), count in sorted(edges.items())
        ),
        unresolved_links=tally["unresolved"],
        ambiguous_links=tally["ambiguous"],
        rejected_links=tally["rejected"],
        links_not_drawn=tally["not_drawn"],
        files_refused=sum(1 for issue in scan.issues if issue.severity == "error"),
        says=SAYS,
    )


def with_live_promises(graph: VaultGraph, counts: Mapping[str, int]) -> VaultGraph:
    """The same graph with each node's live promise count; a path nobody cites has 0."""
    return dataclasses.replace(
        graph,
        nodes=tuple(
            dataclasses.replace(node, live_promises=counts.get(node.path, 0))
            for node in graph.nodes
        ),
    )


class VaultGraphService:
    """Scans the configured vault for a graph. `live_counts` is None when no ledger is in use."""

    def __init__(self, vault_root: Path, live_counts: LiveCounts | None) -> None:
        self._vault_root = vault_root
        self._live_counts = live_counts

    async def graph(self) -> VaultGraph:
        """Scan off the event loop, apply the caps, then (only if it fits) add the ledger counts."""
        try:
            scan = await asyncio.to_thread(scan_vault, self._vault_root)
        except ConfigurationError:
            raise VaultRootNotConfiguredError from None  # the vault folder itself is unusable
        graph = build_graph(scan)
        if self._live_counts is None:
            return graph
        return with_live_promises(graph, await self._live_counts())
