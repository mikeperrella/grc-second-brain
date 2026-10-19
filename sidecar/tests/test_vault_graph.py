"""`GET /vault/graph` (ADR-023, chunk 65c): a read-only vault scan whose edges are resolved explicit
links only, with fixed caps that refuse the whole graph, no document text in the reply, nothing
outside the vault followed, and `live_promises` null (never 0) when no ledger folder is in use.
All data is synthetic.
"""

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

import httpx
import pytest

from grc_sidecar.api.vault_graph import EdgeResponse, GraphResponse, NodeResponse
from grc_sidecar.config import Settings
from grc_sidecar.ledger.service import LedgerService
from grc_sidecar.main import create_app
from grc_sidecar.repositories.sqlite_ledger_repo import SqliteLedgerRepository
from grc_sidecar.security.net import create_async_client
from grc_sidecar.vault import graph as graph_module
from grc_sidecar.vault.clock import FixedClock
from grc_sidecar.vault.graph import SAYS, VaultGraphService

from .contradictionmodelkit import CANARY, run_scenario
from .indexkit import write_doc
from .ledgerkit import run
from .test_ledger_hygiene import FORBIDDEN_FIELD_PARTS
from .test_ledger_recall import BASE, HEADING, KEYS, POLICY, QUOTE, policy_text, scenario
from .test_ledger_recall_routes import BACKUP_LINE, OTHER_POLICY
from .test_questionnaire_runtime import started

pytestmark = pytest.mark.usefixtures("clean_grc_env")

TOKEN = hashlib.sha256(b"grc-test-token").hexdigest()
AUTH = {"X-GRC-Auth": TOKEN}
URL = "/vault/graph"
FRAMEWORK_NOTE = "\n".join(
    ["---", "doc_id: FW-1", "type: framework", "owner: Synthetic Owner", "---", "# Framework", ""]
)


@asynccontextmanager
async def client_for(service: VaultGraphService | None) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(TOKEN, vault_graph=service)
    async with create_async_client(
        "http://127.0.0.1:8000", transport=httpx.ASGITransport(app=app)
    ) as client:
        yield client


def get(service: VaultGraphService | None, **kwargs: object) -> httpx.Response:
    async def scenario_() -> httpx.Response:
        async with client_for(service) as client:
            return await client.get(URL, headers=AUTH, **kwargs)  # type: ignore[arg-type]

    return run_scenario(scenario_())


def make_vault(root: Path) -> Path:
    """Eight company notes, one framework note and every kind of link, written by hand."""
    vault = root / "vault"
    (vault / "x").mkdir(parents=True)
    (vault / "y").mkdir()
    write_doc(
        vault,
        "alpha.md",
        "# Alpha\n\n"
        "See [[beta]] and again [[beta]], plus [[gamma#Part]].\n"
        "Self: [[#Alpha]].\n"
        "Missing: [[nowhere]].\n"
        "Ambiguous: [[dup]].\n"
        "Out: [[../outside]] [[/etc/passwd]] [[C:\\win]] [[sub/../beta]].\n"
        "Framework: [[fw]].\n",
        links=["delta"],
    )
    write_doc(vault, "beta.md", "# Beta\n\nBack to [[alpha]].\n")
    write_doc(vault, "gamma.md", "# Gamma\n\nNo links here.\n")
    write_doc(vault, "delta.md", "# Delta\n\nNo links here.\n")
    write_doc(vault, "x/dup.md", "# Dup X\n\nOne of two notes with this name.\n", doc_id="DUP-X")
    write_doc(vault, "y/dup.md", "# Dup Y\n\nOne of two notes with this name.\n", doc_id="DUP-Y")
    same = "# Reviews\n\nAccess reviews are performed every quarter by the security team.\n"
    write_doc(vault, "similar-1.md", same)  # the same words, but nobody linked them
    write_doc(vault, "similar-2.md", same)
    (vault / "fw.md").write_text(FRAMEWORK_NOTE, encoding="utf-8", newline="\n")
    return vault


PATHS = [
    "alpha.md",
    "beta.md",
    "delta.md",
    "gamma.md",
    "similar-1.md",
    "similar-2.md",
    "x/dup.md",
    "y/dup.md",
]


def service_for(vault: Path) -> VaultGraphService:
    return VaultGraphService(vault, None)


# ----- the token, the unset vault, the request ----------------------------------------------------


@pytest.mark.parametrize("headers", [{}, {"X-GRC-Auth": "0" * 64}, {"X-GRC-Auth": ""}])
def test_the_route_needs_the_token(tmp_path: Path, headers: dict[str, str]) -> None:  # [NEG]
    async def scenario_() -> None:
        async with client_for(service_for(make_vault(tmp_path))) as client:
            response = await client.get(URL, headers=headers)
            assert response.status_code == 401 and response.content == b""

    run_scenario(scenario_())


def test_without_a_vault_folder_the_route_refuses_503(tmp_path: Path) -> None:  # [NEG]
    assert get(None).status_code == 503
    assert get(None).json() == {"error": "vault_root_not_configured"}
    unusable = get(service_for(tmp_path / "does-not-exist"))
    assert unusable.status_code == 503 and unusable.json() == {"error": "vault_root_not_configured"}


def test_the_route_takes_no_body_and_only_get(tmp_path: Path) -> None:  # [NEG]
    service = service_for(make_vault(tmp_path))

    async def scenario_() -> None:
        async with client_for(service) as client:
            assert (await client.post(URL, json={}, headers=AUTH)).status_code == 405
            assert (await client.delete(URL, headers=AUTH)).status_code == 405

    run_scenario(scenario_())
    # A query is ignored, never used: nothing a caller types can point the scan elsewhere.
    plain = get(service).json()
    assert get(service, params={"path": "/etc", "vault": "../x", "q": CANARY}).json() == plain


def test_the_response_models_have_no_url_token_credential_or_host_field() -> None:  # [NEG]
    for model in (GraphResponse, NodeResponse, EdgeResponse):
        assert model.model_config["extra"] == "forbid"
        for name, field in model.model_fields.items():
            for part in FORBIDDEN_FIELD_PARTS:
                assert part not in name.lower() and part not in (field.alias or "")
    assert set(NodeResponse.model_fields) == {"index", "path", "doc_id", "type", "live_promises"}
    assert {f.alias or n for n, f in EdgeResponse.model_fields.items()} == {
        "from",
        "to",
        "origin",
        "count",
    }


# ----- the graph ----------------------------------------------------------------------------------


def test_nodes_edges_and_counts_come_from_the_explicit_links_written_in_the_files(
    tmp_path: Path,
) -> None:
    response = get(service_for(make_vault(tmp_path)))
    assert response.status_code == 200, response.text
    assert response.json() == {
        "nodes": [
            {
                "index": index,
                "path": path,
                "doc_id": (
                    path.removesuffix(".md").upper()
                    if "/" not in path
                    else "DUP-" + path[0].upper()
                ),
                "type": "policy",
                "live_promises": None,
            }
            for index, path in enumerate(PATHS)
        ],
        "edges": [
            {"from": 0, "to": 1, "origin": "body", "count": 2},  # alpha -> beta, written twice
            {"from": 0, "to": 2, "origin": "frontmatter", "count": 1},  # alpha -> delta
            {"from": 0, "to": 3, "origin": "body", "count": 1},  # alpha -> gamma#Part
            {"from": 1, "to": 0, "origin": "body", "count": 1},  # beta -> alpha
        ],
        "unresolved_links": 1,  # [[nowhere]]
        "ambiguous_links": 1,  # [[dup]]: two notes with that name
        "rejected_links": 4,  # ../outside, /etc/passwd, C:\win, sub/../beta
        "links_not_drawn": 2,  # the link to itself and the link to the framework note
        "files_refused": 0,
        "says": SAYS,
    }


def test_similar_text_and_shared_words_make_no_edge(tmp_path: Path) -> None:  # [NEG] CLAUDE.md 8.2
    body = get(service_for(make_vault(tmp_path))).json()
    similar = {PATHS.index("similar-1.md"), PATHS.index("similar-2.md")}
    assert similar == {4, 5}
    for edge in body["edges"]:
        assert edge["from"] not in similar and edge["to"] not in similar
    assert len(body["edges"]) == 4  # exactly the written links, nothing inferred


def test_the_graph_is_read_from_the_files_now_not_from_an_index(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    service = service_for(vault)  # no index folder exists anywhere in this test
    before = get(service).json()
    write_doc(vault, "zeta.md", "# Zeta\n\nSee [[gamma]].\n")
    after = get(service).json()
    assert len(after["nodes"]) == len(before["nodes"]) + 1
    assert after["nodes"][-1]["path"] == "zeta.md"
    assert {"from": 8, "to": 3, "origin": "body", "count": 1} in after["edges"]


def test_no_document_text_and_no_link_text_is_in_the_reply_or_a_log(  # [NEG]
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    vault = make_vault(tmp_path)
    write_doc(
        vault,
        "canary.md",
        f"# {CANARY}-heading\n\nPolicy text {CANARY}-body, link [[{CANARY}-target]] "
        f"and [[../{CANARY}-out]].\n",
        doc_id="CANARY-DOC",
    )
    response = get(service_for(vault))
    assert response.status_code == 200
    assert CANARY not in response.text  # not the heading, the sentence, nor any link target
    assert "canary.md" in response.text and "CANARY-DOC" in response.text  # path and id only
    body = response.json()
    assert body["unresolved_links"] == 2 and body["rejected_links"] == 5
    logged = "\n".join(f"{r.getMessage()}|{r.exc_text}|{r.args}" for r in caplog.records)
    assert CANARY not in logged


def test_the_scan_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # [NEG]
    vault = make_vault(tmp_path)
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)

    def tree() -> dict[str, str]:
        return {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for root in (vault, work)
            for p in sorted(root.rglob("*"))
            if p.is_file()
        }

    before = tree()
    assert get(service_for(vault)).status_code == 200
    assert tree() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cwd", "vault"]  # no index, no cache


# ----- nothing outside the vault is followed ------------------------------------------------------


def test_a_link_that_leaves_the_vault_is_rejected_and_makes_no_edge(
    tmp_path: Path,
) -> None:  # [NEG]
    vault = make_vault(tmp_path)
    write_doc(tmp_path, "outside.md", "# Outside\n\nA valid note next to the vault.\n")
    write_doc(vault, "leaky.md", "# Leaky\n\n[[../outside]] [[sub/../beta]] [[/outside]]\n")
    body = get(service_for(vault)).json()
    assert body["rejected_links"] == 4 + 3
    paths = [node["path"] for node in body["nodes"]]
    assert "outside.md" not in paths
    leaky = paths.index("leaky.md")
    assert all(edge["from"] != leaky for edge in body["edges"])
    assert {"from": 0, "to": 1, "origin": "body", "count": 2} in body["edges"]  # not 3: no `..`


def test_a_symlink_or_junction_in_the_vault_is_refused_and_not_followed(  # [NEG]
    tmp_path: Path,
) -> None:
    vault = make_vault(tmp_path)
    outside = tmp_path / "elsewhere"
    write_doc(outside, "secret-note.md", "# Secret\n\nA note outside the vault.\n")
    try:
        (vault / "link.md").symlink_to(outside / "secret-note.md")
        (vault / "folder").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this system does not allow symlinks")
    write_doc(vault, "seeker.md", "# Seeker\n\n[[secret-note]] and [[link]].\n")
    body = get(service_for(vault)).json()
    paths = [node["path"] for node in body["nodes"]]
    assert "link.md" not in paths and "secret-note.md" not in paths
    assert not any(path.startswith("folder/") for path in paths)
    assert body["files_refused"] >= 1  # counted, so a missing node is not a silent skip
    seeker = paths.index("seeker.md")
    assert all(edge["from"] != seeker for edge in body["edges"])
    assert body["unresolved_links"] == 1 + 2  # [[nowhere]], and the two links to unseen files


# ----- the caps: the whole graph or nothing -------------------------------------------------------


def test_above_the_node_cap_the_whole_graph_is_refused_with_integer_counts(  # [NEG]
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = make_vault(tmp_path)
    monkeypatch.setattr(graph_module, "MAX_GRAPH_NODES", 7)  # eight company notes
    refused = get(service_for(vault))
    assert refused.status_code == 422
    assert refused.json() == {
        "error": "graph_refused",
        "code": "graph_too_large",
        "counts": {"nodes": 8, "edges": 4, "node_cap": 7, "edge_cap": 5000},
    }
    assert "nodes" not in refused.json() and "path" not in refused.text  # nothing partial
    monkeypatch.setattr(graph_module, "MAX_GRAPH_NODES", 8)  # exactly at the cap is fine
    assert get(service_for(vault)).status_code == 200


def test_above_the_edge_cap_the_whole_graph_is_refused_and_the_ledger_is_not_asked(  # [NEG]
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = make_vault(tmp_path)
    asked: list[str] = []

    async def counts() -> dict[str, int]:
        asked.append("ledger")
        return {}

    monkeypatch.setattr(graph_module, "MAX_GRAPH_EDGES", 3)  # four distinct edges
    refused = get(VaultGraphService(vault, counts))
    assert refused.status_code == 422
    assert refused.json()["counts"] == {"nodes": 8, "edges": 4, "node_cap": 500, "edge_cap": 3}
    assert asked == []
    monkeypatch.setattr(graph_module, "MAX_GRAPH_EDGES", 4)
    assert get(VaultGraphService(vault, counts)).status_code == 200 and asked == ["ledger"]


def test_the_caps_are_the_documented_starting_values() -> None:
    assert (graph_module.MAX_GRAPH_NODES, graph_module.MAX_GRAPH_EDGES) == (500, 5_000)


# ----- live_promises ------------------------------------------------------------------------------


def test_with_no_ledger_folder_live_promises_is_null_on_every_node_never_zero(  # [NEG]
    tmp_path: Path,
) -> None:
    response = get(service_for(make_vault(tmp_path)))
    nodes = response.json()["nodes"]
    assert nodes and all(node["live_promises"] is None for node in nodes)
    assert '"live_promises":null' in response.text.replace(" ", "")
    assert '"live_promises":0' not in response.text.replace(" ", "")


def test_live_promises_counts_live_promises_by_the_exact_stored_path(tmp_path: Path) -> None:
    other = policy_text(HEADING, BACKUP_LINE, KEYS).replace("ENC-001", "BAK-001")
    cites = [(POLICY, QUOTE), (OTHER_POLICY, BACKUP_LINE), (POLICY, QUOTE)]
    with scenario(tmp_path, files={POLICY: BASE, OTHER_POLICY: other}, cites=cites) as s:
        extra = policy_text("# Other", "Nothing cites this.").replace("ENC-001", "OTH-001")
        (s.vault / "notes").mkdir()
        (s.vault / "notes" / "other.md").write_text(extra, encoding="utf-8", newline="\n")
        service = VaultGraphService(s.vault, s.d.service.live_promise_counts)

        def counts() -> dict[str, int | None]:
            nodes = get(service).json()["nodes"]
            return {node["path"]: node["live_promises"] for node in nodes}

        assert counts() == {OTHER_POLICY: 1, "notes/other.md": 0, POLICY: 2}
        first = s.promise_ids()[0]
        run(s.d.service.void(first, "other"))
        assert counts() == {OTHER_POLICY: 1, "notes/other.md": 0, POLICY: 1}  # voided: left out


def test_a_configured_ledger_with_no_file_yet_gives_zero_and_creates_nothing(  # [NEG]
    tmp_path: Path,
) -> None:
    clock = FixedClock(date(2026, 10, 10))
    folder = tmp_path / "ledger"
    folder.mkdir()
    repo = SqliteLedgerRepository(folder / "ledger.sqlite3", clock)
    ledger = LedgerService(repo, None, clock)
    vault = make_vault(tmp_path)
    nodes = get(VaultGraphService(vault, ledger.live_promise_counts)).json()["nodes"]
    assert nodes and all(node["live_promises"] == 0 for node in nodes)  # a ledger, no promises
    assert list(folder.iterdir()) == []  # a read never makes the ledger file
    run(repo.close())


# ----- the wiring from the settings ---------------------------------------------------------------


def test_the_app_builds_the_graph_service_from_the_settings(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir()
    # Nothing listens on this port: the background model check fails at once and no model is used.
    quiet = "http://127.0.0.1:9"

    async def scenario_() -> None:
        plain = create_app(TOKEN, settings=Settings(vault_dir=vault, ollama_base_url=quiet))
        async with started(plain) as client:
            body = (await client.get(URL, headers=AUTH)).json()
            assert len(body["nodes"]) == 8
            assert all(node["live_promises"] is None for node in body["nodes"])  # no ledger folder
        with_ledger = create_app(
            TOKEN, settings=Settings(vault_dir=vault, ledger_dir=ledger_dir, ollama_base_url=quiet)
        )
        async with started(with_ledger) as client:
            body = (await client.get(URL, headers=AUTH)).json()
            assert all(node["live_promises"] == 0 for node in body["nodes"])
        no_vault = create_app(TOKEN, settings=Settings(ollama_base_url=quiet))
        async with started(no_vault) as client:
            refused = await client.get(URL, headers=AUTH)
            assert refused.status_code == 503
            assert refused.json() == {"error": "vault_root_not_configured"}

    asyncio.run(asyncio.wait_for(scenario_(), 30))
    assert list(ledger_dir.iterdir()) == []  # the graph never created the ledger file
