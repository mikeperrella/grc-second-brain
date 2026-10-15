"""The vault index routes (ADR-021 decision 6, chunk 6b): start a job, poll it.

Offline: a fake embedder (no Ollama), a fake model gate, the real indexer and `sqlite-vec`
repository in `tmp_path`, the real shared job slot. A first real build against Ollama is a live
check run by the owner (ADR-021, Consequences). All data is synthetic.
"""

import asyncio
import hashlib
import logging
import re
import tempfile
import threading
from collections.abc import AsyncIterator, Awaitable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from grc_sidecar.config import Settings
from grc_sidecar.errors import ModelsUnavailableError
from grc_sidecar.index_runtime import IndexRuntime, build_index_runtime
from grc_sidecar.jobs import JobSlot
from grc_sidecar.main import create_app
from grc_sidecar.repositories.sqlite_vec_repo import SqliteVecRepository
from grc_sidecar.security.net import create_async_client
from grc_sidecar.vault.index_jobs import KEPT_JOBS, IndexJob, IndexJobs
from grc_sidecar.vault.indexer import Indexer, SyncReport

from .fakes import FakeEmbeddingClient
from .indexkit import write_doc
from .runskit import CANARY, FakeGate

pytestmark = pytest.mark.usefixtures("clean_grc_env")

TOKEN = hashlib.sha256(b"grc-test-token").hexdigest()
AUTH = {"X-GRC-Auth": TOKEN}
JOB_ID = re.compile(r"[0-9a-f]{32}")
EMBEDDING = "nomic-embed-text"
STATUS_KEYS = {
    "job_id",
    "status",
    "files_done",
    "files_total",
    "files_indexed",
    "files_unchanged",
    "files_removed",
    "files_failed",
    "chunks_embedded",
    "failure",
}


class GatedEmbedder(FakeEmbeddingClient):
    """Holds every embedding call until `release` is set, so a test can look at a running job."""

    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()
        self.entered = asyncio.Event()

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.entered.set()
        await self.release.wait()
        return await super().embed_documents(texts)


@dataclass
class Rig:
    vault: Path
    index: Path
    embedder: FakeEmbeddingClient
    gate: FakeGate
    slot: JobSlot
    jobs: IndexJobs
    repo: SqliteVecRepository


def make_vault(root: Path, count: int = 3) -> Path:
    vault = root / "vault"
    for n in range(1, count + 1):
        write_doc(vault, f"note-{n}.md", f"# Note {n}\n\nAccess is reviewed quarterly, item {n}.\n")
    return vault


def rig(
    root: Path,
    *,
    embedder: FakeEmbeddingClient | None = None,
    gate: FakeGate | None = None,
    count: int = 3,
) -> Rig:
    vault = make_vault(root, count)
    index = root / "index" / "vault.sqlite3"
    index.parent.mkdir()
    embedder = embedder or FakeEmbeddingClient()
    gate = gate or FakeGate()
    slot = JobSlot()
    repo = SqliteVecRepository(index)
    jobs = IndexJobs(Indexer(repo, embedder, vault), slot, gate, EMBEDDING)
    return Rig(vault, index, embedder, gate, slot, jobs, repo)


@asynccontextmanager
async def client_for(jobs: IndexJobs | None) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(TOKEN, index_jobs=jobs)
    async with create_async_client(
        "http://127.0.0.1:8000", transport=httpx.ASGITransport(app=app)
    ) as client:
        yield client


async def wait_until_finished(client: httpx.AsyncClient, job_id: str) -> dict[str, object]:
    for _ in range(500):
        body = (await client.get(f"/vault/index/{job_id}", headers=AUTH)).json()
        if body["status"] != "running":
            return dict(body)
        await asyncio.sleep(0.01)
    raise AssertionError("the job did not finish")


def tree(folder: Path) -> dict[str, str]:
    return {
        str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


def run(scenario: Awaitable[None]) -> None:
    asyncio.run(asyncio.wait_for(scenario, 60))  # type: ignore[arg-type]


# ----- the happy path ---------------------------------------------------------------------------


def test_start_answers_202_with_a_job_id_and_the_job_runs_to_complete(tmp_path: Path) -> None:
    r = rig(tmp_path)

    async def scenario() -> None:
        async with client_for(r.jobs) as client:
            started = await client.post("/vault/index", headers=AUTH)
            assert started.status_code == 202
            assert set(started.json()) == {"job_id"} and JOB_ID.fullmatch(started.json()["job_id"])
            done = await wait_until_finished(client, started.json()["job_id"])
            assert set(done) == STATUS_KEYS
            assert done["status"] == "complete" and done["failure"] is None
            assert (done["files_done"], done["files_total"]) == (3, 3)
            assert (done["files_indexed"], done["files_unchanged"]) == (3, 0)
            assert (done["files_removed"], done["files_failed"]) == (0, 0)
            assert isinstance(done["chunks_embedded"], int) and done["chunks_embedded"] >= 3
            # A second job finds everything unchanged and embeds nothing.
            second = await client.post("/vault/index", headers=AUTH, json={})
            assert second.status_code == 202
            again = await wait_until_finished(client, second.json()["job_id"])
            assert (again["files_indexed"], again["files_unchanged"]) == (0, 3)
            assert again["chunks_embedded"] == 0 and again["status"] == "complete"
        await r.repo.close()

    run(scenario())
    assert r.gate.required == [(EMBEDDING,), (EMBEDDING,)]  # the embedding model only
    assert r.slot.busy is False


def test_a_running_job_reports_its_progress_and_holds_the_slot(tmp_path: Path) -> None:  # [NEG]
    embedder = GatedEmbedder()
    r = rig(tmp_path, embedder=embedder)

    async def scenario() -> None:
        async with client_for(r.jobs) as client:
            job_id = (await client.post("/vault/index", headers=AUTH)).json()["job_id"]
            await embedder.entered.wait()
            mid = (await client.get(f"/vault/index/{job_id}", headers=AUTH)).json()
            assert mid["status"] == "running" and mid["files_total"] == 3
            assert mid["files_done"] == 0 and mid["files_indexed"] == 0
            assert r.slot.busy is True
            second = await client.post("/vault/index", headers=AUTH)
            assert second.status_code == 409 and second.json() == {"error": "run_in_progress"}
            assert r.slot.busy is True  # a refusal does not free the other job's slot
            embedder.release.set()
            done = await wait_until_finished(client, job_id)
            assert done["status"] == "complete" and done["files_done"] == 3
        await r.repo.close()

    run(scenario())
    assert r.slot.busy is False


def test_a_questionnaire_or_contradiction_run_holding_the_slot_refuses_409(  # [NEG]
    tmp_path: Path,
) -> None:
    r = rig(tmp_path)
    r.slot.begin()  # another heavy job holds the shared slot

    async def scenario() -> None:
        async with client_for(r.jobs) as client:
            response = await client.post("/vault/index", headers=AUTH)
            assert response.status_code == 409
            assert response.json() == {"error": "run_in_progress"}
        await r.repo.close()

    run(scenario())
    assert r.slot.busy is True and r.gate.required == []  # nothing ran, the gate was not asked
    assert not r.index.exists()


# ----- refusals ---------------------------------------------------------------------------------


def test_missing_models_refuse_503_naming_them_and_free_the_slot(tmp_path: Path) -> None:  # [NEG]
    r = rig(tmp_path, gate=FakeGate(ModelsUnavailableError((EMBEDDING,))))

    async def scenario() -> None:
        async with client_for(r.jobs) as client:
            response = await client.post("/vault/index", headers=AUTH)
            assert response.status_code == 503
            assert response.json() == {"error": "models_unavailable", "models_missing": [EMBEDDING]}
        await r.repo.close()

    run(scenario())
    assert r.slot.busy is False and not r.index.exists()


def test_without_a_vault_folder_both_routes_refuse_503() -> None:  # [NEG]
    async def scenario() -> None:
        async with client_for(None) as client:
            start = await client.post("/vault/index", headers=AUTH)
            status = await client.get(f"/vault/index/{'a' * 32}", headers=AUTH)
            for response in (start, status):
                assert response.status_code == 503
                assert response.json() == {"error": "vault_root_not_configured"}

    run(scenario())


def test_an_unknown_job_is_404_and_a_malformed_id_is_422(tmp_path: Path) -> None:  # [NEG]
    r = rig(tmp_path)

    async def scenario() -> None:
        async with client_for(r.jobs) as client:
            unknown = await client.get(f"/vault/index/{'a' * 32}", headers=AUTH)
            assert unknown.status_code == 404 and unknown.json() == {"error": "run_not_found"}
            for bad in ("short", "A" * 32, "g" * 32, "a" * 65, "a" * 7):
                response = await client.get(f"/vault/index/{bad}", headers=AUTH)
                assert response.status_code == 422, bad
        await r.repo.close()

    run(scenario())


def test_the_start_body_takes_no_fields_and_both_routes_need_the_token(  # [NEG]
    tmp_path: Path,
) -> None:
    r = rig(tmp_path)

    async def scenario() -> None:
        async with client_for(r.jobs) as client:
            for body in ({"vault": "/etc"}, {"path": "/etc"}, {"index_dir": "elsewhere"}, [1]):
                response = await client.post("/vault/index", headers=AUTH, json=body)
                assert response.status_code == 422, body
                assert "/etc" not in response.text
            assert (await client.post("/vault/index")).status_code == 401
            wrong = {"X-GRC-Auth": "0" * 64}
            assert (await client.post("/vault/index", headers=wrong)).status_code == 401
            assert (await client.get(f"/vault/index/{'a' * 32}")).status_code == 401
        await r.repo.close()

    run(scenario())
    assert r.slot.busy is False and r.gate.required == []


def test_a_failed_job_reports_a_fixed_code_and_frees_the_slot(tmp_path: Path) -> None:  # [NEG]
    for embedder, code in (
        (FakeEmbeddingClient(unreachable=True), "embedding_unavailable"),
        (FakeEmbeddingClient(model_missing=True), "models_unavailable"),
    ):
        r = rig(tmp_path / code, embedder=embedder)

        async def scenario(r: Rig = r, code: str = code) -> None:
            async with client_for(r.jobs) as client:
                job_id = (await client.post("/vault/index", headers=AUTH)).json()["job_id"]
                done = await wait_until_finished(client, job_id)
                assert done["status"] == "failed" and done["failure"] == code
                assert done["files_indexed"] == 0 and set(done) == STATUS_KEYS
            await r.repo.close()

        run(scenario())
        assert r.slot.busy is False


def test_a_document_that_cannot_be_embedded_makes_the_job_incomplete(tmp_path: Path) -> None:
    r = rig(tmp_path, embedder=FakeEmbeddingClient(fail_marker="item 2."))

    async def scenario() -> None:
        async with client_for(r.jobs) as client:
            job_id = (await client.post("/vault/index", headers=AUTH)).json()["job_id"]
            done = await wait_until_finished(client, job_id)
            assert done["status"] == "incomplete" and done["failure"] is None
            assert (done["files_indexed"], done["files_failed"], done["files_done"]) == (2, 1, 3)
        await r.repo.close()

    run(scenario())


# ----- the last 20 jobs are kept ----------------------------------------------------------------


def test_only_the_last_20_jobs_are_kept_and_an_evicted_id_is_404(tmp_path: Path) -> None:  # [NEG]
    r = rig(tmp_path, count=1)
    ids: list[str] = []

    async def scenario() -> None:
        async with client_for(r.jobs) as client:
            for _ in range(KEPT_JOBS + 3):
                started = await client.post("/vault/index", headers=AUTH)
                assert started.status_code == 202
                ids.append(started.json()["job_id"])
                await wait_until_finished(client, ids[-1])
            for evicted in ids[:3]:
                response = await client.get(f"/vault/index/{evicted}", headers=AUTH)
                assert response.status_code == 404 and response.json() == {"error": "run_not_found"}
            for kept in ids[3:]:
                assert (await client.get(f"/vault/index/{kept}", headers=AUTH)).status_code == 200
        await r.repo.close()

    run(scenario())
    assert len(ids) == KEPT_JOBS + 3 and len(set(ids)) == len(ids)


# ----- threads ----------------------------------------------------------------------------------


def test_job_counters_stay_consistent_when_progress_is_reported_from_other_threads() -> None:
    """The progress callback may run on a worker thread: every snapshot is one whole update."""
    job = IndexJob("a" * 32)
    stop = threading.Event()
    torn: list[tuple[int, int]] = []

    def reader() -> None:
        while not stop.is_set():
            snapshot = job.snapshot()
            if snapshot.files_total != 0 and snapshot.files_total != snapshot.files_done + 1000:
                torn.append((snapshot.files_done, snapshot.files_total))

    def writer(offset: int) -> None:
        for n in range(3000):
            done = (offset * 3000 + n) % 100_000
            job.progress(done, done + 1000)  # the pair always differs by exactly 1000

    watcher = threading.Thread(target=reader)
    writers = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    watcher.start()
    for thread in writers:
        thread.start()
    for thread in writers:
        thread.join()
    stop.set()
    watcher.join()
    assert torn == []
    last = job.snapshot()
    assert last.files_total == last.files_done + 1000 and last.status == "running"


class ThreadedSyncer:
    """Reports progress only from a worker thread, as a future threaded scan would."""

    def __init__(self) -> None:
        self.threads: set[int] = set()

    async def sync(self, on_progress: object = None) -> SyncReport:
        assert callable(on_progress)
        report = on_progress

        def work() -> None:
            self.threads.add(threading.get_ident())
            for done in range(6):
                report(done, 5)

        await asyncio.to_thread(work)
        return SyncReport(
            complete=True,
            documents_seen=5,
            indexed=("a.md",),
            unchanged=4,
            removed=(),
            marked_stale=(),
            failed=(),
            chunks_embedded=7,
            issues=(),
        )


def test_a_progress_callback_called_from_another_thread_is_seen_by_the_job() -> None:
    syncer = ThreadedSyncer()
    slot = JobSlot()
    jobs = IndexJobs(syncer, slot, FakeGate(), EMBEDDING)

    async def scenario() -> None:
        job_id = await jobs.start()
        for _ in range(500):
            if jobs.get(job_id).status != "running":
                break
            await asyncio.sleep(0.01)
        status = jobs.get(job_id)
        assert status.status == "complete"
        assert (status.files_done, status.files_total) == (5, 5)
        assert (status.files_indexed, status.files_unchanged, status.chunks_embedded) == (1, 4, 7)

    run(scenario())
    assert syncer.threads and threading.get_ident() not in syncer.threads
    assert slot.busy is False


def test_the_indexers_progress_callback_counts_every_document() -> None:
    calls: list[tuple[int, int]] = []

    async def scenario(vault: Path, index: Path) -> None:
        repo = SqliteVecRepository(index)
        embedder = FakeEmbeddingClient(fail_marker="item 2.")
        report = await Indexer(repo, embedder, vault).sync(lambda d, t: calls.append((d, t)))
        assert report.complete is False and len(report.failed) == 1
        await repo.close()

    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        asyncio.run(scenario(make_vault(root), root / "index.sqlite3"))
    assert calls == [(0, 3), (1, 3), (2, 3), (3, 3)]  # a failed document is still dealt with


# ----- folders and content ----------------------------------------------------------------------


def test_the_job_reads_only_the_vault_and_writes_only_the_index_folder(
    tmp_path: Path,
) -> None:  # [NEG]
    r = rig(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    (other / "keep.txt").write_text("untouched", encoding="utf-8")
    before_vault, before_other = tree(r.vault), tree(other)
    assert tree(r.index.parent) == {}

    async def scenario() -> None:
        async with client_for(r.jobs) as client:
            job_id = (await client.post("/vault/index", headers=AUTH)).json()["job_id"]
            await wait_until_finished(client, job_id)
        await r.repo.close()

    run(scenario())
    assert tree(r.vault) == before_vault  # the vault is byte-identical
    assert tree(other) == before_other
    assert r.index.exists() and all(
        name.startswith("vault.sqlite3") for name in tree(r.index.parent)
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == ["index", "other", "vault"]


def test_no_vault_content_or_file_name_reaches_a_response_or_a_log(  # [NEG]
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    r = rig(tmp_path, count=1)
    write_doc(r.vault, f"canary-{CANARY}.md", f"# Canary\n\nThe secret is {CANARY}.\n")
    (r.vault / "broken.md").write_text(f"---\nnot: frontmatter {CANARY}\n---\n", encoding="utf-8")
    texts: list[str] = []

    async def scenario() -> None:
        async with client_for(r.jobs) as client:
            started = await client.post("/vault/index", headers=AUTH)
            texts.append(started.text)
            job_id = started.json()["job_id"]
            for _ in range(100):
                polled = await client.get(f"/vault/index/{job_id}", headers=AUTH)
                texts.append(polled.text)
                if polled.json()["status"] != "running":
                    break
                await asyncio.sleep(0.01)
            texts.append((await client.get(f"/vault/index/{'b' * 32}", headers=AUTH)).text)
        await r.repo.close()

    with caplog.at_level(logging.DEBUG):
        run(scenario())
    assert len(texts) >= 3
    assert all(CANARY not in text and str(tmp_path) not in text for text in texts)
    assert CANARY not in caplog.text and "canary-" not in caplog.text


# ----- wiring from the settings -----------------------------------------------------------------


def test_the_runtime_needs_only_the_vault_folder_and_closes(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    index = tmp_path / "index"
    index.mkdir()
    gate, embedder = FakeGate(), FakeEmbeddingClient()
    assert build_index_runtime(Settings(index_dir=index), gate, embedder, JobSlot()) is None  # type: ignore[arg-type]
    runtime = build_index_runtime(
        Settings(vault_dir=vault, index_dir=index),
        gate,  # type: ignore[arg-type]
        embedder,
        JobSlot(),
    )
    assert isinstance(runtime, IndexRuntime) and isinstance(runtime.jobs, IndexJobs)

    async def scenario() -> None:
        assert runtime is not None
        job_id = await runtime.jobs.start()
        for _ in range(500):
            if runtime.jobs.get(job_id).status != "running":
                break
            await asyncio.sleep(0.01)
        assert runtime.jobs.get(job_id).status == "complete"
        await runtime.aclose()

    run(scenario())
    assert (index / "vault.sqlite3").exists()
