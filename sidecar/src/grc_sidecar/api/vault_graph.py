"""The vault graph route (ADR-023). Thin: every decision is made in `vault/graph.py`.

Behind the auth middleware like every route. It takes no query and no body. The reply holds
vault-relative paths, `doc_id`, `type` and integers, never document text.

- `GET /vault/graph`  documents as nodes and resolved explicit links as edges; 422 `graph_refused`
                      with code `graph_too_large` and integer counts above the caps (no partial
                      graph); 503 `vault_root_not_configured`

`live_promises` is null on every node when no ledger folder is in use, never 0.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from grc_sidecar.errors import VaultRootNotConfiguredError
from grc_sidecar.vault.graph import VaultGraph, VaultGraphService

router = APIRouter()


def get_vault_graph(request: Request) -> VaultGraphService:
    service = getattr(request.app.state, "vault_graph", None)
    if not isinstance(service, VaultGraphService):
        raise VaultRootNotConfiguredError
    return service


Graph = Annotated[VaultGraphService, Depends(get_vault_graph)]


class _Body(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class NodeResponse(_Body):
    index: int
    path: str
    doc_id: str
    type: str
    live_promises: int | None


class EdgeResponse(_Body):
    from_: int = Field(alias="from")
    to: int
    origin: str
    count: int


class GraphResponse(_Body):
    nodes: list[NodeResponse]
    edges: list[EdgeResponse]
    unresolved_links: int
    ambiguous_links: int
    rejected_links: int
    links_not_drawn: int
    files_refused: int
    says: str


def _graph_response(graph: VaultGraph) -> GraphResponse:
    return GraphResponse(
        nodes=[
            NodeResponse(
                index=n.index,
                path=n.path,
                doc_id=n.doc_id,
                type=n.type,
                live_promises=n.live_promises,
            )
            for n in graph.nodes
        ],
        edges=[
            EdgeResponse(from_=e.source, to=e.target, origin=e.origin, count=e.count)
            for e in graph.edges
        ],
        unresolved_links=graph.unresolved_links,
        ambiguous_links=graph.ambiguous_links,
        rejected_links=graph.rejected_links,
        links_not_drawn=graph.links_not_drawn,
        files_refused=graph.files_refused,
        says=graph.says,
    )


@router.get("/vault/graph")
async def vault_graph(service: Graph) -> GraphResponse:
    return _graph_response(await service.graph())
