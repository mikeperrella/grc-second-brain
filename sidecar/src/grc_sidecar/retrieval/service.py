"""Hybrid retrieval: keyword and vector search over the vault and the framework packs (M4e).

`Retriever.retrieve(question)` does, in this order (the order is the safety property):

1. `refresh_staleness()` on the vault index BEFORE anything is read. A file edited since the last
   sync loses its rows now, so its old text cannot be returned. If the check cannot run, nothing
   is returned (`RetrievalUnavailableError`); if there is no index at all, `IndexNotBuiltError`.
2. A keyword query, built so that user text can only ever be literal words (query.py), runs in
   each store.
3. The vector side: the model gate (`ModelGate.require`, CLAUDE.md 3.7), then the query is embedded
   and searched in each store. If the embedding model is missing or Ollama is down, retrieval
   returns the keyword results with `mode="lexical_only"` and the reason. It is never a silent
   fallback. A profile mismatch, a dimension error or a store error is NOT downgraded: the index
   needs a human.
4. Per store, the two ranked lists are merged by Reciprocal Rank Fusion (fusion.py).

Two stores, never mixed: the vault index (the company's own documents) and the packs index
(framework text, read-only, ADR-008). Each chunk is tagged with a `source_kind` computed HERE from
where the document came from, never from what the document says about itself.

Freshness of the packs is checked when they are loaded (ADR-008), not per question.

Retrieval never builds an index and never writes one: the repositories are opened for reading
(or for deletions only, to drop stale rows).
"""

import asyncio
from datetime import date
from typing import Protocol

from grc_sidecar.errors import (
    EmbeddingUnavailableError,
    GrcError,
    IndexNotBuiltError,
    ModelMissingError,
    ModelsUnavailableError,
    RetrievalUnavailableError,
)
from grc_sidecar.llm.embeddings import EmbeddingClient
from grc_sidecar.llm.model_gate import ModelGate
from grc_sidecar.repositories.base import (
    DocInfo,
    EmbeddingProfile,
    LexicalHit,
    ScoredChunk,
    VectorRepository,
)
from grc_sidecar.retrieval.fusion import Fused, fuse
from grc_sidecar.retrieval.models import (
    DegradedReason,
    RetrievalResult,
    RetrievedChunk,
    SourceKind,
)
from grc_sidecar.retrieval.query import MAX_QUERY_CHARS, build_lexical_query
from grc_sidecar.vault.clock import Clock, is_stale
from grc_sidecar.vault.indexer import RefreshReport

PACK_COLLECTION_PREFIX = "pack:"
CANDIDATES_PER_LIST = 20  # how many results each of the two searches contributes to the merge
DEFAULT_LIMIT = 5


class StalenessRefresher(Protocol):
    async def refresh_staleness(self) -> RefreshReport: ...


def source_kind_of(collection: str, doc_type: str) -> SourceKind:
    """Framework text is recognised by where it was loaded from (a pack collection) or by its
    declared type. Either one makes it `framework_reference`; a vault note that merely claims to
    be a policy stays what its location says it is."""
    if collection.startswith(PACK_COLLECTION_PREFIX) or doc_type == "framework":
        return "framework_reference"
    return "company_policy"


def pack_id_of(collection: str) -> str | None:
    if collection.startswith(PACK_COLLECTION_PREFIX):
        return collection[len(PACK_COLLECTION_PREFIX) :]
    return None


async def _make_readable(repo: VectorRepository, profile: EmbeddingProfile | None) -> bool:
    """Open `repo` for reading (never creating anything). False if it has no index file.

    With a `profile` the index is upgraded to a full connection, which checks that it was built
    with this embedding model (a different model means a different vector space).
    """
    if repo.mode == "closed" and not await repo.open_for_deletions():
        return False
    if profile is not None and repo.mode == "deletions_only":
        await repo.open_or_create(profile)
    return True


class Retriever:
    def __init__(
        self,
        *,
        vault_repo: VectorRepository,
        indexer: StalenessRefresher,
        embedder: EmbeddingClient,
        gate: ModelGate,
        embedding_model: str,
        clock: Clock,
        packs_repo: VectorRepository | None = None,
    ) -> None:
        self._vault = vault_repo
        self._packs = packs_repo
        self._indexer = indexer
        self._embedder = embedder
        self._gate = gate
        self._embedding_model = embedding_model
        self._clock = clock
        self._profile: EmbeddingProfile | None = None
        # One question at a time: opening an index and the freshness check must not interleave.
        self._lock = asyncio.Lock()

    async def retrieve(self, query: str, *, limit: int = DEFAULT_LIMIT) -> RetrievalResult:
        """Answer one question. Raises IndexNotBuiltError, RetrievalUnavailableError, or a store
        or embedding error that needs a human (profile mismatch, wrong dimension)."""
        if not query.strip():
            raise ValueError("the question is empty")
        if limit < 1:
            raise ValueError("limit must be at least 1")
        async with self._lock:
            return await self._retrieve(query[:MAX_QUERY_CHARS], limit)

    # ----- the steps -------------------------------------------------------------------------

    async def _refresh_vault(self) -> tuple[str, ...]:
        """Step 1. Returns the documents withheld because their file changed after indexing."""
        failed = False
        found = False
        try:
            found = (await self._indexer.refresh_staleness()).index_found
        except GrcError:
            failed = True
        # Raised after the `except` block, so nothing about the failure is chained to the error.
        if failed:
            raise RetrievalUnavailableError
        if not found:
            raise IndexNotBuiltError
        states = await self._vault.document_states()
        return tuple(sorted(path for path, state in states.items() if state.stale))

    async def _embed(
        self, query: str
    ) -> tuple[list[float] | None, EmbeddingProfile | None, DegradedReason | None]:
        """Step 3, the model side: the query vector, or the reason there is none."""
        reason: DegradedReason | None = None
        try:
            await self._gate.require(self._embedding_model)
            if self._profile is None:
                self._profile = await self._embedder.ensure_ready()
            return await self._embedder.embed_query(query), self._profile, None
        except ModelsUnavailableError:
            self._profile = None
            reason = (
                "embedding_unavailable"
                if self._gate.status.ollama != "reachable"
                else "embedding_model_missing"
            )
        except ModelMissingError:
            self._profile = None
            reason = "embedding_model_missing"
        except EmbeddingUnavailableError:
            self._profile = None
            reason = "embedding_unavailable"
        return None, None, reason

    async def _search_store(
        self,
        repo: VectorRepository,
        match: str,
        vector: list[float] | None,
        profile: EmbeddingProfile | None,
    ) -> tuple[list[Fused], dict[str, DocInfo]]:
        nearest: list[ScoredChunk] = []
        if vector is not None:
            # Checks the index was built with this embedding model before its vectors are used.
            await _make_readable(repo, profile)
            nearest = await repo.search(vector, CANDIDATES_PER_LIST)
        lexical: list[LexicalHit] = await repo.lexical_search(match, CANDIDATES_PER_LIST)
        fused = fuse(lexical, nearest)
        info = await repo.documents_info([item.chunk.source_path for item in fused])
        return fused, info

    async def _retrieve(self, query: str, limit: int) -> RetrievalResult:
        withheld = await self._refresh_vault()
        lexical_query = build_lexical_query(query)
        vector, profile, reason = await self._embed(query)

        stores: list[VectorRepository] = [self._vault]
        packs_indexed = True
        if self._packs is not None:
            packs_indexed = await _make_readable(self._packs, None)
            if packs_indexed:
                stores.append(self._packs)

        candidates: list[tuple[Fused, DocInfo]] = []
        for repo in stores:
            fused, info = await self._search_store(repo, lexical_query.match, vector, profile)
            for item in fused:
                document = info.get(item.chunk.source_path)
                if document is None:
                    raise RetrievalUnavailableError  # a chunk with no document row: damaged index
                candidates.append((item, document))
        candidates.sort(key=lambda pair: (-pair[0].score, pair[0].chunk.chunk_id))

        today = self._clock.today()
        chunks = tuple(
            self._to_chunk(item, document, today) for item, document in candidates[:limit]
        )
        return RetrievalResult(
            query=query,
            mode="hybrid" if vector is not None else "lexical_only",
            degraded_reason=reason,
            chunks=chunks,
            withheld_unindexed=withheld,
            packs_indexed=packs_indexed,
        )

    @staticmethod
    def _to_chunk(item: Fused, document: DocInfo, today: date) -> RetrievedChunk:
        chunk = item.chunk
        review_by = date.fromisoformat(document.review_by) if document.review_by else None
        return RetrievedChunk(
            chunk_id=chunk.chunk_id,
            source_path=chunk.source_path,
            doc_id=chunk.doc_id,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            heading_path=chunk.heading_path,
            text=chunk.text,
            text_sha256=chunk.text_sha256,
            source_kind=source_kind_of(document.collection, document.type),
            pack_id=pack_id_of(document.collection),
            doc_type=document.type,
            role="seed",
            expanded_from=(),
            review_by=review_by,
            review_overdue=is_stale(review_by, today),
            withdrawn=False,
            fused_score=float(item.score),
            lexical_rank=item.lexical_rank,
            vector_rank=item.vector_rank,
        )
