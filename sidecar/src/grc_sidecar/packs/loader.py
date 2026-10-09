"""Load framework packs and index them into their own database (ADR-008).

A packs folder looks like `packs/<pack_id>/{pack.json, *.md}`. Loading is fail-closed: every pack
is verified first, and if any one fails nothing is indexed. Verification is an integrity check
(files match the manifest), not authenticity: someone who can rewrite both the files and
`pack.json` is not stopped (ADR-008).

Strictness that keeps the verified file set equal to the indexed file set: the packs folder may
hold only directories, a pack folder may hold only `pack.json` and top-level `*.md` files, and
symlinks are refused. Otherwise a stray Markdown file could be indexed without being hashed.
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Final

from grc_sidecar.errors import GrcError, PackIntegrityError, PackMetadataError
from grc_sidecar.llm.embeddings import EmbeddingClient
from grc_sidecar.packs.models import PackMetadata, parse_pack_metadata
from grc_sidecar.repositories.base import VectorRepository
from grc_sidecar.security.paths import resolve_within
from grc_sidecar.vault.indexer import Indexer, SyncReport

PACK_FILE: Final = "pack.json"
PACKS_INDEX_NAME: Final = "packs.sqlite3"
COLLECTION_PREFIX: Final = "pack:"


def packs_index_path(index_dir: Path) -> Path:
    """The pack database: separate from every vault index, built at runtime, never committed."""
    return index_dir / PACKS_INDEX_NAME


def content_sha256(files: dict[str, bytes]) -> str:
    """sha256 over the sorted (relative path, file sha256) pairs. tools/ computes the same."""
    pairs = [[name, hashlib.sha256(files[name]).hexdigest()] for name in sorted(files)]
    return hashlib.sha256(
        json.dumps(pairs, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _read_pack_files(pack_dir: Path, pack_id: str) -> tuple[bytes, dict[str, bytes]]:
    manifest: bytes | None = None
    files: dict[str, bytes] = {}
    try:
        with os.scandir(pack_dir) as entries:
            ordered = sorted(entries, key=lambda entry: entry.name)
        for entry in ordered:
            if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                raise PackIntegrityError(pack_id)
            data = Path(entry.path).read_bytes()
            if entry.name == PACK_FILE:
                manifest = data
            elif entry.name.endswith(".md") and not entry.name.startswith("."):
                files[entry.name] = data
            else:
                raise PackIntegrityError(pack_id)
    except OSError:
        raise PackIntegrityError(pack_id) from None
    if manifest is None or not files:
        raise PackIntegrityError(pack_id)
    return manifest, files


def verify_pack(pack_dir: Path) -> PackMetadata:
    """Verify one pack folder. Raises PackMetadataError or PackIntegrityError."""
    pack_id = pack_dir.name
    manifest, files = _read_pack_files(pack_dir, pack_id)
    try:
        metadata = parse_pack_metadata(manifest.decode("utf-8"))
    except UnicodeDecodeError:
        raise PackMetadataError((PACK_FILE,)) from None
    if metadata.pack_id != pack_id or content_sha256(files) != metadata.content_sha256:
        raise PackIntegrityError(pack_id)
    return metadata


def verify_packs(packs_dir: Path) -> list[PackMetadata]:
    """Verify every pack under `packs_dir`, in sorted order. Any failure raises (fail closed)."""
    try:
        root = resolve_within(".", packs_dir)
        with os.scandir(root) as entries:
            ordered = sorted(entries, key=lambda entry: entry.name)
    except (GrcError, OSError):
        raise PackIntegrityError from None
    packs: list[PackMetadata] = []
    for entry in ordered:
        if entry.name.startswith("."):
            continue
        if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
            raise PackIntegrityError(entry.name)
        packs.append(verify_pack(Path(entry.path)))
    return packs


def collection_of(source_path: str) -> str:
    """`nist-csf-2.0/gv.md` -> `pack:nist-csf-2.0`."""
    return COLLECTION_PREFIX + source_path.split("/", 1)[0]


async def index_packs(
    packs_dir: Path, repo: VectorRepository, embedder: EmbeddingClient
) -> SyncReport:
    """Verify all packs, then index them into `repo` (the pack database, not the vault's).

    Takes any `EmbeddingClient`. Verification happens before the embedder or the repository is
    touched, so a tampered pack changes nothing.
    """
    verify_packs(packs_dir)
    return await Indexer(repo, embedder, packs_dir, collection_of=collection_of).sync()
