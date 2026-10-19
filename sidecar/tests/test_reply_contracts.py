"""Reply contracts (M6_5_PLAN.md section 10; fixes the cause of deviation M6-15).

The window parses every reply the sidecar sends. This test builds synthetic data (a vault, a ledger
with customers and promises, an export CSV, a fixed clock, a scripted model), calls every route the
window uses or will use through the REAL app with the token, and keeps for each route at least one
success reply and one example of every error family it can return. Ids and run ids are replaced, in
order of appearance, by fixed values of the right shape, and the result is compared with
`fixtures/contracts/replies/<route>.json`.

Those files are written ONLY when `GRC_WRITE_REPLY_CONTRACTS=1`, and that run still FAILS on
purpose (the `GRC_WRITE_HEALTH_CONTRACT` pattern, ADR-006 decision 3), so a write can never pass
unnoticed. Without the switch the test compares and fails on any difference, a missing file or a
file for a route that no longer exists. Route names are the snake_case names of the host proxy's
routes (the seven M6 routes are named in `proxy-contract.json`; the rest are the names planned for
chunk 65d).

Everything is offline: no Ollama, no port 11434, no network. All data is synthetic.
"""

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import zipfile
from collections import Counter
from io import BytesIO
from pathlib import Path

import httpx
import pytest

from grc_sidecar.contradictions.runs import ContradictionRuns
from grc_sidecar.contradictions.service import ContradictionDetector
from grc_sidecar.errors import AuthControlsError, IndexNotBuiltError, ModelsUnavailableError
from grc_sidecar.jobs import JobSlot
from grc_sidecar.ledger import service as ledger_service_module
from grc_sidecar.ledger.service import LedgerService
from grc_sidecar.main import create_app
from grc_sidecar.reality import claims as reality_claims_module
from grc_sidecar.repositories.sqlite_ledger_repo import SqliteLedgerRepository
from grc_sidecar.repositories.sqlite_vec_repo import SqliteVecRepository
from grc_sidecar.security.net import create_async_client
from grc_sidecar.vault import controlmap as controlmap_module
from grc_sidecar.vault import graph as graph_module
from grc_sidecar.vault.graph import VaultGraphService

from .clockkit import ManualClock
from .contradictionmodelkit import MODEL, repeat, reply, run_scenario
from .ledgerkit import (
    QUESTION_COL,
    SENT_ON,
    approve,
    draft_for,
    edit_cells,
    supported_outcomes,
)
from .realitykit import csv_bytes, row, write
from .runskit import (
    FILE_NAME,
    MAPPING,
    FakeGate,
    FakeVault,
    ScriptedAnswerer,
    make_workbook,
    models_unavailable,
    setup,
)
from .test_contradiction_routes import real_runs
from .test_contradiction_runs_routes import ScriptedDetector
from .test_index_routes import GatedEmbedder, rig
from .test_ledger_recall import BASE, HEADING, KEYS, POLICY, QUOTE, policy_text, scenario, set_text
from .test_ledger_recall_routes import BACKUP_LINE, OTHER_POLICY
from .test_model_gate import EMBEDDING, GENERATION, FakeOllama
from .test_readiness import gate_for
from .test_reality_check_routes import body_for, demo_runtime
from .test_vault_graph import make_vault
from .vaultkit import CONTRADICTIONS_VAULT, REPO_ROOT
from .xlsxkit import rebuild, simple_workbook, to_bytes

pytestmark = pytest.mark.usefixtures("clean_grc_env")

TOKEN = hashlib.sha256(b"grc-test-token").hexdigest()
AUTH = {"X-GRC-Auth": TOKEN}
WRITE_SWITCH = "GRC_WRITE_REPLY_CONTRACTS"
REPLIES = REPO_ROOT / "fixtures" / "contracts" / "replies"
FORWARDED = {200, 202, 400, 404, 409, 422, 503}  # the statuses the host proxy forwards
SOME_RUN = "a" * 32
SOME_PROMISE = "00000000-0000-4000-8000-000000000000"
DOCTYPE = b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]><a/>'

IMPORT_BODY = {
    "run_id": SOME_RUN,
    "file": FILE_NAME,
    "new_customer_name": "Acme Test Co",
    "confirm_new": True,
    "sent_on": SENT_ON,
}
CHECK_BODY = {
    "file": "okta.csv",
    "claim": {"path": "p.md", "start_line": 1, "end_line": 1, "quote": "MFA is required."},
    "mfa_types": ["Okta Verify"],
    "phone_types": ["Phone"],
    "exported_at": "2026-10-01",
}

# route -> (method, url, json body): the smallest valid request for each route.
MINIMAL: dict[str, tuple[str, str, object]] = {
    "readiness": ("GET", "/readiness", None),
    "questionnaire_inspect": ("POST", "/questionnaire/inspect", {"file": FILE_NAME}),
    "questionnaire_start": (
        "POST",
        "/questionnaire/runs",
        {"file": FILE_NAME, "mapping": MAPPING.model_dump()},
    ),
    "questionnaire_status": ("GET", f"/questionnaire/runs/{SOME_RUN}", None),
    "vault_index_start": ("POST", "/vault/index", None),
    "vault_index_status": ("GET", f"/vault/index/{SOME_RUN}", None),
    "draft_rows": ("POST", "/questionnaire/draft", {"file": FILE_NAME}),
    "ledger_customers": ("GET", "/ledger/customers", None),
    "ledger_promises": ("GET", "/ledger/promises", None),
    "ledger_cited_paths": ("GET", "/ledger/cited-paths", None),
    "ledger_import": ("POST", "/ledger/import", IMPORT_BODY),
    "ledger_recall": ("POST", "/ledger/recall", {}),
    "ledger_void": ("POST", f"/ledger/promises/{SOME_PROMISE}/void", {"reason": "other"}),
    "ledger_acknowledge": (
        "POST",
        f"/ledger/promises/{SOME_PROMISE}/acknowledge",
        {"seen_recall_event_id": 1},
    ),
    "ledger_blast_radius": (
        "POST",
        "/ledger/blast-radius",
        {"path": "policies/p.md", "delete_file": True},
    ),
    "reality_inspect": ("POST", "/reality/inspect", {"file": "okta.csv"}),
    "reality_claims": ("GET", "/reality/claims", None),
    "reality_check": ("POST", "/reality/check", CHECK_BODY),
    "contradictions_start": ("POST", "/contradictions/runs", {}),
    "contradictions_status": ("GET", f"/contradictions/runs/{SOME_RUN}", None),
    "vault_graph": ("GET", "/vault/graph", None),
}
LEDGER_ROUTES = [r for r in MINIMAL if r.startswith("ledger_")] + ["draft_rows"]

# route -> the error words (families) the route can answer, each of which must have an example.
# Every route also needs a success (200 or 202).
INVALID = "invalid_request"
SLOT = "run_in_progress"
LEDGER_503 = {"ledger_root_not_configured", "ledger_unusable", "ledger_store_error"}
REQUIRED_ERRORS: dict[str, set[str]] = {
    "readiness": {"models_unavailable"},
    "questionnaire_inspect": {
        "questionnaire_root_not_configured",
        "file_not_found",
        "path_not_allowed",
        "workbook_refused",
        INVALID,
    },
    "questionnaire_start": {
        "questionnaire_root_not_configured",
        "file_not_found",
        "path_not_allowed",
        "workbook_refused",
        "mapping_refused",
        "models_unavailable",
        "vault_changed_reindex",
        "index_not_built",
        SLOT,
        INVALID,
    },
    "questionnaire_status": {"questionnaire_root_not_configured", "run_not_found", INVALID},
    "vault_index_start": {"vault_root_not_configured", "models_unavailable", SLOT},
    "vault_index_status": {"vault_root_not_configured", "run_not_found", INVALID},
    "draft_rows": LEDGER_503
    | {
        "file_not_found",
        "path_not_allowed",
        "draft_refused",
        "export_root_not_configured",
        INVALID,
    },
    "ledger_customers": LEDGER_503,
    "ledger_promises": LEDGER_503 | {INVALID},
    "ledger_cited_paths": LEDGER_503 | {"recall_refused"},
    "ledger_import": LEDGER_503
    | {
        "export_root_not_configured",
        "import_refused",
        "ledger_refused",
        "workbook_refused",
        "file_not_found",
        "path_not_allowed",
        INVALID,
    },
    "ledger_recall": LEDGER_503 | {"vault_root_not_configured", "recall_refused"},
    "ledger_void": LEDGER_503 | {"ledger_refused", "recall_refused", INVALID},
    "ledger_acknowledge": LEDGER_503 | {"ledger_refused", "recall_refused", INVALID},
    "ledger_blast_radius": LEDGER_503
    | {"vault_root_not_configured", "blast_radius_refused", INVALID},
    "reality_inspect": {
        "import_root_not_configured",
        "file_not_found",
        "path_not_allowed",
        "export_refused",
        INVALID,
    },
    "reality_claims": {
        "import_root_not_configured",
        "control_map_too_large",
        "auth_controls_invalid",
    },
    "reality_check": {
        "import_root_not_configured",
        "file_not_found",
        "path_not_allowed",
        "export_refused",
        "check_refused",
        "control_map_too_large",
        "auth_controls_invalid",
        INVALID,
    },
    "contradictions_start": {"vault_root_not_configured", "index_not_built", SLOT, INVALID},
    "contradictions_status": {"vault_root_not_configured", "run_not_found", INVALID},
    "vault_graph": {"vault_root_not_configured", "graph_refused"},
}


class Recorder:
    def __init__(self) -> None:
        self.cases: dict[str, list[dict[str, object]]] = {}

    def add(self, route: str, case: str, response: httpx.Response) -> httpx.Response:
        assert route in MINIMAL, route
        assert response.status_code in FORWARDED, (route, case, response.status_code)
        self.cases.setdefault(route, []).append(
            {"case": case, "status": response.status_code, "body": response.json()}
        )
        return response


async def send(
    app: object,
    method: str,
    url: str,
    body: object = None,
    params: dict[str, object] | None = None,
) -> httpx.Response:
    async with create_async_client(
        "http://127.0.0.1:8000",
        transport=httpx.ASGITransport(app=app),  # type: ignore[arg-type]
    ) as client:
        return await client.request(method, url, json=body, params=params, headers=AUTH)


def hit(
    app: object,
    method: str,
    url: str,
    body: object = None,
    params: dict[str, object] | None = None,
) -> httpx.Response:
    return run_scenario(send(app, method, url, body, params))


def minimal(app: object, route: str, **changes: object) -> httpx.Response:
    method, url, body = MINIMAL[route]
    if changes and isinstance(body, dict):
        body = {**body, **changes}
    return hit(app, method, url, body)


# ----- worlds: each builds one situation and records what the routes answer -----------------------


def fixed_workbook(count: int) -> bytes:
    """The sample questionnaire with fixed times inside, so its hash is the same on every run.

    openpyxl stamps the document properties and every zip entry with the current time; a hash
    printed in a reply (`sha256`, `input_sha256`) would otherwise differ from run to run. The
    entries are stored without compression and with fixed attributes, so the bytes do not depend on
    the zlib version or on the operating system either."""
    rows = [(f"Q-{n}", f"Do you encrypt data at rest, question {n}?") for n in range(1, count + 1)]
    out = BytesIO()
    with (
        zipfile.ZipFile(BytesIO(to_bytes(simple_workbook(rows)))) as source,
        zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as target,
    ):
        for info in source.infolist():
            content = source.read(info)
            if info.filename == "docProps/core.xml":  # openpyxl stamps both times on every save
                content = re.sub(
                    rb"(<dcterms:\w+[^>]*>)[^<]*", rb"\g<1>2026-10-10T00:00:00Z", content
                )
            entry = zipfile.ZipInfo(info.filename, date_time=(2026, 10, 10, 0, 0, 0))
            entry.create_system = 3  # the default is 0 on Windows and 3 elsewhere
            entry.external_attr = 0o600 << 16
            target.writestr(entry, content, zipfile.ZIP_STORED)
    return out.getvalue()


def fresh(root: Path, name: str) -> Path:
    """A new empty folder `root/name` (the parents too)."""
    path = root / name
    path.mkdir(parents=True)
    return path


def world_nothing_configured(rec: Recorder) -> None:
    """An app with no folders and no models: every route says what is missing."""
    app = create_app(TOKEN)
    for route in MINIMAL:
        rec.add(route, "not_configured", minimal(app, route))


def world_readiness(rec: Recorder) -> None:
    unreachable = FakeOllama(error=lambda r: httpx.ConnectError("refused", request=r))
    for case, fake in (
        ("ready", FakeOllama()),
        ("model_missing", FakeOllama((f"{EMBEDDING}:latest",))),
        ("ollama_unreachable", unreachable),
    ):
        rec.add("readiness", case, hit(create_app(TOKEN, gate=gate_for(fake)), "GET", "/readiness"))
    assert GENERATION  # the missing model above is the generation model


def world_questionnaire(rec: Recorder, tmp: Path) -> None:
    hold = asyncio.Event()
    answerer = ScriptedAnswerer(hold=hold)
    s = setup(fresh(tmp, "run"), answerer=answerer, data=fixed_workbook(3))
    hostile = rebuild(make_workbook(2), replace={"xl/worksheets/sheet1.xml": DOCTYPE})
    (s.questionnaire_root / "Hostile.xlsx").write_bytes(hostile)
    app = create_app(TOKEN, questionnaire=s.runs)
    body = {"file": FILE_NAME, "mapping": MAPPING.model_dump()}
    wrong = {"file": FILE_NAME, "mapping": {"sheet": "Nope", "header_row": 1, "question_col": 2}}

    async def go() -> None:
        post = "POST"
        inspect, start = "/questionnaire/inspect", "/questionnaire/runs"
        rec.add("questionnaire_inspect", "ok", await send(app, post, inspect, {"file": FILE_NAME}))
        for case, name in (
            ("file_not_found", "Missing.xlsx"),
            ("path_not_allowed", "../x.xlsx"),
            ("workbook_refused", "Hostile.xlsx"),
        ):
            rec.add("questionnaire_inspect", case, await send(app, post, inspect, {"file": name}))
            rec.add(
                "questionnaire_start", case, await send(app, post, start, {**body, "file": name})
            )
        rec.add("questionnaire_inspect", "invalid_request", await send(app, post, inspect, {}))
        rec.add("questionnaire_start", "invalid_request", await send(app, post, start, {}))
        rec.add("questionnaire_start", "mapping_refused", await send(app, post, start, wrong))
        started = rec.add("questionnaire_start", "accepted", await send(app, post, start, body))
        run_id = started.json()["run_id"]
        await answerer.entered.wait()
        rec.add("questionnaire_start", "run_in_progress", await send(app, post, start, body))
        status = f"/questionnaire/runs/{run_id}"
        rec.add("questionnaire_status", "running", await send(app, "GET", status))
        hold.set()
        await send(app, "GET", f"{status}/events")  # to the end; the stream itself is not proxied
        rec.add("questionnaire_status", "complete", await send(app, "GET", status))
        unknown = f"/questionnaire/runs/{SOME_RUN}"
        rec.add("questionnaire_status", "run_not_found", await send(app, "GET", unknown))
        rec.add("questionnaire_status", "invalid_request", await send(app, "GET", f"{start}/nope"))

    run_scenario(go())
    refusals = (
        ("models_unavailable", {"gate": FakeGate(models_unavailable())}),
        ("vault_changed_reindex", {"vault": FakeVault(withheld=("a.md",))}),
        ("index_not_built", {"vault": FakeVault(error=IndexNotBuiltError())}),
    )
    for n, (case, kwargs) in enumerate(refusals):
        refused = setup(fresh(tmp, f"refused{n}"), **kwargs)  # type: ignore[arg-type]
        refused_app = create_app(TOKEN, questionnaire=refused.runs)
        rec.add("questionnaire_start", case, hit(refused_app, "POST", "/questionnaire/runs", body))


def world_vault_index(rec: Recorder, tmp: Path) -> None:
    embedder = GatedEmbedder()
    r = rig(fresh(tmp, "index"), embedder=embedder)
    app = create_app(TOKEN, index_jobs=r.jobs)

    async def go() -> None:
        started = rec.add("vault_index_start", "accepted", await send(app, "POST", "/vault/index"))
        job = f"/vault/index/{started.json()['job_id']}"
        await embedder.entered.wait()
        rec.add("vault_index_start", "run_in_progress", await send(app, "POST", "/vault/index"))
        rec.add("vault_index_status", "running", await send(app, "GET", job))
        embedder.release.set()
        await r.jobs._active  # the job ends
        rec.add("vault_index_status", "complete", await send(app, "GET", job))
        rec.add(
            "vault_index_status",
            "run_not_found",
            await send(app, "GET", f"/vault/index/{SOME_RUN}"),
        )
        rec.add(
            "vault_index_status", "invalid_request", await send(app, "GET", "/vault/index/nope")
        )
        await r.repo.close()

    run_scenario(go())
    missing = rig(fresh(tmp, "missing"), gate=FakeGate(ModelsUnavailableError((EMBEDDING,))))
    missing_app = create_app(TOKEN, index_jobs=missing.jobs)
    rec.add("vault_index_start", "models_unavailable", hit(missing_app, "POST", "/vault/index"))
    run_scenario(missing.repo.close())


def world_ledger_import(rec: Recorder, tmp: Path) -> None:
    with draft_for(fresh(tmp, "import"), supported_outcomes(4), data=fixed_workbook(4)) as d:
        app = create_app(TOKEN, ledger=d.service)
        folder = d.export_root
        (folder / "Copy.xlsx").write_bytes(d.path.read_bytes())  # a copy has no run record
        (folder / "Hostile.xlsx").write_bytes(
            rebuild(d.path.read_bytes(), replace={"xl/worksheets/sheet1.xml": DOCTYPE})
        )
        edit_cells(d, {(2, QUESTION_COL): "A question that was changed?"}, name="Edited.xlsx")
        file = d.path.name
        draft, imp = "/questionnaire/draft", "/ledger/import"
        rec.add("draft_rows", "ok", hit(app, "POST", draft, {"file": file}))
        for case, name in (
            ("file_not_found", "Missing.xlsx"),
            ("path_not_allowed", "../x.xlsx"),
            ("draft_refused", "Copy.xlsx"),
        ):
            rec.add("draft_rows", case, hit(app, "POST", draft, {"file": name}))
        rec.add("draft_rows", "invalid_request", hit(app, "POST", draft, {}))
        no_export = create_app(TOKEN, ledger=LedgerService(d.repo, None, d.clock))
        rec.add(
            "draft_rows",
            "export_root_not_configured",
            hit(no_export, "POST", draft, {"file": file}),
        )
        rec.add(
            "ledger_import", "export_root_not_configured", hit(no_export, "POST", imp, IMPORT_BODY)
        )

        rec.add("ledger_customers", "empty", hit(app, "GET", "/ledger/customers"))
        rec.add("ledger_cited_paths", "empty", hit(app, "GET", "/ledger/cited-paths"))
        rec.add("ledger_promises", "empty", hit(app, "GET", "/ledger/promises"))

        approve(d, 2, 3, 4)
        edit_cells(d, {(5, 8): "rejected"})
        body = {**IMPORT_BODY, "run_id": d.run_id, "file": file}
        rec.add("ledger_import", "preview", hit(app, "POST", imp, {**body, "preview": True}))
        real = rec.add("ledger_import", "created", hit(app, "POST", imp, body))
        customer = real.json()["customer_id"]
        existing = {**body, "new_customer_name": None, "confirm_new": False}
        existing["existing_customer_id"] = customer
        rec.add(
            "ledger_import", "preview_again", hit(app, "POST", imp, {**existing, "preview": True})
        )
        refusals: list[tuple[str, dict[str, object]]] = [
            ("customer_choice_invalid", {**body, "new_customer_name": None, "confirm_new": False}),
            ("run_record_missing", {**body, "run_id": SOME_RUN}),
            ("question_mismatch", {**body, "file": "Edited.xlsx"}),
            ("new_customer_not_confirmed", {**body, "confirm_new": False}),
            ("customer_exists", body),
            ("customer_unknown", {**existing, "existing_customer_id": "b" * 32}),
            ("workbook_refused", {**body, "file": "Hostile.xlsx"}),
            ("file_not_found", {**body, "file": "Missing.xlsx"}),
            ("path_not_allowed", {**body, "file": "../x.xlsx"}),
            ("invalid_request", {**body, "sent_on": "yesterday"}),
        ]
        for case, bad in refusals:
            rec.add("ledger_import", case, hit(app, "POST", imp, bad))

        rec.add("ledger_customers", "one_customer", hit(app, "GET", "/ledger/customers"))
        rec.add("ledger_cited_paths", "one_path", hit(app, "GET", "/ledger/cited-paths"))
        promises = rec.add("ledger_promises", "page", hit(app, "GET", "/ledger/promises"))
        first, second = [p["promise_id"] for p in promises.json()["promises"]][:2]
        rec.add(
            "ledger_promises",
            "invalid_request",
            hit(app, "GET", "/ledger/promises", None, {"page": 0}),
        )

        void = f"/ledger/promises/{first}/void"
        rec.add("ledger_void", "ok", hit(app, "POST", void, {"reason": "other"}))
        rec.add("ledger_void", "promise_voided", hit(app, "POST", void, {"reason": "other"}))
        unknown_void = f"/ledger/promises/{SOME_PROMISE}/void"
        rec.add(
            "ledger_void", "promise_unknown", hit(app, "POST", unknown_void, {"reason": "other"})
        )
        rec.add("ledger_void", "invalid_request", hit(app, "POST", void, {"reason": "because"}))
        listing = hit(
            app, "GET", "/ledger/promises", None, {"include_voided": True, "page_size": 1}
        )
        rec.add("ledger_promises", "voided_included_small_page", listing)

        ack = f"/ledger/promises/{second}/acknowledge"
        rec.add(
            "ledger_acknowledge",
            "acknowledge_stale",
            hit(app, "POST", ack, {"seen_recall_event_id": 1}),
        )
        unknown_ack = f"/ledger/promises/{SOME_PROMISE}/acknowledge"
        rec.add(
            "ledger_acknowledge",
            "promise_unknown",
            hit(app, "POST", unknown_ack, {"seen_recall_event_id": 1}),
        )
        rec.add(
            "ledger_acknowledge",
            "invalid_request",
            hit(app, "POST", ack, {"seen_recall_event_id": 0}),
        )


def world_recall_and_blast(rec: Recorder, tmp: Path, patch: pytest.MonkeyPatch) -> None:
    other = policy_text(HEADING, BACKUP_LINE, KEYS).replace("ENC-001", "BAK-001")
    cites = [(POLICY, QUOTE), (OTHER_POLICY, BACKUP_LINE)]
    with scenario(
        fresh(tmp, "recall"), files={POLICY: BASE, OTHER_POLICY: other}, cites=cites
    ) as s:
        app = create_app(TOKEN, ledger=s.d.service)
        recall, blast = "/ledger/recall", "/ledger/blast-radius"
        first = rec.add("ledger_recall", "intact", hit(app, "POST", recall, {}))
        old_event = first.json()["items"][0]["recall_event_id"]
        set_text(s.vault, POLICY, policy_text(HEADING, "Gone.", KEYS))
        second = rec.add("ledger_recall", "changed", hit(app, "POST", recall, {}))
        changed = next(i for i in second.json()["items"] if i["state"] == "changed")
        ack = f"/ledger/promises/{changed['promise_id']}/acknowledge"
        newest = {"seen_recall_event_id": changed["recall_event_id"]}
        rec.add(
            "ledger_acknowledge",
            "acknowledge_stale",
            hit(app, "POST", ack, {"seen_recall_event_id": old_event}),
        )
        rec.add("ledger_acknowledge", "ok", hit(app, "POST", ack, newest))
        rec.add("ledger_cited_paths", "two_paths", hit(app, "GET", "/ledger/cited-paths"))
        rec.add("ledger_promises", "acknowledged", hit(app, "GET", "/ledger/promises"))

        edited = policy_text(HEADING, "Data at rest is encrypted.", KEYS)
        rec.add(
            "ledger_blast_radius",
            "proposed_text",
            hit(app, "POST", blast, {"path": POLICY, "proposed_text": edited}),
        )
        rec.add(
            "ledger_blast_radius",
            "delete_file",
            hit(app, "POST", blast, {"path": POLICY, "delete_file": True}),
        )
        rec.add(
            "ledger_blast_radius",
            "no_match",
            hit(app, "POST", blast, {"path": "policies/none.md", "delete_file": True}),
        )
        nul = {"path": POLICY, "proposed_text": "a\x00b"}
        rec.add("ledger_blast_radius", "proposed_text_has_nul", hit(app, "POST", blast, nul))
        both = {"path": POLICY, "proposed_text": "x", "delete_file": True}
        rec.add("ledger_blast_radius", "invalid_request", hit(app, "POST", blast, both))

        patch.setattr(ledger_service_module, "MAX_RECALL_PROMISES", 1)
        patch.setattr(ledger_service_module, "MAX_BLAST_PROMISES", 0)  # one promise cites POLICY
        patch.setattr(ledger_service_module, "MAX_CITED_PATHS", 1)
        rec.add("ledger_recall", "too_many_promises", hit(app, "POST", recall, {}))
        rec.add(
            "ledger_blast_radius",
            "too_many_promises",
            hit(app, "POST", blast, {"path": POLICY, "delete_file": True}),
        )
        rec.add("ledger_cited_paths", "too_many_paths", hit(app, "GET", "/ledger/cited-paths"))
        patch.undo()

        no_vault = LedgerService(s.d.repo, s.d.export_root, s.d.clock, None)
        no_vault_app = create_app(TOKEN, ledger=no_vault)
        rec.add("ledger_recall", "vault_root_not_configured", hit(no_vault_app, "POST", recall, {}))
        rec.add(
            "ledger_blast_radius",
            "vault_root_not_configured",
            hit(no_vault_app, "POST", blast, {"path": POLICY, "delete_file": True}),
        )


def world_broken_ledgers(rec: Recorder, tmp: Path) -> None:
    """A ledger file of another schema, and a ledger path that is a folder: every ledger route."""
    clock = ManualClock()
    vault, exports = tmp / "vault", tmp / "exports"
    vault.mkdir()
    exports.mkdir()
    (exports / FILE_NAME).write_bytes(b"x")  # so the draft route gets as far as the ledger
    other_schema, a_folder = tmp / "other-schema", tmp / "a-folder"
    other_schema.mkdir()
    (a_folder / "ledger.sqlite3").mkdir(parents=True)
    with sqlite3.connect(other_schema / "ledger.sqlite3") as con:
        con.execute("CREATE TABLE schema_info (version INTEGER NOT NULL)")
        con.execute("INSERT INTO schema_info (version) VALUES (99)")
    for case, folder in (("ledger_unusable", other_schema), ("ledger_store_error", a_folder)):
        repo = SqliteLedgerRepository(folder / "ledger.sqlite3", clock)
        app = create_app(TOKEN, ledger=LedgerService(repo, exports, clock, vault))
        for route in LEDGER_ROUTES:
            rec.add(route, case, minimal(app, route))
        run_scenario(repo.close())


def world_reality(rec: Recorder, tmp: Path, patch: pytest.MonkeyPatch) -> None:
    runtime, claim = demo_runtime(fresh(tmp, "reality"))
    imports = runtime.import_root
    write(imports, csv_bytes([row()], headers=["a", "b"]), "bad.csv")  # required columns missing
    app = create_app(TOKEN, reality=runtime)
    inspect, check = "/reality/inspect", "/reality/check"
    rec.add("reality_inspect", "ok", hit(app, "POST", inspect, {"file": "okta.csv"}))
    for case, name in (
        ("file_not_found", "missing.csv"),
        ("path_not_allowed", "../x.csv"),
        ("export_refused", "bad.csv"),
    ):
        rec.add("reality_inspect", case, hit(app, "POST", inspect, {"file": name}))
        rec.add("reality_check", case, hit(app, "POST", check, body_for(claim, file=name)))
    rec.add("reality_inspect", "invalid_request", hit(app, "POST", inspect, {}))
    rec.add("reality_claims", "ok", hit(app, "GET", "/reality/claims"))
    rec.add("reality_check", "ok", hit(app, "POST", check, body_for(claim)))
    altered = {**claim, "quote": f"{claim['quote']} (changed)"}
    rec.add("reality_check", "claim_not_verbatim", hit(app, "POST", check, body_for(altered)))
    future = body_for(claim, exported_at="2099-01-01")
    rec.add("reality_check", "exported_at_in_future", hit(app, "POST", check, future))
    rec.add("reality_check", "invalid_request", hit(app, "POST", check, {}))

    patch.setattr(controlmap_module, "MAX_DOCUMENTS", 1)
    rec.add("reality_claims", "control_map_too_large", hit(app, "GET", "/reality/claims"))
    rec.add("reality_check", "control_map_too_large", hit(app, "POST", check, body_for(claim)))
    patch.undo()

    def broken() -> tuple[str, ...]:
        raise AuthControlsError

    patch.setattr(reality_claims_module, "auth_control_ids", broken)
    rec.add("reality_claims", "auth_controls_invalid", hit(app, "GET", "/reality/claims"))
    rec.add("reality_check", "auth_controls_invalid", hit(app, "POST", check, body_for(claim)))
    patch.undo()


def world_contradictions(rec: Recorder, tmp: Path) -> None:
    start, runs_url = "/contradictions/runs", "/contradictions/runs/"

    async def real() -> None:
        steps = repeat(reply(True), 50)
        async with real_runs(CONTRADICTIONS_VAULT, tmp / "idx", steps) as (runs, _):
            app = create_app(TOKEN, contradictions=runs)
            started = rec.add(
                "contradictions_start", "accepted", await send(app, "POST", start, {"cap": 3})
            )
            run_id = started.json()["run_id"]
            for _ in range(500):
                done = await send(app, "GET", runs_url + run_id)
                if done.json()["status"] != "running":
                    break
                await asyncio.sleep(0.01)
            rec.add("contradictions_status", "complete", done)
            rec.add(
                "contradictions_status",
                "run_not_found",
                await send(app, "GET", runs_url + SOME_RUN),
            )
            rec.add(
                "contradictions_status", "invalid_request", await send(app, "GET", runs_url + "XYZ")
            )
            rec.add(
                "contradictions_start",
                "invalid_request",
                await send(app, "POST", start, {"cap": 0}),
            )
            await runs.aclose()

    run_scenario(real())

    async def scripted() -> None:
        held = ScriptedDetector(total=2)
        runs = ContradictionRuns(held, JobSlot(), MODEL)
        app = create_app(TOKEN, contradictions=runs)
        run_id = (await send(app, "POST", start, {})).json()["run_id"]
        await held.entered.wait()
        rec.add("contradictions_status", "running", await send(app, "GET", runs_url + run_id))
        rec.add("contradictions_start", "run_in_progress", await send(app, "POST", start, {}))
        await runs.aclose()
        rec.add("contradictions_status", "cancelled", await send(app, "GET", runs_url + run_id))

        failing = ScriptedDetector(total=0, error=RuntimeError("boom"))
        failing_runs = ContradictionRuns(failing, JobSlot(), MODEL)
        failing_app = create_app(TOKEN, contradictions=failing_runs)
        failed_id = (await send(failing_app, "POST", start, {})).json()["run_id"]
        for _ in range(500):
            failed = await send(failing_app, "GET", runs_url + failed_id)
            if failed.json()["status"] != "running":
                break
            await asyncio.sleep(0.01)
        rec.add("contradictions_status", "failed", failed)

        repo = SqliteVecRepository(tmp / "missing.sqlite3")
        empty = ContradictionRuns(ContradictionDetector(repo), JobSlot(), MODEL)
        refused = await send(create_app(TOKEN, contradictions=empty), "POST", start, {})
        rec.add("contradictions_start", "index_not_built", refused)
        await repo.close()

    run_scenario(scripted())


def world_graph(rec: Recorder, tmp: Path, patch: pytest.MonkeyPatch) -> None:
    vault = make_vault(tmp / "graph")

    async def counts() -> Counter[str]:
        return Counter({"alpha.md": 2, "beta.md": 1})

    plain = create_app(TOKEN, vault_graph=VaultGraphService(vault, None))
    rec.add("vault_graph", "no_ledger_folder", hit(plain, "GET", "/vault/graph"))
    with_ledger = create_app(TOKEN, vault_graph=VaultGraphService(vault, counts))
    rec.add("vault_graph", "with_ledger", hit(with_ledger, "GET", "/vault/graph"))
    patch.setattr(graph_module, "MAX_GRAPH_NODES", 7)
    rec.add("vault_graph", "graph_too_large", hit(plain, "GET", "/vault/graph"))
    patch.undo()


# ----- normalizing, writing and comparing ---------------------------------------------------------

HEX32 = r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])"
UUID = r"(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-f])"


def normalize(cases: list[dict[str, object]], route: str) -> str:
    """The route's replies as text, with every id replaced in order of appearance.

    A 32-digit hex id becomes `00000001` plus 24 zeros, then `00000002` plus 24 zeros, ...; a dashed
    promise id becomes `00000000-0000-4000-8000-000000000001`, ...; and the first 8 digits of a
    replaced run id, which ALIBI puts in the output file's name, follow it.
    """
    text = json.dumps({"route": route, "replies": cases}, indent=2) + "\n"
    fixed: dict[str, str] = {}
    for match in re.finditer(f"{HEX32}|{UUID}", text):
        real = match.group(0)
        if real not in fixed:
            number = sum(1 for other in fixed if len(other) == len(real)) + 1
            fixed[real] = (
                f"{number:08x}" + "0" * 24
                if len(real) == 32
                else f"00000000-0000-4000-8000-{number:012x}"
            )
    text = re.sub(f"{HEX32}|{UUID}", lambda m: fixed[m.group(0)], text)
    # What was a run id's first 8 digits (in a file name) follows the id it came from.
    for real, replacement in fixed.items():
        if len(real) == 32:
            text = re.sub(f"(?<![0-9a-f]){real[:8]}(?![0-9a-f])", replacement[:8], text)
    return text


def collect(tmp: Path, patch: pytest.MonkeyPatch) -> Recorder:
    rec = Recorder()
    world_nothing_configured(rec)
    world_readiness(rec)
    world_questionnaire(rec, fresh(tmp, "q"))
    world_vault_index(rec, fresh(tmp, "v"))
    world_ledger_import(rec, fresh(tmp, "l"))
    world_recall_and_blast(rec, fresh(tmp, "r"), patch)
    world_broken_ledgers(rec, fresh(tmp, "b"))
    world_reality(rec, fresh(tmp, "re"), patch)
    world_contradictions(rec, fresh(tmp, "c"))
    world_graph(rec, fresh(tmp, "g"), patch)
    return rec


def families(cases: list[dict[str, object]]) -> set[str]:
    found: set[str] = set()
    for case in cases:
        body = case["body"]
        if isinstance(body, dict) and "error" in body:
            found.add(str(body["error"]))
    return found


def test_the_collected_replies_cover_every_route_and_every_error_family(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = collect(tmp_path, monkeypatch)
    assert set(rec.cases) == set(MINIMAL), "a route has no reply, or a route is not listed"
    for route, cases in rec.cases.items():
        statuses = {case["status"] for case in cases}
        assert statuses & {200, 202}, f"{route} has no success reply"
        assert families(cases) >= REQUIRED_ERRORS[route], (
            route,
            sorted(REQUIRED_ERRORS[route] - families(cases)),
        )


def test_every_reply_matches_its_file_in_fixtures_contracts_replies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = collect(tmp_path, monkeypatch)
    produced = {route: normalize(cases, route) for route, cases in rec.cases.items()}
    if os.environ.get(WRITE_SWITCH) == "1":
        REPLIES.mkdir(parents=True, exist_ok=True)
        for stale in REPLIES.glob("*.json"):
            if stale.stem not in produced:
                stale.unlink()
        for route, text in produced.items():
            (REPLIES / f"{route}.json").write_text(text, encoding="utf-8", newline="\n")
        pytest.fail(f"{WRITE_SWITCH}=1 wrote {len(produced)} files; run again without it")
    on_disk = {path.stem for path in REPLIES.glob("*.json")}
    assert on_disk == set(produced), (
        f"files and routes differ: missing {sorted(set(produced) - on_disk)}, "
        f"extra {sorted(on_disk - set(produced))}; regenerate with {WRITE_SWITCH}=1"
    )
    different = [
        route
        for route, text in produced.items()
        if (REPLIES / f"{route}.json").read_text(encoding="utf-8") != text
    ]
    assert not different, (
        f"replies changed for {different}; if on purpose, regenerate with {WRITE_SWITCH}=1"
    )


def test_the_normalizer_replaces_ids_in_order_of_appearance_and_nothing_else() -> None:
    first, second = "ab12cd34" + "e" * 24, "0f" * 16
    promise = "1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed"
    sha = "c" * 64  # not an id: a 64-digit hash is left alone
    cases = [
        {"case": "x", "status": 200, "body": {"a": first, "b": [second, first], "c": promise}},
        {"case": "y", "status": 200, "body": {"file": f"draft-{first[:8]}.xlsx", "sha": sha}},
    ]
    text = normalize(cases, "demo")
    assert first not in text and second not in text and promise not in text
    assert "00000001" + "0" * 24 in text and "00000002" + "0" * 24 in text
    assert "00000000-0000-4000-8000-000000000001" in text
    assert "draft-00000001.xlsx" in text  # the file name follows the id it came from
    assert sha in text
    assert normalize(cases, "demo") == text  # the same input, the same text
    # The same replies with other random ids make the same text: that is the point of the step.
    renamed = json.dumps(cases)
    for real, other in (
        (first, "12345678" + "f" * 24),
        (second, "9" * 32),
        (promise, "2b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bee"),
        (first[:8], "12345678"),
    ):
        renamed = renamed.replace(real, other)
    assert renamed != json.dumps(cases)
    assert normalize(json.loads(renamed), "demo") == text


def test_the_ids_of_one_run_do_not_change_the_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two collections in two folders use different random ids (customers, promises, runs) yet make
    # the same text, so a fixture never differs from one machine or one run to another.
    first = collect(tmp_path / "one", monkeypatch)
    second = collect(tmp_path / "two", monkeypatch)
    for route in first.cases:
        assert normalize(first.cases[route], route) == normalize(second.cases[route], route), route
