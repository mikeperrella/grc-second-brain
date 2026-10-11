"""Hybrid retrieval end to end, offline (ADR-010, M4e).

The vault is the synthetic sample vault ("Halcyon Ridge"); the embedder is the deterministic fake;
the model listing is fake. See `retrievalkit.py`. Nothing here needs Ollama.
"""

import asyncio
import json
import shutil
from datetime import date
from pathlib import Path

import httpx
import pytest

from grc_sidecar.errors import (
    IndexNotBuiltError,
    IndexProfileMismatchError,
    IndexStoreError,
    RetrievalUnavailableError,
)
from grc_sidecar.llm.ollama_embeddings import OllamaEmbeddingClient
from grc_sidecar.repositories.sqlite_vec_repo import SqliteVecRepository
from grc_sidecar.retrieval.models import RetrievalResult
from grc_sidecar.retrieval.service import Retriever

from .fakes import FakeEmbeddingClient, vector_for
from .retrievalkit import EMBEDDING, stack, write_pack
from .vaultkit import ADVERSARIAL_VAULT, REPO_ROOT, SAMPLE_VAULT

PACKS = REPO_ROOT / "packs"
MFA = "access-control-and-mfa-policy.md"
VENDOR = "vendor-management-policy.md"
RETENTION = "data-retention-policy.md"


def paths_of(result: RetrievalResult) -> list[str]:
    return [chunk.source_path for chunk in result.chunks]


@pytest.fixture
def index_dir(tmp_path: Path) -> Path:
    return tmp_path / "index"


# ----- the basics --------------------------------------------------------------------------------


def test_a_question_returns_cited_company_chunks(index_dir: Path) -> None:
    async def scenario() -> None:
        async with stack(SAMPLE_VAULT, index_dir) as s:
            result = await s.retriever.retrieve("multi-factor authentication")
            assert MFA in paths_of(result)
            assert result.mode == "hybrid"
            assert result.degraded_reason is None
            assert result.withheld_unindexed == ()
            for chunk in result.chunks:
                assert chunk.source_kind == "company_policy"
                assert chunk.pack_id is None
                assert chunk.role == "seed"
                assert chunk.expanded_from == ()
                assert 1 <= chunk.start_line <= chunk.end_line
                # a citation can be checked: the text IS the file's lines start..end
                lines = (SAMPLE_VAULT / chunk.source_path).read_text(encoding="utf-8").splitlines()
                assert "\n".join(lines[chunk.start_line - 1 : chunk.end_line]) == chunk.text
                # and it is exactly what the index holds
                stored = {c.chunk_id: c for c in await s.vault_repo.chunks_for(chunk.source_path)}
                assert stored[chunk.chunk_id].text == chunk.text

    asyncio.run(scenario())


def test_both_searches_contribute(index_dir: Path) -> None:
    async def scenario() -> None:
        async with stack(SAMPLE_VAULT, index_dir) as s:
            result = await s.retriever.retrieve("multi-factor authentication")
            assert any(c.lexical_rank and c.vector_rank for c in result.chunks)
            assert all(c.lexical_rank or c.vector_rank for c in result.chunks)
            scores = [c.fused_score for c in result.chunks]
            assert scores == sorted(scores, reverse=True)

    asyncio.run(scenario())


def test_the_same_question_gives_byte_identical_output(index_dir: Path, tmp_path: Path) -> None:
    async def once(directory: Path) -> str:
        async with stack(SAMPLE_VAULT, directory) as s:
            first = (await s.retriever.retrieve("log retention")).model_dump_json()
            second = (await s.retriever.retrieve("log retention")).model_dump_json()
            assert first == second
            return first

    # two separate indexes (separate processes' worth of state) agree as well
    assert asyncio.run(once(index_dir)) == asyncio.run(once(tmp_path / "second-index"))


def test_limit_is_honoured_and_validated(index_dir: Path) -> None:
    async def scenario() -> None:
        async with stack(SAMPLE_VAULT, index_dir) as s:
            assert len((await s.retriever.retrieve("policy", limit=2)).chunks) == 2
            with pytest.raises(ValueError, match="limit"):
                await s.retriever.retrieve("policy", limit=0)
            for empty in ("", "   ", "\n"):
                with pytest.raises(ValueError, match="empty"):
                    await s.retriever.retrieve(empty)

    asyncio.run(scenario())


def test_nonsense_still_returns_company_text_there_is_no_relevance_floor(  # [NEG]
    index_dir: Path,
) -> None:
    # Pinning a documented limit (ADR-010): vector search returns its nearest rows however far
    # away they are, and M4 sets no threshold. A non-empty result is NOT evidence.
    async def scenario() -> None:
        async with stack(SAMPLE_VAULT, index_dir) as s:
            result = await s.retriever.retrieve("zzqx vortex lemon")
            assert result.chunks
            assert result.mode == "hybrid"
            assert all(c.lexical_rank is None for c in result.chunks)  # no word matched anywhere

    asyncio.run(scenario())


# ----- the vector side can fail, and then the mode says so ---------------------------------------


def test_embedding_failure_reports_lexical_only(index_dir: Path) -> None:  # [NEG] mutation row 16
    async def scenario() -> None:
        async with stack(SAMPLE_VAULT, index_dir) as s:
            s.embedder.unreachable = True
            result = await s.retriever.retrieve("multi-factor authentication")
            assert result.mode == "lexical_only"
            assert result.degraded_reason == "embedding_unavailable"
            assert result.chunks, "keyword results are still returned"
            assert MFA in paths_of(result)
            assert all(c.vector_rank is None for c in result.chunks)

    asyncio.run(scenario())


def test_a_missing_embedding_model_reports_lexical_only_without_calling_it(  # [NEG] row 14
    index_dir: Path,
) -> None:
    async def scenario() -> None:
        async with stack(SAMPLE_VAULT, index_dir, models=("llama3.1:8b",)) as s:
            s.embedder.ready_calls = 0
            result = await s.retriever.retrieve("multi-factor authentication")
            assert result.mode == "lexical_only"
            assert result.degraded_reason == "embedding_model_missing"
            assert s.embedder.ready_calls == 0, "the model was called although it is missing"
            assert result.chunks

    asyncio.run(scenario())


def test_an_unreachable_ollama_is_reported_by_the_gate(index_dir: Path) -> None:  # [NEG]
    async def scenario() -> None:
        async with stack(SAMPLE_VAULT, index_dir, models=None) as s:
            s.embedder.ready_calls = 0
            result = await s.retriever.retrieve("multi-factor authentication")
            assert result.mode == "lexical_only"
            assert result.degraded_reason == "embedding_unavailable"
            assert s.embedder.ready_calls == 0

    asyncio.run(scenario())


def test_a_model_missing_error_from_the_embedder_is_reported(index_dir: Path) -> None:  # [NEG]
    async def scenario() -> None:
        async with stack(SAMPLE_VAULT, index_dir) as s:
            s.embedder.model_missing = True
            result = await s.retriever.retrieve("multi-factor authentication")
            assert (result.mode, result.degraded_reason) == (
                "lexical_only",
                "embedding_model_missing",
            )

    asyncio.run(scenario())


def test_the_vector_side_comes_back_when_the_model_does(index_dir: Path) -> None:
    async def scenario() -> None:
        async with stack(SAMPLE_VAULT, index_dir) as s:
            s.embedder.unreachable = True
            assert (await s.retriever.retrieve("log retention")).mode == "lexical_only"
            s.embedder.unreachable = False
            assert (await s.retriever.retrieve("log retention")).mode == "hybrid"

    asyncio.run(scenario())


def test_a_changed_embedding_model_is_an_error_not_a_downgrade(  # [NEG]
    index_dir: Path,
) -> None:
    # The index was built with one model build; the question is embedded by another. Their
    # vectors are in different spaces, so this is not silently turned into keyword-only.
    async def scenario() -> None:
        old = FakeEmbeddingClient(digest="sha256:old-build")
        new = FakeEmbeddingClient(digest="sha256:new-build")
        async with stack(SAMPLE_VAULT, index_dir, embedder=new, index_embedder=old) as s:
            with pytest.raises(IndexProfileMismatchError):
                await s.retriever.retrieve("log retention")

    asyncio.run(scenario())


# ----- freshness ---------------------------------------------------------------------------------


def test_no_index_is_a_typed_error(index_dir: Path) -> None:  # [NEG]
    async def scenario() -> None:
        async with stack(SAMPLE_VAULT, index_dir, sync=False) as s:
            with pytest.raises(IndexNotBuiltError):
                await s.retriever.retrieve("log retention")

    asyncio.run(scenario())


def test_edited_file_is_never_served_after_retrieval(  # [NEG] mutation row 12
    index_dir: Path, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    shutil.copytree(SAMPLE_VAULT, vault)

    async def scenario() -> None:
        async with stack(vault, index_dir) as s:
            # Open the index here, so the only thing that can notice the edit is the retriever's
            # own freshness check (otherwise a missing check would fail on "index not open").
            assert await s.vault_repo.open_for_deletions()
            before = await s.retriever.retrieve("security logs retention 90 days")
            assert RETENTION in paths_of(before), "the fixture must find the file before the edit"
            # The file is edited AFTER the index was built. Retrieval (not this test) must notice.
            path = vault / RETENTION
            path.write_text(
                path.read_text(encoding="utf-8").replace("90 days", "999 days"), encoding="utf-8"
            )
            after = await s.retriever.retrieve("security logs retention 90 days")
            assert RETENTION not in paths_of(after)
            assert not any("90 days" in c.text for c in after.chunks if c.source_path == RETENTION)
            assert after.withheld_unindexed == (RETENTION,)

    asyncio.run(scenario())


def test_refresh_failure_raises_and_returns_nothing(  # [NEG] mutation row 13
    index_dir: Path,
) -> None:
    async def scenario() -> None:
        async with stack(SAMPLE_VAULT, index_dir) as s:
            assert await s.vault_repo.open_for_deletions()  # open it here, not via the retriever
            assert (await s.retriever.retrieve("log retention")).chunks  # a valid index exists
            s.embedder.ready_calls = 0

            async def broken(*_args: object) -> None:
                raise IndexStoreError

            s.vault_repo.begin_sync = broken  # type: ignore[method-assign,assignment]
            with pytest.raises(RetrievalUnavailableError):
                await s.retriever.retrieve("log retention")
            assert s.embedder.ready_calls == 0, "nothing past the failed freshness check ran"

    asyncio.run(scenario())


def test_an_unusable_vault_folder_fails_closed(index_dir: Path, tmp_path: Path) -> None:  # [NEG]
    vault = tmp_path / "vault"
    shutil.copytree(SAMPLE_VAULT, vault)

    async def scenario() -> None:
        async with stack(vault, index_dir) as s:
            shutil.rmtree(vault)
            with pytest.raises(RetrievalUnavailableError) as info:
                await s.retriever.retrieve("log retention")
            assert info.value.__cause__ is None and info.value.__context__ is None

    asyncio.run(scenario())


# ----- review dates ------------------------------------------------------------------------------


def test_vendor_policy_is_flagged_overdue(index_dir: Path) -> None:  # [NEG] mutation row 17
    async def scenario() -> None:
        async with stack(SAMPLE_VAULT, index_dir, today=date(2026, 10, 10)) as s:
            result = await s.retriever.retrieve("vendor review", limit=10)
            by_path = {c.source_path: c for c in result.chunks}
            assert VENDOR in by_path, "the vendor policy must be retrieved for this test"
            vendor = by_path[VENDOR]
            assert vendor.review_by == date(2024, 6, 30)
            assert vendor.review_overdue is True
            others = [c for c in result.chunks if c.source_path != VENDOR]
            assert not any(c.review_overdue for c in others), "the others are reviewed to 2028"

    asyncio.run(scenario())


def test_the_review_date_itself_is_not_overdue_the_day_after_is(index_dir: Path) -> None:
    async def scenario() -> None:
        async with stack(SAMPLE_VAULT, index_dir, today=date(2024, 6, 30)) as s:
            result = await s.retriever.retrieve("vendor review", limit=10)
            vendor = [c for c in result.chunks if c.source_path == VENDOR]
            assert vendor, "the vendor policy must be retrieved for this test"
            assert not any(c.review_overdue for c in vendor)  # due today is not overdue
            s.clock.fixed = date(2024, 7, 1)
            result = await s.retriever.retrieve("vendor review", limit=10)
            assert all(c.review_overdue for c in result.chunks if c.source_path == VENDOR)

    asyncio.run(scenario())


# ----- where a chunk came from -------------------------------------------------------------------


def _vault_with(tmp_path: Path, name: str, doc_type: str, body: str) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / name).write_text(
        f"---\ndoc_id: X-{doc_type.upper()}\ntype: {doc_type}\nowner: Test Owner\n"
        f"review_by: 2028-01-01\n---\n# Title\n\n{body}\n",
        encoding="utf-8",
        newline="\n",
    )
    return vault


def _packs_with_csf(tmp_path: Path) -> Path:
    packs = tmp_path / "packs"
    shutil.copytree(PACKS / "nist-csf-2.0", packs / "nist-csf-2.0")
    return packs


def test_pack_chunks_are_framework_reference(  # [NEG] mutation row 3
    index_dir: Path, tmp_path: Path
) -> None:
    async def scenario() -> None:
        packs = _packs_with_csf(tmp_path)
        async with stack(SAMPLE_VAULT, index_dir, packs_dir=packs) as s:
            result = await s.retriever.retrieve("Identities and credentials for authorized users")
            framework = [c for c in result.chunks if c.source_kind == "framework_reference"]
            assert framework, "a CSF chunk must be retrieved for this test"
            for chunk in framework:
                assert chunk.pack_id == "nist-csf-2.0"
                assert chunk.doc_type == "framework"
                assert chunk.review_by is None and not chunk.review_overdue
            assert result.packs_indexed
            company = [c for c in result.chunks if c.source_kind == "company_policy"]
            assert all(c.pack_id is None for c in company)

    asyncio.run(scenario())


def test_a_pack_chunk_is_framework_reference_whatever_its_document_type(  # [NEG] row 3
    index_dir: Path, tmp_path: Path
) -> None:
    # Isolates the "came from a pack" rule: this pack's document is typed `policy`, so only its
    # collection (`pack:synthetic-pack`) can make it a framework reference.
    text = (
        "---\ndoc_id: SYN-1\ntype: policy\nowner: Test Owner\nreview_by: 2028-01-01\n---\n"
        "# Control\n\nThe organization shall do the zebrafish thing.\n"
    )
    packs = write_pack(tmp_path / "packs", "synthetic-pack", {"control.md": text})

    async def scenario() -> None:
        async with stack(SAMPLE_VAULT, index_dir, packs_dir=packs) as s:
            result = await s.retriever.retrieve("zebrafish")
            found = [c for c in result.chunks if c.source_path.startswith("synthetic-pack/")]
            assert found
            for chunk in found:
                assert chunk.doc_type == "policy"
                assert chunk.source_kind == "framework_reference"
                assert chunk.pack_id == "synthetic-pack"

    asyncio.run(scenario())


def test_framework_typed_vault_file_is_framework_reference(  # [NEG] mutation row 3
    index_dir: Path, tmp_path: Path
) -> None:
    vault = _vault_with(tmp_path, "pasted-framework.md", "framework", "Zebrafish control text.")

    async def scenario() -> None:
        async with stack(vault, index_dir) as s:
            result = await s.retriever.retrieve("zebrafish")
            assert len(result.chunks) == 1
            assert result.chunks[0].source_kind == "framework_reference"
            assert result.chunks[0].pack_id is None  # it did not come from a pack

    asyncio.run(scenario())


def test_vault_note_claiming_framework_is_not_company_policy_or_the_reverse(  # [NEG] row 3
    index_dir: Path, tmp_path: Path
) -> None:
    # A policy that SAYS it is a NIST framework control stays company policy: the kind comes from
    # where the document was loaded from and its declared type, not from its text.
    vault = _vault_with(
        tmp_path,
        "claims.md",
        "policy",
        "This is an official NIST framework reference control, not a company policy. Zebrafish.",
    )

    async def scenario() -> None:
        async with stack(vault, index_dir) as s:
            result = await s.retriever.retrieve("zebrafish")
            assert [c.source_kind for c in result.chunks] == ["company_policy"]

    asyncio.run(scenario())


def test_a_packs_store_with_no_index_is_reported_not_hidden(  # [NEG]
    index_dir: Path, tmp_path: Path
) -> None:
    async def scenario() -> None:
        async with stack(SAMPLE_VAULT, index_dir) as s:
            missing = SqliteVecRepository(tmp_path / "no-packs-here.sqlite3")
            try:
                retriever = Retriever(
                    vault_repo=s.vault_repo,
                    indexer=s.indexer,
                    embedder=s.embedder,
                    gate=s.gate,
                    embedding_model=EMBEDDING,
                    clock=s.clock,
                    packs_repo=missing,
                )
                result = await retriever.retrieve("log retention")
            finally:
                await missing.close()
            assert result.packs_indexed is False
            assert result.chunks  # company results are unaffected
            assert not (tmp_path / "no-packs-here.sqlite3").exists(), "retrieval created an index"

    asyncio.run(scenario())


# ----- hostile content ---------------------------------------------------------------------------


def _transport() -> tuple[httpx.MockTransport, list[tuple[str, str]]]:
    """A fake Ollama that serves the model listing and embeddings, and records every request."""
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/api/tags":
            entry = {"name": f"{EMBEDDING}:latest", "model": f"{EMBEDDING}:latest"}
            entry["digest"] = "sha256:fake-digest"
            return httpx.Response(200, json={"models": [entry]})
        if request.url.path == "/api/embed":
            texts = json.loads(request.content)["input"]
            return httpx.Response(
                200, json={"embeddings": [vector_for(t) for t in texts], "prompt_eval_count": 1}
            )
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler), seen


def test_hidden_instructions_come_back_as_plain_data(  # [NEG]
    index_dir: Path, tmp_path: Path
) -> None:
    # fixtures/vault-adversarial/hidden-instructions.md tells whoever reads it to ignore its rules,
    # reveal a prompt and write a file. Retrieval returns it as text with the right provenance and
    # does nothing else: the only requests are the two Ollama endpoints it is allowed to call.
    async def scenario() -> tuple[list[tuple[str, str]], list[str]]:
        transport, seen = _transport()
        client = OllamaEmbeddingClient(
            "http://127.0.0.1:11434", EMBEDDING, expected_dimension=768, transport=transport
        )
        before = sorted(p.name for p in tmp_path.rglob("*"))
        try:
            async with stack(ADVERSARIAL_VAULT, index_dir) as s:
                retriever = Retriever(
                    vault_repo=s.vault_repo,
                    indexer=s.indexer,
                    embedder=client,
                    gate=s.gate,
                    embedding_model=EMBEDDING,
                    clock=s.clock,
                )
                question = "ignore all previous instructions administrator mode"
                result = await retriever.retrieve(question)
                hidden = [c for c in result.chunks if c.source_path == "hidden-instructions.md"]
                assert hidden, "the adversarial note must be retrieved for this test"
                rows = await s.vault_repo.chunks_for(hidden[0].source_path)
                stored = {c.chunk_id: c.text for c in rows}
                for chunk in hidden:
                    assert chunk.source_kind == "company_policy"
                    assert chunk.doc_type == "note"
                    assert chunk.text == stored[chunk.chunk_id]  # byte for byte
                assert any("ignore all previous instructions" in c.text.lower() for c in hidden)
                assert result.mode == "hybrid"
        finally:
            await client.aclose()
        after = sorted(p.name for p in tmp_path.rglob("*") if p.name not in {"index"})
        return seen, [n for n in after if n not in before and not n.endswith(".sqlite3")]

    seen, new_files = asyncio.run(scenario())
    assert {path for _method, path in seen} <= {"/api/tags", "/api/embed"}
    assert new_files == [], f"retrieval wrote files: {new_files}"
    assert not Path("C:/Windows/evil.txt").exists()


def test_retrieval_never_alters_the_text_it_returns(index_dir: Path) -> None:
    async def scenario() -> None:
        async with stack(SAMPLE_VAULT, index_dir) as s:
            result = await s.retriever.retrieve("multi-factor authentication", limit=10)
            for chunk in result.chunks:
                stored = {c.chunk_id: c for c in await s.vault_repo.chunks_for(chunk.source_path)}
                assert chunk.text == stored[chunk.chunk_id].text
                assert chunk.text_sha256 == stored[chunk.chunk_id].text_sha256

    asyncio.run(scenario())
