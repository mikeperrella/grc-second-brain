"""The offline draft tool (ADR-017 decision 16): no model client, no network, folders that may not
overlap, nothing overwritten, and a draft that imports unedited with every row unchanged.

The tool is driven through its own `main()`, exactly as the command line does, and only what it
prints is read: the tests never look inside it.
"""

import ast
import hashlib
import re
import socket
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from openpyxl.utils import column_index_from_string

from grc_sidecar.llm.generation_client import GenerationClient
from grc_sidecar.llm.ollama_embeddings import OllamaEmbeddingClient
from grc_sidecar.questionnaire.loader import load_questionnaire
from grc_sidecar.vault.clock import SystemClock

from . import offline_draft
from .vaultkit import DEMO_VAULT
from .xlsxkit import to_bytes

pytestmark = pytest.mark.usefixtures("clean_grc_env")

REAL_OLLAMA_PORT = 11434
ROW = re.compile(r"^row (\d+) (D-\d+) (SUPPORTED|PARTIAL|NEEDS_REVIEW) (\S+)$", re.MULTILINE)
SKIPPED = re.compile(
    r"^skipped not_approved=(\d+) no_answer=(\d+) status_edited=(\d+) evidence_edited=(\d+) "
    r"baseline_unavailable=(\d+) already_imported=(\d+)$",
    re.MULTILINE,
)


class Folders:
    def __init__(self, base: Path) -> None:
        self.base = base
        self.work = base / "work"
        self.questionnaires = base / "questionnaires"
        self.exports = base / "exports"
        self.ledger = base / "ledger"

    def all(self) -> list[Path]:
        return [self.work, self.questionnaires, self.exports, self.ledger]

    def draft_args(self) -> list[str]:
        return [
            "draft",
            "--work-dir",
            str(self.work),
            "--questionnaire-dir",
            str(self.questionnaires),
            "--export-dir",
            str(self.exports),
            "--ledger-dir",
            str(self.ledger),
        ]


def check_args(folders: Folders, run_id: str, file: str, *extra: str) -> list[str]:
    return [
        "import-check",
        "--ledger-dir",
        str(folders.ledger),
        "--export-dir",
        str(folders.exports),
        "--run-id",
        run_id,
        "--file",
        file,
        "--new-customer-name",
        "Acme Offline Co",
        *extra,
    ]


def run_tool(capsys: pytest.CaptureFixture[str], args: list[str]) -> tuple[int, str, str]:
    code = offline_draft.main(args)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def hash_tree(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def drafted(
    capsys: pytest.CaptureFixture[str], folders: Folders
) -> tuple[str, str, int, list[tuple[int, str, str, str]]]:
    """Run `draft`; return (run id, draft file name, Review column index, the printed rows)."""
    code, out, err = run_tool(capsys, folders.draft_args())
    assert code == 0, err
    run_id = re.search(r"^run_id ([0-9a-f]+)$", out, re.MULTILINE)
    name = re.search(r"^draft (.+)$", out, re.MULTILINE)
    letter = re.search(r"^review_column ([A-Z]+)$", out, re.MULTILINE)
    assert run_id and name and letter, out
    rows = [(int(r), ref, status, reasons) for r, ref, status, reasons in ROW.findall(out)]
    return run_id.group(1), name.group(1), column_index_from_string(letter.group(1)), rows


def approve_rows(path: Path, review_column: int, rows: list[int]) -> None:
    """What the owner does in Excel: type `approved` in the Review column, press Save."""
    loaded = load_questionnaire(path.read_bytes())
    try:
        sheet = loaded.workbook["Questions"]
        for row in rows:
            sheet.cell(row=row, column=review_column).value = "approved"
        data = to_bytes(loaded.workbook)
    finally:
        loaded.close()
    path.write_bytes(data)


# ----- the tool builds no model client and touches no network -------------------------------------


def test_the_tool_imports_no_http_client_and_never_names_a_model_client() -> None:  # [NEG]
    tree = ast.parse(Path(offline_draft.__file__).read_text(encoding="utf-8"))
    modules: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    assert not [m for m in modules if m.split(".")[0] == "httpx"]
    for forbidden in ("security.net", "generation_client", "ollama_embeddings", "model_gate"):
        assert not [m for m in modules if forbidden in m], forbidden
    clients = {
        "GenerationClient",
        "OllamaEmbeddingClient",
        "AsyncClient",
        "Client",
        "create_async_client",
        "create_client",
        "build_runtime",
        "ModelGate",
    }
    assert names.isdisjoint(clients), names & clients


def test_a_whole_draft_and_import_build_no_client_and_never_connect_to_ollama(  # [NEG]
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a model or http client was built")

    for client in (httpx.Client, httpx.AsyncClient, GenerationClient, OllamaEmbeddingClient):
        monkeypatch.setattr(client, "__init__", boom)
    connected: list[object] = []
    original = socket.socket.connect

    def record(self: socket.socket, address: object) -> None:
        connected.append(address)
        original(self, address)  # type: ignore[arg-type]  # still through the egress guard

    monkeypatch.setattr(socket.socket, "connect", record)

    folders = Folders(tmp_path)
    run_id, name, column, rows = drafted(capsys, folders)
    approve_rows(folders.exports / name, column, [r for r, _, s, _ in rows if s != "NEEDS_REVIEW"])
    code, _, err = run_tool(capsys, check_args(folders, run_id, name))
    assert code == 0, err
    for address in connected:  # the event loop's own loopback socket pair is the only kind allowed
        assert isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1")
        assert address[1] != REAL_OLLAMA_PORT


# ----- the round trip -----------------------------------------------------------------------------


def test_a_draft_reviewed_in_place_imports_with_every_row_unedited(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    demo = offline_draft._load_demo_tool()
    vault_before = hash_tree(DEMO_VAULT)
    folders = Folders(tmp_path)

    run_id, name, column, rows = drafted(capsys, folders)
    statuses = [status for _, _, status, _ in rows]
    assert len(rows) == len(demo.DEMO_QUESTIONS) == 15
    assert [ref for _, ref, _, _ in rows] == [q.ref for q in demo.DEMO_QUESTIONS]
    # The real engine computed these: the demo's three kinds of row all appear.
    assert statuses.count("SUPPORTED") >= 1 and statuses.count("NEEDS_REVIEW") >= 1
    partial = [(ref, reasons) for _, ref, status, reasons in rows if status == "PARTIAL"]
    assert partial == [("D-11", "review_overdue")]  # the vendor policy's review date is long past

    answered = [row for row, _, status, _ in rows if status != "NEEDS_REVIEW"]
    approve_rows(folders.exports / name, column, answered)

    code, out, err = run_tool(capsys, check_args(folders, run_id, name))
    assert code == 0 and "refused" not in err, err
    skipped = SKIPPED.search(out)
    assert skipped is not None
    not_approved, no_answer, status_edited, evidence_edited, baseline, already = map(
        int, skipped.groups()
    )
    assert (no_answer, status_edited, evidence_edited, baseline, already) == (0, 0, 0, 0, 0)
    assert not_approved == statuses.count("NEEDS_REVIEW")
    assert f"created {len(answered)} (edited_after_draft 0)" in out
    created = re.findall(r"^promise row (\d+) edited_after_draft (True|False)$", out, re.MULTILINE)
    assert sorted(int(row) for row, _ in created) == sorted(answered)
    assert {flag for _, flag in created} == {"False"}
    assert "customer_created yes" in out

    # What the tool printed holds no question, no quote and no company name.
    shown = out + err
    assert "Marlowe" not in shown
    for question in demo.DEMO_QUESTIONS:
        assert question.question not in shown
    for found in offline_draft.QUOTES.values():
        if found is not None:
            assert found[1] not in shown

    # The vault copy is a copy; the original was only read. Files exist only in the named folders.
    assert hash_tree(DEMO_VAULT) == vault_before
    assert hash_tree(folders.work / "vault") == vault_before
    for path in (p for p in tmp_path.rglob("*") if p.is_file()):
        assert any(path.is_relative_to(folder) for folder in folders.all()), path


def test_an_unreviewed_draft_imports_nothing_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folders = Folders(tmp_path)
    run_id, name, _, rows = drafted(capsys, folders)
    code, out, err = run_tool(capsys, check_args(folders, run_id, name))
    assert code == 0, err
    assert "created 0 (edited_after_draft 0)" in out and "customer_created no" in out
    skipped = SKIPPED.search(out)
    assert skipped is not None and int(skipped.group(1)) == len(rows)


def test_the_sent_date_defaults_to_today_and_is_checked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folders = Folders(tmp_path)
    run_id, name, column, rows = drafted(capsys, folders)
    approve_rows(folders.exports / name, column, [rows[0][0]])
    future = f"{SystemClock().today().year + 1}-01-01"
    code, _, err = run_tool(capsys, check_args(folders, run_id, name, "--sent-on", future))
    assert code == 1 and "sent_on_in_future" in err


# ----- folders that overlap are refused -----------------------------------------------------------


def overlapping_cases(base: Path) -> dict[str, dict[str, Path]]:
    q, e, w, led = base / "q", base / "e", base / "w", base / "l"
    return {
        "export-inside-questionnaires": {"q": q, "e": q / "e", "w": w, "l": led},
        "ledger-equals-export": {"q": q, "e": e, "w": w, "l": e},
        "ledger-inside-work": {"q": q, "e": e, "w": w, "l": w / "ledger"},
        "questionnaires-contain-work": {"q": q, "e": e, "w": q / "w", "l": led},
        "work-inside-export": {"q": q, "e": e, "w": e / "w", "l": led},
        "questionnaires-equal-work": {"q": w, "e": e, "w": w, "l": led},
        "everything-in-one-folder": {"q": q, "e": q, "w": q, "l": q},
    }


@pytest.mark.parametrize("case", sorted(overlapping_cases(Path("x"))))
def test_folders_that_overlap_are_refused_and_nothing_is_created(  # [NEG]
    tmp_path: Path, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    chosen = overlapping_cases(tmp_path)[case]
    args = [
        "draft",
        "--work-dir",
        str(chosen["w"]),
        "--questionnaire-dir",
        str(chosen["q"]),
        "--export-dir",
        str(chosen["e"]),
        "--ledger-dir",
        str(chosen["l"]),
    ]
    code, _, err = run_tool(capsys, args)
    assert code == 1 and "overlap" in err, err
    assert list(tmp_path.iterdir()) == []  # not even an empty folder was made


def test_a_ledger_folder_that_is_a_link_into_the_export_folder_is_refused(  # [NEG]
    tmp_path: Path, capsys: pytest.CaptureFixture[str], symlink_support: None
) -> None:
    folders = Folders(tmp_path)
    folders.exports.mkdir()
    link = tmp_path / "ledger-link"
    link.symlink_to(folders.exports, target_is_directory=True)
    args = folders.draft_args()
    args[args.index("--ledger-dir") + 1] = str(link)
    code, _, err = run_tool(capsys, args)
    assert code == 1 and "overlap" in err, err
    assert list(folders.exports.iterdir()) == [] and not folders.work.exists()


def test_the_environment_cannot_change_which_folders_the_rules_apply_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folders = offline_draft.Folders(tmp_path / "w", tmp_path / "q", tmp_path / "e", tmp_path / "l")
    monkeypatch.setenv("GRC_IMPORT_DIR", str(tmp_path / "q"))  # would overlap, if it were read
    monkeypatch.setenv("GRC_VAULT_DIR", str(tmp_path / "e"))
    settings = offline_draft._check_folders(folders)
    assert settings.import_dir is None and settings.vault_dir == tmp_path / "w" / "vault"


# ----- nothing is overwritten ---------------------------------------------------------------------


def test_a_second_draft_into_the_same_folders_is_refused_and_changes_nothing(  # [NEG]
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folders = Folders(tmp_path)
    drafted(capsys, folders)
    before = hash_tree(tmp_path)
    code, _, err = run_tool(capsys, folders.draft_args())
    assert code == 1 and "already holds" in err, err
    assert hash_tree(tmp_path) == before


def test_import_check_refuses_an_unknown_run_and_a_missing_ledger_without_creating_one(  # [NEG]
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folders = Folders(tmp_path)
    empty = Folders(tmp_path / "other")
    empty.ledger.mkdir(parents=True)
    empty.exports.mkdir()
    code, _, err = run_tool(capsys, check_args(empty, "c" * 32, "x.xlsx"))
    assert code == 1 and "no ledger" in err
    assert list(empty.ledger.iterdir()) == []  # an empty ledger was not created

    _, name, _, _ = drafted(capsys, folders)
    code, _, err = run_tool(capsys, check_args(folders, "c" * 32, name))
    assert code == 1 and "run_record_missing" in err


def test_import_check_refuses_overlapping_ledger_and_export_folders(  # [NEG]
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    same = tmp_path / "same"
    same.mkdir()
    args = [
        "import-check",
        "--ledger-dir",
        str(same),
        "--export-dir",
        str(same),
        "--run-id",
        "a" * 32,
        "--file",
        "x.xlsx",
        "--new-customer-name",
        "X",
    ]
    code, _, err = run_tool(capsys, args)
    assert code == 1 and "overlap" in err
    assert list(same.iterdir()) == []


def test_every_demo_question_has_a_scripted_answer_or_a_stated_blank() -> None:
    demo = offline_draft._load_demo_tool()
    assert [q.ref for q in demo.DEMO_QUESTIONS] == list(offline_draft.QUOTES)
    check: Callable[[Path], None] = offline_draft._check_quotes
    check(DEMO_VAULT)  # every scripted sentence is in the demo vault, word for word
