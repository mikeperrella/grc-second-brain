"""What a questionnaire run asks before it starts, and what it checks when it finishes (ADR-014).

`Retriever.vault_withheld()` lists the vault files that changed after they were indexed, so a run
can refuse to start from an index that is behind the vault. `current_file_sha256` gives the hash of
a cited file as it is now, in the form the index recorded, so an export can blank an answer whose
source changed since retrieval.
"""

import asyncio
import shutil
from pathlib import Path

import pytest

from grc_sidecar.errors import IndexNotBuiltError
from grc_sidecar.vault.scan import current_file_sha256, scan_vault

from .retrievalkit import stack
from .vaultkit import SAMPLE_VAULT

RETENTION = "data-retention-policy.md"


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    copy = tmp_path / "vault"
    shutil.copytree(SAMPLE_VAULT, copy)
    return copy


# ----- the hash of a file as it is now -------------------------------------------------------


def test_an_unchanged_file_has_the_hash_the_index_recorded(vault: Path) -> None:
    documents = scan_vault(vault).documents
    assert documents  # the comparison is not vacuous
    for document in documents:
        assert current_file_sha256(vault, document.source_path) == document.file_sha256


def test_the_hash_ignores_line_endings_and_a_byte_order_mark_like_the_index(vault: Path) -> None:
    path = vault / RETENTION
    recorded = current_file_sha256(vault, RETENTION)
    text = path.read_text(encoding="utf-8")
    path.write_bytes(b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"))
    assert current_file_sha256(vault, RETENTION) == recorded


def test_a_changed_file_has_another_hash(vault: Path) -> None:  # [NEG]
    recorded = current_file_sha256(vault, RETENTION)
    path = vault / RETENTION
    path.write_text(
        path.read_text(encoding="utf-8").replace("90 days", "999 days"), encoding="utf-8"
    )
    changed = current_file_sha256(vault, RETENTION)
    assert changed is not None and changed != recorded


@pytest.mark.parametrize(
    "source_path",
    ["no-such-note.md", "../outside.md", "/etc/passwd", "sub/../../x.md", "", "a\x00b.md"],
)
def test_anything_that_is_not_a_readable_vault_file_has_no_hash(  # [NEG]
    vault: Path, source_path: str
) -> None:
    assert current_file_sha256(vault, source_path) is None


def test_a_file_that_is_not_utf8_has_no_hash(vault: Path) -> None:  # [NEG]
    (vault / "binary.md").write_bytes(b"\xff\xfe\x00bad")
    assert current_file_sha256(vault, "binary.md") is None


def test_a_missing_vault_root_has_no_hash(tmp_path: Path) -> None:  # [NEG]
    assert current_file_sha256(tmp_path / "no-vault", "note.md") is None


def test_a_link_out_of_the_vault_has_no_hash(
    vault: Path, tmp_path: Path, symlink_support: None
) -> None:  # [NEG]
    outside = tmp_path / "outside.md"
    outside.write_text("# secret\n", encoding="utf-8")
    (vault / "link.md").symlink_to(outside)
    assert current_file_sha256(vault, "link.md") is None


# ----- vault_withheld --------------------------------------------------------------------------


def test_nothing_is_withheld_when_the_vault_matches_the_index(vault: Path, tmp_path: Path) -> None:
    async def scenario() -> None:
        async with stack(vault, tmp_path / "index") as s:
            assert await s.retriever.vault_withheld() == ()

    asyncio.run(scenario())


def test_an_edited_file_is_listed_as_withheld(vault: Path, tmp_path: Path) -> None:  # [NEG]
    async def scenario() -> None:
        async with stack(vault, tmp_path / "index") as s:
            assert await s.vault_repo.open_for_deletions()
            path = vault / RETENTION
            path.write_text(path.read_text(encoding="utf-8") + "\nAn edit.\n", encoding="utf-8")
            assert await s.retriever.vault_withheld() == (RETENTION,)

    asyncio.run(scenario())


def test_no_index_is_a_typed_error(vault: Path, tmp_path: Path) -> None:  # [NEG]
    async def scenario() -> None:
        async with stack(vault, tmp_path / "index", sync=False) as s:
            with pytest.raises(IndexNotBuiltError):
                await s.retriever.vault_withheld()

    asyncio.run(scenario())
