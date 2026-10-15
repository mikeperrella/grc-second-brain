"""The contradiction evaluation freeze (ADR-019, decision 14), as `test_eval_freeze.py` does for M5.

Pinned by hash, so none of these can change without this test failing:
- the dev split items and the held-out split items (every field of every item: ids, categories,
  quotes and lines, labels, expected statuses, notes), and the two known cases;
- every note in `fixtures/vault-contradictions/` (hashed as the indexer hashes a file: normalized
  text, LF endings, no byte-order mark), so adding, removing or editing a note fails too;
- the prompt template, by `PAIR_TEMPLATE_SHA256` (any wording change changes it).

WHAT THE FREEZE COVERS: that these files and this template are the ones that were fixed before
the live runs. WHAT IT DOES NOT COVER: it does not prove the labels are right (the owner reviewed
the held-out labels at STOP C1, and pairs CD-115 to CD-117 are the owner's, written by a reviewer
assistant that had read the extraction rules, so they are not blind); it proves they did not change
after the freeze. It does not pin the evaluation code (`contradictionlivekit.py`), the detector's
extraction rules or the demo-vault notes of the two known cases (those are pinned by
`test_eval_freeze.py`), and it says nothing about what a model will answer.

If a pin has to change, that is a tuning-log entry in ADR-019 and the owner's approval, and the new
value goes in the same commit as the entry. Hashes are independent of line endings.
"""

import hashlib
import json
from collections.abc import Sequence

import pytest
from pydantic import BaseModel

from grc_sidecar.contradictions.verdict import PAIR_TEMPLATE_SHA256
from grc_sidecar.vault.scan import scan_vault

from .contradictionkit import load_set
from .vaultkit import CONTRADICTIONS_VAULT

FROZEN_SPLITS = {
    "dev": "650d72221a052a57e4290e685b9c5590c13c7307bbadd95f1b20dc45ca6c0e14",
    "heldout": "36d443c64ac64d754560c355c6ac44aca9e0fdcd01620a98774744a958e2ceac",
}
FROZEN_KNOWN_CASES = "836ca1eb6c9e8b33356bce3b9023f405c199f9f8d81943703671d166bf3559bd"
FROZEN_TEMPLATE = "70898002f9b13eeffb3ba0f904466aba6f2662e3d5c5e80b6c5aaed10ce1e1e3"
FROZEN_NOTES = {
    "framework-excerpt.md": "819b2b6915d660448bf27f6e1ef22acd76a6221b72b2ddcbec1aa5683bcaf8cd",
    "housekeeping-notes-a.md": "61b9a60b5f34bc0c069d9deb3015aecc0f10d55982a7d453d4d0cf6591666525",
    "housekeeping-notes-b.md": "31d5f19d93e1f0dc1ea7dee9772c9bb1f05293b59be5b1f7dd70db687eda066c",
    "operations-standard-a.md": "7f410296c3d3c5d67fa54a2b7de87a9541e5e1afb99085798af5a18d1627a79b",
    "operations-standard-b.md": "ab031b9d72c5965531eacd8db9eaeaa086d14eef59e86007b14e430d012e1667",
    "password-policy.md": (
        "ebe01890aac266237e31c40014f4286522284d40d802c0fcba88f9f4c556406e"  # a file hash
    ),
    "records-schedule-a.md": "44a8027b5261b3042395b59c65d8a673e6efe6abfbb36cac397be872aaf138c8",
    "records-schedule-b.md": "e59bbcecbe5713be5b518b30d1c8b19735b82821af98c3b402ef0717aafe82c6",
    "retention-notes-a.md": "a645c0209744db6b8bd57f070946180b1fefd3b11ea0f68a5990303681632d14",
    "retention-notes-b.md": "9f24c5dff1e58803c670bd51f6193f740aefb85ec2bea4e07dcd9b9c1af9a647",
    "safety-notes-a.md": "8621fee3c522982d54fc0394ee3da513eab52ff0540826d0c8cee4fb0d96929c",
    "safety-notes-b.md": "e0ed68c06345c04d54fd8450c45c01c0194613d393def3365f6dfec12390559d",
    "service-standard-a.md": "9dd547a8415278bc3638fc0c37a792038baf9f940eb51783e8606a02e42df6b1",
    "service-standard-b.md": "f6794eb305842bcb02d600379a15c19cab52d1feb5616d684a4af9ca2cfea657",
    "training-framework-excerpt.md": (
        "db2a2904910c44e2b0e2d84b9c935fe03b09daec365632026c0660b2d18da810"
    ),
    "training-policy.md": "d88320acc135610bcded565e16814f55dd098c0fbfebc882a538e162c4f2ca81",
    "vendor-payments-a.md": "9cccd7a2f5d730fe825fb3eb2891c0eac1a55d57bfc26ac191e7b5158b0317d3",
    "vendor-payments-b.md": "804a2f501aac54ca46ed1fb9b66e8be200556ca90256556f1fc3e3a8249b14d8",
}


def canonical_sha256(models: Sequence[BaseModel]) -> str:
    """sha256 of canonical JSON of the models sorted by id: independent of key order and of line
    endings in the file."""
    items = sorted((m.model_dump(mode="json") for m in models), key=lambda d: str(d["id"]))
    text = json.dumps(items, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


@pytest.mark.parametrize("split", ["dev", "heldout"])
def test_a_split_has_not_changed_since_the_freeze(split: str) -> None:  # [NEG]
    """Failing here means an item (a label, a quote, a line range, an expected status) was
    edited. That needs a tuning-log entry in ADR-019 and the owner's approval."""
    items = [i for i in load_set().items if i.split == split]
    assert canonical_sha256(items) == FROZEN_SPLITS[split]


def test_the_known_cases_have_not_changed_since_the_freeze() -> None:  # [NEG]
    assert canonical_sha256(load_set().known_cases) == FROZEN_KNOWN_CASES


def test_no_note_was_added_removed_or_changed_since_the_freeze() -> None:  # [NEG]
    found = {d.source_path: d.file_sha256 for d in scan_vault(CONTRADICTIONS_VAULT).documents}
    assert found == FROZEN_NOTES


def test_the_prompt_template_has_not_changed_since_the_freeze() -> None:  # [NEG]
    assert PAIR_TEMPLATE_SHA256 == FROZEN_TEMPLATE
