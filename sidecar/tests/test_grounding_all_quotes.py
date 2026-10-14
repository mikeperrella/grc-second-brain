"""Every place a quote occurs in a text (ADR-017 decision 17), and the vault text read recall uses.

`locate_all_quotes` follows the grounding rule and nothing more: whitespace runs count as one
space, the ends are trimmed, and no other folding. `read_vault_text` reads a file once through the
scanner's path guard and gives the text the indexer would see, or the reason it cannot.
"""

from pathlib import Path

import pytest

from grc_sidecar.questionnaire.grounding import locate_all_quotes, locate_quote
from grc_sidecar.retrieval.models import RetrievedChunk
from grc_sidecar.vault import scan
from grc_sidecar.vault.normalize import MAX_FILE_BYTES
from grc_sidecar.vault.scan import VaultText, read_vault_text

TEXT = "# Title\nData at rest is encrypted.\nKeys rotate yearly.\nData at rest is encrypted.\n"


def places(text: str, quote: str, first_line: int = 1) -> list[tuple[int, int]]:
    return [(p.start_line, p.end_line) for p in locate_all_quotes(text, quote, first_line)]


def test_every_occurrence_is_found_in_order_as_lines() -> None:
    assert places(TEXT, "Data at rest is encrypted.") == [(2, 2), (4, 4)]


def test_one_occurrence_and_no_occurrence() -> None:
    assert places(TEXT, "Keys rotate yearly.") == [(3, 3)]
    assert places(TEXT, "Keys rotate monthly.") == []


def test_a_quote_that_spans_lines_gives_both_lines_and_whitespace_is_the_only_leniency() -> None:
    assert places(TEXT, "encrypted. Keys   rotate\n yearly.") == [(2, 3)]
    assert places(TEXT, "  Keys rotate yearly.  ") == [(3, 3)]


def test_letter_case_is_not_folded() -> None:  # [NEG]
    assert places(TEXT, "data at rest is encrypted.") == []
    assert places(TEXT, "DATA AT REST IS ENCRYPTED.") == []


def test_a_quote_is_verbatim_text_so_a_part_of_a_sentence_still_matches() -> None:
    assert places(TEXT, "Data at rest is encrypted") == [(2, 2), (4, 4)]


def test_matches_do_not_overlap() -> None:
    assert places("aaaa", "aa") == [(1, 1), (1, 1)]  # two matches, not three
    assert len(locate_all_quotes("aaa", "aa")) == 1  # the second would overlap the first
    assert len(locate_all_quotes("abababa", "aba")) == 2  # at 0 and at 4, not at 2


def test_an_empty_quote_has_no_place() -> None:
    assert locate_all_quotes(TEXT, "") == ()
    assert locate_all_quotes(TEXT, " \n\t ") == ()


def test_lines_are_counted_from_the_first_line_given() -> None:
    assert places(TEXT, "Keys rotate yearly.", first_line=10) == [(12, 12)]


@pytest.mark.parametrize("quote", ["Data at rest is encrypted.", "Keys rotate yearly.", "Title"])
def test_the_first_place_is_the_one_locate_quote_finds(quote: str) -> None:
    """The two functions share their line arithmetic, so they cannot disagree."""
    chunk = RetrievedChunk.model_construct(text=TEXT, start_line=5)
    single = locate_quote(chunk, quote)
    everywhere = locate_all_quotes(TEXT, quote, 5)
    assert single is not None and everywhere[0] == single


# ----- the vault text recall reads ---------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "policies").mkdir(parents=True)
    return root


def test_the_text_is_what_the_indexer_sees(vault: Path) -> None:
    (vault / "policies" / "a.md").write_bytes("﻿one\r\ntwo\r\n".encode())
    assert read_vault_text(vault, "policies/a.md") == VaultText("one\ntwo\n")


def test_the_file_is_read_once(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (vault / "a.md").write_bytes(b"one\n")
    reads: list[Path] = []
    real = scan._read_limited

    def counting(path: Path) -> bytes | None:
        reads.append(path)
        return real(path)

    monkeypatch.setattr(scan, "_read_limited", counting)
    assert isinstance(read_vault_text(vault, "a.md"), VaultText) and len(reads) == 1


def test_a_missing_file_and_a_missing_root_are_not_found(vault: Path, tmp_path: Path) -> None:
    assert read_vault_text(vault, "gone.md") == "not_found"
    assert read_vault_text(tmp_path / "no-vault", "a.md") == "not_found"


def test_a_path_outside_the_vault_and_a_symlink_are_unsafe(  # [NEG]
    vault: Path, tmp_path: Path, symlink_support: None
) -> None:
    (tmp_path / "outside.md").write_bytes(b"secret\n")
    assert read_vault_text(vault, "../outside.md") == "unsafe_path"
    (vault / "alias.md").symlink_to(tmp_path / "outside.md")
    assert read_vault_text(vault, "alias.md") == "unsafe_path"


def test_an_oversize_file_and_a_file_that_is_not_utf8_say_so(vault: Path) -> None:  # [NEG]
    (vault / "big.md").write_bytes(b"a" * (MAX_FILE_BYTES + 1))
    (vault / "bad.md").write_bytes(b"caf\xe9\n")
    (vault / "nul.md").write_bytes(b"a\x00b\n")
    assert read_vault_text(vault, "big.md") == "too_large"
    assert read_vault_text(vault, "bad.md") == "not_utf8"
    assert read_vault_text(vault, "nul.md") == "not_utf8"
