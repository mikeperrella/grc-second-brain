"""Reading a cited vault file once, for the run record (ADR-017 decision 3 and 15).

`read_cited_file` returns the file's hash and the hash of each cited line range from one read, or
the reason the file cannot be used. `current_file_sha256` must behave exactly as before.
"""

import hashlib
from pathlib import Path

import pytest

from grc_sidecar.vault import scan
from grc_sidecar.vault.normalize import MAX_FILE_BYTES
from grc_sidecar.vault.scan import CitedFile, current_file_sha256, read_cited_file


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "policies").mkdir(parents=True)
    return root


def write(vault: Path, name: str, data: bytes) -> None:
    (vault / name).write_bytes(data)


def test_the_file_and_each_cited_range_are_hashed_from_the_normalized_text(vault: Path) -> None:
    # A byte-order mark and Windows line endings, as the indexer would see through its own rules.
    write(vault, "policies/a.md", "﻿one\r\ntwo\r\nthree\r\nfour\r\n".encode())
    read = read_cited_file(vault, "policies/a.md", [(2, 3), (4, 4), (1, 4)])
    assert read == CitedFile(
        file_sha256=sha("one\ntwo\nthree\nfour\n"),
        lines_sha256=(sha("two\nthree"), sha("four"), sha("one\ntwo\nthree\nfour")),
    )
    assert read.file_sha256 == current_file_sha256(vault, "policies/a.md")


def test_the_file_is_read_once_however_many_ranges_are_asked_for(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(vault, "a.md", b"one\ntwo\nthree\n")
    reads: list[Path] = []
    real = scan._read_limited

    def counting(path: Path) -> bytes | None:
        reads.append(path)
        return real(path)

    monkeypatch.setattr(scan, "_read_limited", counting)
    assert isinstance(read_cited_file(vault, "a.md", [(1, 1), (2, 2), (3, 3)]), CitedFile)
    assert len(reads) == 1


@pytest.mark.parametrize("bad", [(0, 1), (2, 1), (1, 4), (4, 4), (-1, 2)])
def test_a_range_outside_the_file_has_no_hash(vault: Path, bad: tuple[int, int]) -> None:  # [NEG]
    write(vault, "a.md", b"one\ntwo\nthree\n")  # three lines
    read = read_cited_file(vault, "a.md", [(1, 3), bad])
    assert isinstance(read, CitedFile)
    assert read.lines_sha256 == (sha("one\ntwo\nthree"), None)


def test_a_missing_file_and_a_missing_root_are_not_found(vault: Path, tmp_path: Path) -> None:
    assert read_cited_file(vault, "policies/gone.md", [(1, 1)]) == "not_found"
    assert read_cited_file(tmp_path / "no-vault", "a.md", [(1, 1)]) == "not_found"


def test_a_path_outside_the_vault_is_unsafe(vault: Path, tmp_path: Path) -> None:  # [NEG]
    (tmp_path / "outside.md").write_bytes(b"secret\n")
    assert read_cited_file(vault, "../outside.md", [(1, 1)]) == "unsafe_path"


def test_a_symlink_is_unsafe_even_inside_the_vault(  # [NEG]
    vault: Path, symlink_support: None
) -> None:
    write(vault, "real.md", b"one\n")
    (vault / "alias.md").symlink_to(vault / "real.md")
    assert read_cited_file(vault, "alias.md", [(1, 1)]) == "unsafe_path"


def test_a_file_over_the_size_cap_is_too_large(vault: Path) -> None:  # [NEG]
    write(vault, "big.md", b"a" * (MAX_FILE_BYTES + 1))
    assert read_cited_file(vault, "big.md", [(1, 1)]) == "too_large"


@pytest.mark.parametrize("data", [b"caf\xe9\n", b"\xff\xfe\x00a\n", b"nul\x00byte\n"])
def test_a_file_that_is_not_utf8_text_is_not_utf8(vault: Path, data: bytes) -> None:  # [NEG]
    write(vault, "bad.md", data)
    assert read_cited_file(vault, "bad.md", [(1, 1)]) == "not_utf8"


def test_current_file_sha256_still_gives_none_for_every_one_of_those_cases(
    vault: Path, tmp_path: Path
) -> None:
    (tmp_path / "outside.md").write_bytes(b"secret\n")
    write(vault, "big.md", b"a" * (MAX_FILE_BYTES + 1))
    write(vault, "bad.md", b"caf\xe9\n")
    write(vault, "ok.md", b"fine\n")
    assert current_file_sha256(vault, "ok.md") == sha("fine\n")
    for name in ("gone.md", "../outside.md", "big.md", "bad.md"):
        assert current_file_sha256(vault, name) is None
