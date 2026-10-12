"""Hybrid retrieval: keyword and vector search over the vault and the framework packs (M4e, M4f).

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
5. Withdrawn framework controls are left out, except a stub named by an identifiers-only question
   (withdrawn.py).
6. Seeds are chosen within the slots, the character budget and the framework quota
   (selection.py); then one expanded chunk is added by following an explicit link from a company
   seed (expansion.py) to company text only, or the slot goes to the next company seed when no
   link qualifies.

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
from grc_sidecar.retrieval.expansion import Expansion, expansion_candidates
from grc_sidecar.retrieval.fusion import Fused, fuse
from grc_sidecar.retrieval.models import (
    BudgetReport,
    ChunkRole,
    DegradedReason,
    RetrievalResult,
    RetrievedChunk,
    SourceKind,
)
from grc_sidecar.retrieval.ordering import order_candidates
from grc_sidecar.retrieval.query import MAX_QUERY_CHARS, LexicalQuery, build_lexical_query
from grc_sidecar.retrieval.selection import (
    DEFAULT_CHAR_BUDGET,
    DEFAULT_EXPANSION_DEPTH,
    DEFAULT_MAX_CHUNKS,
    MAX_EXPANSION_DEPTH,
    Candidate,
    expansion_slots,
    select_seeds,
)
from grc_sidecar.retrieval.withdrawn import is_withdrawn_section, names_a_stub
from grc_sidecar.vault.clock import Clock, is_stale
from grc_sidecar.vault.indexer import RefreshReport
from grc_sidecar.vault.models import Chunk

PACK_COLLECTION_PREFIX = "pack:"
CANDIDATES_PER_LIST = 20  # how many results each of the two searches contributes to the merge


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


def _to_chunk(candidate: Candidate, today: date) -> RetrievedChunk:
    chunk, document = candidate.item.chunk, candidate.document
    return _chunk_of(
        chunk,
        document,
        today,
        role="seed",
        expanded_from=(),
        withdrawn=candidate.withdrawn,
        score=float(candidate.item.score),
        lexical_rank=candidate.item.lexical_rank,
        vector_rank=candidate.item.vector_rank,
    )


def _chunk_of(
    chunk: Chunk,
    document: DocInfo,
    today: date,
    *,
    role: ChunkRole,
    expanded_from: tuple[str, ...],
    withdrawn: bool,
    score: float,
    lexical_rank: int | None,
    vector_rank: int | None,
) -> RetrievedChunk:
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
        file_sha256=document.file_sha256,
        source_kind=source_kind_of(document.collection, document.type),
        pack_id=pack_id_of(document.collection),
        doc_type=document.type,
        role=role,
        expanded_from=expanded_from,
        review_by=review_by,
        review_overdue=is_stale(review_by, today),
        withdrawn=withdrawn,
        fused_score=score,
        lexical_rank=lexical_rank,
        vector_rank=vector_rank,
    )


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

    async def retrieve(
        self,
        query: str,
        *,
        max_chunks: int = DEFAULT_MAX_CHUNKS,
        char_budget: int = DEFAULT_CHAR_BUDGET,
        expansion_depth: int = DEFAULT_EXPANSION_DEPTH,
    ) -> RetrievalResult:
        """Answer one question. Raises IndexNotBuiltError, RetrievalUnavailableError, or a store
        or embedding error that needs a human (profile mismatch, wrong dimension).

        `max_chunks` and `char_budget` are the context budget (selection.py explains the defaults);
        `expansion_depth` is how many link levels may be followed (0 turns expansion off).
        """
        if not query.strip():
            raise ValueError("the question is empty")
        if max_chunks < 1 or char_budget < 1:
            raise ValueError("max_chunks and char_budget must be at least 1")
        if not 0 <= expansion_depth <= MAX_EXPANSION_DEPTH:
            raise ValueError(f"expansion_depth must be between 0 and {MAX_EXPANSION_DEPTH}")
        async with self._lock:
            return await self._retrieve(
                query[:MAX_QUERY_CHARS], max_chunks, char_budget, expansion_depth
            )

    # ----- the steps -------------------------------------------------------------------------

    async def vault_withheld(self) -> tuple[str, ...]:
        """The vault documents that changed after they were indexed (sorted source paths).

        Runs the same freshness check a question starts with, so it also refreshes what it can.
        A questionnaire run asks this before it starts and refuses if anything is withheld
        (ADR-014): answers must not be drafted from an index that is known to be behind the vault.
        Raises IndexNotBuiltError or RetrievalUnavailableError like `retrieve`.
        """
        async with self._lock:
            return await self._refresh_vault()

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

    @staticmethod
    def _candidate(item: Fused, document: DocInfo, query: LexicalQuery) -> Candidate | None:
        """Step 5. None for a withdrawn framework stub that was not asked for by name."""
        kind = source_kind_of(document.collection, document.type)
        withdrawn = False
        if kind == "framework_reference" and is_withdrawn_section(item.chunk.heading_path):
            asked_for = query.identifiers_only and names_a_stub(
                item.chunk.text, query.identifier_forms
            )
            if not asked_for:
                return None
            withdrawn = True
        return Candidate(item=item, document=document, kind=kind, withdrawn=withdrawn)

    async def _expand(
        self,
        seeds: tuple[Candidate, ...],
        *,
        depth: int,
        room: int,
        dropped: list[str],
    ) -> Expansion | None:
        """The first linked chunk that is company text and fits in `room` characters, or None.

        A link into a document whose derived kind is framework (a vault file typed `framework`) is
        never taken: the expansion slot is outside the framework cap, so it must hold company text
        only. The walk itself still passes through such a document (ADR-010).
        """
        company = [c.item.chunk for c in seeds if c.kind == "company_policy"]
        if depth < 1 or not company:
            return None
        found_all = await expansion_candidates(self._vault, company, depth=depth)
        documents = await self._vault.documents_info(
            list(dict.fromkeys(f.chunk.source_path for f in found_all))
        )
        for found in found_all:
            document = documents.get(found.chunk.source_path)
            if document is None:
                raise RetrievalUnavailableError  # a chunk with no document row: damaged index
            if source_kind_of(document.collection, document.type) != "company_policy":
                continue
            if len(found.chunk.text) <= room:
                return found
            dropped.append(found.chunk.chunk_id)
        return None

    async def _retrieve(
        self, query: str, max_chunks: int, char_budget: int, depth: int
    ) -> RetrievalResult:
        withheld = await self._refresh_vault()
        lexical_query = build_lexical_query(query)
        vector, profile, reason = await self._embed(query)

        stores: list[VectorRepository] = [self._vault]
        packs_indexed = True
        if self._packs is not None:
            packs_indexed = await _make_readable(self._packs, None)
            if packs_indexed:
                stores.append(self._packs)

        ranked: list[Candidate] = []
        for repo in stores:
            fused, info = await self._search_store(repo, lexical_query.match, vector, profile)
            for item in fused:
                document = info.get(item.chunk.source_path)
                if document is None:
                    raise RetrievalUnavailableError  # a chunk with no document row: damaged index
                candidate = self._candidate(item, document, lexical_query)
                if candidate is not None:
                    ranked.append(candidate)
        ranked = order_candidates(ranked, lexical_query)

        selection = select_seeds(ranked, max_chunks=max_chunks, char_budget=char_budget)
        today = self._clock.today()
        chunks = [_to_chunk(candidate, today) for candidate in selection.seeds]
        used = selection.used_chars
        framework_chars = selection.framework_chars
        dropped = list(selection.dropped_for_budget)

        if expansion_slots(max_chunks):
            linked = await self._expand(
                selection.seeds, depth=depth, room=char_budget - used, dropped=dropped
            )
            if linked is not None:
                info = await self._vault.documents_info([linked.chunk.source_path])
                document = info.get(linked.chunk.source_path)
                if document is None:
                    raise RetrievalUnavailableError
                chunks.append(
                    _chunk_of(
                        linked.chunk,
                        document,
                        today,
                        role="expanded",
                        expanded_from=(linked.root_seed,),
                        withdrawn=False,
                        score=0.0,  # not a search result: reached by a link, not ranked
                        lexical_rank=None,
                        vector_rank=None,
                    )
                )
                used += len(linked.chunk.text)
            else:
                # No qualifying link: the reserved slot goes to the next company seed that fits.
                chosen = {c.item.chunk.chunk_id for c in selection.seeds}
                for candidate in ranked:
                    if (
                        candidate.kind != "company_policy"
                        or candidate.item.chunk.chunk_id in chosen
                    ):
                        continue
                    if used + candidate.size > char_budget:
                        dropped.append(candidate.item.chunk.chunk_id)
                        continue
                    chunks.append(_to_chunk(candidate, today))
                    used += candidate.size
                    break

        return RetrievalResult(
            query=query,
            mode="hybrid" if vector is not None else "lexical_only",
            degraded_reason=reason,
            chunks=tuple(chunks),
            budget=BudgetReport(
                max_chunks=max_chunks,
                char_budget=char_budget,
                used_chars=used,
                framework_chars=framework_chars,
                dropped_for_budget=tuple(dict.fromkeys(dropped)),
            ),
            withheld_unindexed=withheld,
            packs_indexed=packs_indexed,
        )
