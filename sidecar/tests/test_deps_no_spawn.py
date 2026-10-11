"""PyYAML and sqlite-vec start no process (M3 plan, "starts no subprocess" verification, layer 2).

The child runs the real work of both dependencies with the REAL ADR-007 guard installed. Any
attempt to start a process raises SpawnBlockedError and the child fails.
"""

import json
import secrets
import textwrap
import time
from pathlib import Path

from .childproc import minimal_env, run_python, running_python
from .test_startup import _get_health, _read_ready

_DEPENDENCY_WORK = r"""
import asyncio, json, sqlite3, sys
from pathlib import Path

from grc_sidecar.repositories.base import EmbeddingProfile, IndexedDocument
from grc_sidecar.repositories.sqlite_vec_repo import SqliteVecRepository, vec_extension_path
from grc_sidecar.security.process_guard import install_process_guard
from grc_sidecar.vault.chunker import CHUNKER_VERSION, chunk_document
from grc_sidecar.vault.frontmatter import parse_document

install_process_guard(vec_extension_path())

TEXT = "\n".join([
    "---", "doc_id: DOC-1", "type: policy", "owner: Test Owner", "review_by: 2028-01-01", "---",
    "# AC-2 Account Management", "Accounts are reviewed quarterly (see CC6.1).", "",
])
parsed = parse_document(TEXT, "policies/access.md")      # PyYAML
chunks = chunk_document(parsed)
doc = IndexedDocument(
    source_path=parsed.source_path, collection="vault", doc_id=parsed.frontmatter.doc_id,
    file_sha256=parsed.file_sha256, chunker_version=CHUNKER_VERSION, type="policy",
    owner="Test Owner", review_by="2028-01-01",
)
profile = EmbeddingProfile(model="m", model_digest="d", dimension=4, doc_prefix="", query_prefix="")
index_path = Path(sys.argv[1]) / "index.sqlite3"


async def work():
    repo = SqliteVecRepository(index_path)
    await repo.open_or_create(profile)                      # loads vec0 under the guard
    try:
        await repo.replace_document(doc, chunks, [[1.0, 0.0, 0.0, 0.0]] * len(chunks), [])
        hits = await repo.search([1.0, 0.0, 0.0, 0.0], k=1)  # vec0 KNN
        return len(chunks), len(hits)
    finally:
        await repo.close()


n_chunks, n_hits = asyncio.run(work())
con = sqlite3.connect(index_path)                           # FTS5, no extension needed
sql = "SELECT count(*) FROM fts_chunks WHERE fts_chunks MATCH ?"
fts = con.execute(sql, ('"AC-2"',)).fetchone()[0]
con.close()
print(json.dumps({
    "yaml_title": parsed.frontmatter.doc_id, "chunks": n_chunks,
    "knn_hits": n_hits, "fts_hits": fts,
}))
"""


def test_deps_run_under_real_process_guard(tmp_path: Path) -> None:  # [NEG]
    script = tmp_path / "child.py"
    script.write_text(textwrap.dedent(_DEPENDENCY_WORK), encoding="utf-8")
    result = run_python([str(script), str(tmp_path)], env=minimal_env(), cwd=tmp_path, timeout=120)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "yaml_title": "DOC-1",
        "chunks": 1,
        "knn_hits": 1,
        "fts_hits": 1,
    }


# The real sidecar, started the way the host starts it. After the ready line a probe tries to load
# the vendored extension (must work) and a foreign copy of it (must be refused), proving that
# __main__ really passes the approved path to the guard.
_REAL_SIDECAR_PROBE = r"""
import json, os, runpy, shutil, sqlite3, sys
from pathlib import Path
from grc_sidecar import main as sidecar_main
from grc_sidecar.repositories.sqlite_vec_repo import vec_extension_path

RESULT = os.environ["GRC_TEST_RESULT"]
real_emit = sidecar_main.ReadySignalServer._emit_ready


def attempt(path):
    con = sqlite3.connect(":memory:")
    con.enable_load_extension(True)
    try:
        con.load_extension(path)
        return "loaded"
    except BaseException as exc:
        return type(exc).__name__
    finally:
        con.close()


def emit_then_probe(self, port):
    real_emit(self, port)
    approved = vec_extension_path()
    suffix = ".dll" if sys.platform == "win32" else ".so"
    foreign_dir = Path(os.environ["GRC_TEST_FOREIGN"])
    foreign_dir.mkdir()
    shutil.copyfile(str(approved) + suffix, foreign_dir / ("vec0" + suffix))
    outcome = {"approved": attempt(str(approved)), "foreign": attempt(str(foreign_dir / "vec0"))}
    with open(RESULT, "w", encoding="utf-8") as handle:
        json.dump(outcome, handle)


sidecar_main.ReadySignalServer._emit_ready = emit_then_probe
runpy.run_module("grc_sidecar", run_name="__main__", alter_sys=True)
"""


def test_the_real_sidecar_allows_only_the_vendored_extension(tmp_path: Path) -> None:  # [NEG]
    token = secrets.token_hex(32)
    result_file = tmp_path / "result.json"
    (tmp_path / "index").mkdir()  # the host creates the index folder; the sidecar requires it (M4b)
    env = minimal_env(
        {
            "GRC_LOG_DIR": str(tmp_path / "logs"),
            "GRC_INDEX_DIR": str(tmp_path / "index"),
            "GRC_AUTH_TOKEN": token,
            "GRC_TEST_RESULT": str(result_file),
            "GRC_TEST_FOREIGN": str(tmp_path / "elsewhere"),
        }
    )
    stderr_path = tmp_path / "stderr.txt"
    with running_python(
        ["-c", _REAL_SIDECAR_PROBE], env=env, cwd=tmp_path, stderr_path=stderr_path
    ) as (_proc, out):
        _line, port = _read_ready(out, stderr_path)
        deadline = time.monotonic() + 30
        while not result_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert result_file.exists(), "the post-ready probe never reported"
        assert _get_health(port, {"X-GRC-Auth": token}).status_code == 200
    assert json.loads(result_file.read_text(encoding="utf-8")) == {
        "approved": "loaded",
        "foreign": "ExtensionLoadBlockedError",
    }
