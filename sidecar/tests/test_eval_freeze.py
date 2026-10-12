"""The evaluation freeze (ADR-013, decision 16): the labels, the vaults and the hostile cell texts
are pinned by hash, so none of them can change without this test failing.

The freeze is set at M5g. After the first live run, any label edit, vault edit or prompt change
needs a tuning-log entry in ADR-013 and the owner's approval; when one is approved, the pinned
value here is updated in the same commit as the log entry. The prompt template has its own pin
(`test_prompt.py::test_the_template_hash_is_recorded_and_pinned`).

Hashes are independent of line endings: the splits and the cell texts are hashed as canonical JSON,
and each note as the indexer hashes it (normalized text, LF endings, no byte-order mark).
"""

import hashlib
import json
from datetime import date

import pytest

from grc_sidecar.vault.scan import scan_vault

from .evalkit import load_eval_set, split_sha256
from .vaultkit import ADVERSARIAL_M5_CELLS, EVAL_SET, REPO_ROOT

FROZEN_CLOCK_TODAY = date(2026, 10, 10)
FROZEN_SPLITS = {
    "dev": "20a32f2d0d45d161e03178e67290a19020b10623979051d5c3dd7cc84faf7a5a",
    "heldout": "11063d1916f10c6e64ce2ec1cde73f1a683c89bea329f79c3fc6dd3b861a6dfe",
}
FROZEN_CELLS = "b24369af4d5932e4c82a6b3c4b7c8dda68d4052500e1154a5866f16d7a4ec169"
FROZEN_VAULT_FILES = {
    "vault-demo/vault/acceptable-use-policy.md": (
        "64d8ad1c06c786e91c3c5ededc55f8f6c59b0c3b1342845cfeaa4f18b2945caf"
    ),
    "vault-demo/vault/access-control-policy.md": (
        "4c5e77d8eb95f75569ebe9a6bf46f7930afd25a3cf91b619a61a8645d69315b9"
    ),
    "vault-demo/vault/backup-and-recovery-standard.md": (
        "d9ebefafbba380f3681dbb46279d94b7dbaf4d9b06d6cee7390dddbf8fe9353b"
    ),
    "vault-demo/vault/change-management-policy.md": (
        "4f06620f8ab337de2f3376337294d74e165ac177db59db22110fc1aac9ee4c3d"
    ),
    "vault-demo/vault/data-retention-policy.md": (
        "4ff157dd73a41a8df971b9ce2d286bf06944c9e46ea67fb9c071552d8776da79"
    ),
    "vault-demo/vault/encryption-policy.md": (
        "0ac9ea4708bcbf0ef43d72357b690d18e7368a7787bf11bf947c82471d5f26d3"
    ),
    "vault-demo/vault/incident-response-plan.md": (
        "27b9f7e1357bc3167bccbe2f8b781007137a24ef8fc2c409fe3b4b0266e5d5cc"
    ),
    "vault-demo/vault/mfa-enrollment-procedure.md": (
        "8b38d2930e7dfafb65d1e6a67fe6d1251a40347514eba79c25d3d6e6ad0a221c"
    ),
    "vault-demo/vault/monitoring-standard.md": (
        "71f3ed0c202c4a5da56ff5906e4a7c6bcf3d27c368e9c3aa9f306afbdca4844a"
    ),
    "vault-demo/vault/security-awareness-standard.md": (
        "2eeae7423d384fa886266c3ac3f37281caccc43acd9e661fdbfbd25731c8ede5"
    ),
    "vault-demo/vault/vendor-management-policy.md": (
        "04591b41330122a086a3d528c96935f8add2f801275bf5155c8c55d5d348515f"
    ),
    "vault-demo/vault/vulnerability-management-procedure.md": (
        "a9587829211a3a929de4ee3c159d7f7a7f36220e2689b90f497dddecba046012"
    ),
    "fixtures/adversarial-m5/vault/contact-sheet.md": (
        "99336c56cbb485624e28a4a7abbd03099aeae2ff847868c7d99ead37edfd8230"
    ),
    "fixtures/adversarial-m5/vault/contractor-note.md": (
        "30f03f83a5a5e11c75822e320b21480f8f6b37eba12516066bc5ae605ebe5380"
    ),
    "fixtures/adversarial-m5/vault/guest-wifi-note.md": (
        "5daa261ea2d5a87b99e64fcbc05e9ddf3c216fa1d446078cb7e67e6372885eb5"
    ),
    "fixtures/adversarial-m5/vault/printer-note.md": (
        "bea57bb2f7932e4a1145beca1cdc5bb2ca38776a0b1f126e6cb7a456ea870806"
    ),
}


def vault_hashes() -> dict[str, str]:
    evalset = load_eval_set(EVAL_SET)
    found: dict[str, str] = {}
    for root in evalset.vault_roots:
        for doc in scan_vault(REPO_ROOT / root).documents:
            found[f"{root}/{doc.source_path}"] = doc.file_sha256
    return found


def test_the_clock_is_the_frozen_one() -> None:
    assert load_eval_set(EVAL_SET).clock_today == FROZEN_CLOCK_TODAY


@pytest.mark.parametrize("split", ["dev", "heldout"])
def test_a_split_has_not_changed_since_the_freeze(split: str) -> None:  # [NEG]
    """Failing here means a label was edited. After the first live run that needs a tuning-log
    entry and the owner's approval (ADR-013), and then this pin is updated with it."""
    assert split_sha256(load_eval_set(EVAL_SET), split) == FROZEN_SPLITS[split]  # type: ignore[arg-type]


def test_no_vault_note_has_changed_since_the_freeze() -> None:  # [NEG]
    assert vault_hashes() == FROZEN_VAULT_FILES


def test_the_hostile_cell_texts_have_not_changed_since_the_freeze() -> None:  # [NEG]
    data = json.loads(ADVERSARIAL_M5_CELLS.read_text(encoding="utf-8"))
    text = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    assert hashlib.sha256(text.encode("ascii")).hexdigest() == FROZEN_CELLS
