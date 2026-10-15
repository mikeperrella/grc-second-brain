import sys
from pathlib import Path

import pytest

from grc_sidecar.errors import ConfigurationError
from grc_sidecar.vault.models import ParsedDocument
from grc_sidecar.vault.normalize import MAX_FILE_BYTES, sha256_hex
from grc_sidecar.vault.scan import _safe_file, scan_vault

from .vaultkit import doc_text


def _write(
    root: Path, relative: str, text: str | None = None, *, doc_id: str | None = None
) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    name = doc_id or relative.replace("/", "-").replace(".", "-")
    path.write_text(
        text if text is not None else doc_text("body", f"doc_id: {name}", "type: note", "owner: o"),
        encoding="utf-8",
        newline="",
    )
    return path


def _paths(documents: tuple[ParsedDocument, ...]) -> list[str]:
    return [doc.source_path for doc in documents]


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root


def test_a_simple_vault_scans_clean_and_in_sorted_order(vault: Path) -> None:
    _write(vault, "b.md")
    _write(vault, "a.md")
    _write(vault, "sub/deeper/c.md")
    result = scan_vault(vault)
    assert result.ok and result.issues == ()
    assert _paths(result.documents) == ["a.md", "b.md", "sub/deeper/c.md"]


def test_obsidian_and_hidden_are_skipped(vault: Path) -> None:
    _write(vault, "keep.md")
    _write(vault, ".obsidian/workspace.md")
    _write(vault, ".git/info.md")
    _write(vault, ".hidden.md")
    _write(vault, "sub/.trash/old.md")
    _write(vault, ".obsidian/nested/deep.md")
    result = scan_vault(vault)
    assert _paths(result.documents) == ["keep.md"]
    assert result.issues == ()  # skipped silently by design, not reported as problems


def test_only_markdown_files_are_considered(vault: Path) -> None:
    _write(vault, "note.md")
    (vault / "image.png").write_bytes(b"\x89PNG")
    (vault / "data.json").write_text("{}", encoding="utf-8")
    (vault / "note.md.bak").write_text("x", encoding="utf-8")
    (vault / "README").write_text("x", encoding="utf-8")
    result = scan_vault(vault)
    assert _paths(result.documents) == ["note.md"]
    assert result.issues == ()


def test_the_markdown_extension_is_case_insensitive(vault: Path) -> None:
    _write(vault, "Upper.MD")
    assert _paths(scan_vault(vault).documents) == ["Upper.MD"]


def test_symlink_escaping_file_is_rejected(vault: Path, symlink_support: None) -> None:  # [NEG]
    outside = vault.parent / "outside.md"
    outside.write_text(doc_text("SECRET OUTSIDE", "doc_id: OUT", "type: note", "owner: o"), "utf-8")
    _write(vault, "ok.md")
    (vault / "escape.md").symlink_to(outside)
    result = scan_vault(vault)
    assert _paths(result.documents) == ["ok.md"]
    assert [(i.code, i.source_path, i.severity) for i in result.issues] == [
        ("unsafe_path", "escape.md", "error")
    ]
    assert "SECRET OUTSIDE" not in repr(result)


def test_a_symlink_to_a_file_inside_the_vault_is_also_rejected(
    vault: Path, symlink_support: None
) -> None:  # [NEG]
    _write(vault, "real.md")
    (vault / "alias.md").symlink_to(vault / "real.md")
    result = scan_vault(vault)
    assert _paths(result.documents) == ["real.md"]
    assert [(i.code, i.source_path) for i in result.issues] == [("unsafe_path", "alias.md")]


def test_a_symlinked_folder_is_not_entered(vault: Path, symlink_support: None) -> None:  # [NEG]
    outside = vault.parent / "elsewhere"
    _write(outside, "secret.md")
    _write(vault, "ok.md")
    (vault / "linked").symlink_to(outside, target_is_directory=True)
    result = scan_vault(vault)
    assert _paths(result.documents) == ["ok.md"]
    assert [(i.code, i.source_path) for i in result.issues] == [("unsafe_path", "linked")]


def test_a_vault_root_that_is_itself_a_symlink_scans_its_real_content(
    tmp_path: Path, symlink_support: None
) -> None:
    real = tmp_path / "real-vault"
    _write(real, "a.md")
    link = tmp_path / "vault-link"
    link.symlink_to(real, target_is_directory=True)
    assert _paths(scan_vault(link).documents) == ["a.md"]


def test_duplicate_doc_ids_exclude_every_copy_and_report_each(vault: Path) -> None:  # [NEG]
    _write(vault, "one.md", doc_id="SAME")
    _write(vault, "two.md", doc_id="SAME")
    _write(vault, "three.md", doc_id="UNIQUE")
    result = scan_vault(vault)
    assert _paths(result.documents) == ["three.md"]
    assert [(i.code, i.source_path, i.field) for i in result.issues] == [
        ("duplicate_doc_id", "one.md", "doc_id"),
        ("duplicate_doc_id", "two.md", "doc_id"),
    ]
    assert not result.ok


def test_a_bad_file_is_reported_and_excluded_without_stopping_the_scan(vault: Path) -> None:
    _write(vault, "good.md")
    _write(vault, "no-frontmatter.md", "# just a heading\n")
    _write(vault, "bad-field.md", doc_text("x", "doc_id: B-1", "type: policy", "owner: o"))
    _write(vault, "bad-yaml.md", "---\nkey: [unclosed\n---\nbody\n")
    result = scan_vault(vault)
    assert _paths(result.documents) == ["good.md"]
    found = {(i.source_path, i.code, i.field) for i in result.issues}
    assert found == {
        ("no-frontmatter.md", "missing_frontmatter", None),
        ("bad-field.md", "missing_field", "review_by"),
        ("bad-yaml.md", "yaml_syntax", None),
    }


def test_invalid_utf8_and_nul_files_are_reported(vault: Path) -> None:  # [NEG]
    _write(vault, "good.md")
    (vault / "latin1.md").write_bytes(b"---\ndoc_id: L\n---\ncaf\xe9\n")
    (vault / "nul.md").write_bytes(b"---\ndoc_id: N\n---\nabc\x00def\n")
    result = scan_vault(vault)
    assert _paths(result.documents) == ["good.md"]
    assert {(i.source_path, i.code) for i in result.issues} == {
        ("latin1.md", "invalid_encoding"),
        ("nul.md", "invalid_encoding"),
    }


def test_an_oversized_file_is_reported_and_not_read_in_full(vault: Path) -> None:  # [NEG]
    _write(vault, "good.md")
    big = vault / "big.md"
    big.write_bytes(b"x" * (MAX_FILE_BYTES + 5))
    result = scan_vault(vault)
    assert _paths(result.documents) == ["good.md"]
    assert [(i.source_path, i.code) for i in result.issues] == [("big.md", "file_too_large")]


def test_crlf_and_bom_files_scan_like_lf_files(vault: Path) -> None:
    text = doc_text("# T\n\nbody\n", "doc_id: X", "type: note", "owner: o")
    bom = chr(0xFEFF).encode()
    (vault / "crlf.md").write_bytes(bom + text.replace("\n", "\r\n").encode())
    result = scan_vault(vault)
    assert result.ok
    (doc,) = result.documents
    assert doc.file_sha256 == sha256_hex(text)  # hashed as LF text without the BOM
    assert all("\r" not in line and chr(0xFEFF) not in line for line in doc.lines)


def test_issues_never_contain_absolute_paths_or_document_text(vault: Path) -> None:  # [NEG]
    _write(vault, "bad.md", "---\ndoc_id: has space\ntype: policy\nowner: o\n---\nTOPSECRETBODY\n")
    result = scan_vault(vault)
    dumped = repr(result.issues)
    assert str(vault) not in dumped and vault.name not in dumped
    assert "TOPSECRETBODY" not in dumped and "has space" not in dumped


def test_a_missing_vault_root_is_a_configuration_error(tmp_path: Path) -> None:  # [NEG]
    with pytest.raises(ConfigurationError):
        scan_vault(tmp_path / "does-not-exist")


def test_a_file_as_the_vault_root_is_a_configuration_error(tmp_path: Path) -> None:  # [NEG]
    file_path = tmp_path / "file.md"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        scan_vault(file_path)


def test_an_empty_vault_is_fine(vault: Path) -> None:
    result = scan_vault(vault)
    assert result.documents == () and result.issues == () and result.ok


def test_unknown_frontmatter_keys_are_reported_as_info_not_errors(vault: Path) -> None:
    _write(vault, "tagged.md", doc_text("x", "doc_id: T", "type: note", "owner: o", "tags: [a]"))
    result = scan_vault(vault)
    assert result.ok
    assert [(i.severity, i.code, i.field) for i in result.issues] == [
        ("info", "unknown_frontmatter_key", "tags")
    ]


def test_names_with_spaces_and_mixed_case_are_scanned(vault: Path) -> None:
    _write(vault, "Mixed Case.md", doc_id="MIXED")  # the file name is not the doc_id
    _write(vault, "with space/inner.md", doc_id="INNER")
    assert _paths(scan_vault(vault).documents) == ["Mixed Case.md", "with space/inner.md"]


windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only path behavior")


@windows_only
def test_a_junction_to_a_folder_outside_the_vault_is_not_entered(vault: Path) -> None:  # [NEG]
    import _winapi

    outside = vault.parent / "elsewhere"
    _write(outside, "secret.md", doc_id="OUT")
    _write(vault, "ok.md")
    _winapi.CreateJunction(str(outside), str(vault / "junc"))
    result = scan_vault(vault)
    assert _paths(result.documents) == ["ok.md"]
    assert [(i.code, i.source_path, i.severity) for i in result.issues] == [
        ("unsafe_path", "junc", "error")
    ]
    assert "secret" not in repr(result)


@windows_only
def test_a_junction_to_a_folder_inside_the_vault_is_refused(vault: Path) -> None:  # [NEG]
    # F9-1b: without the refusal, one file would appear under two paths (real/a.md, alias/a.md).
    import _winapi

    _write(vault, "real/a.md", doc_id="A")
    _winapi.CreateJunction(str(vault / "real"), str(vault / "alias"))
    result = scan_vault(vault)
    assert _paths(result.documents) == ["real/a.md"]
    assert [(i.code, i.source_path) for i in result.issues] == [("unsafe_path", "alias")]


@windows_only
def test_the_path_guard_refuses_a_file_reached_through_a_junction(vault: Path) -> None:  # [NEG]
    # The second layer on its own, as if a junction had been swapped in after the listing was
    # read: the resolved path differs from the walked path, for a target inside and outside.
    import _winapi

    outside = vault.parent / "elsewhere"
    _write(outside, "secret.md", doc_id="OUT")
    _write(vault, "real/a.md", doc_id="A")
    _winapi.CreateJunction(str(outside), str(vault / "out"))
    _winapi.CreateJunction(str(vault / "real"), str(vault / "alias"))
    root = vault.resolve()
    assert _safe_file(root, "out/secret.md") is None
    assert _safe_file(root, "alias/a.md") is None
    assert _safe_file(root, "real/a.md") == root / "real" / "a.md"


def test_the_path_guard_refuses_a_link_even_if_the_walker_missed_it(
    vault: Path, symlink_support: None
) -> None:  # [NEG]
    # The walker already skips symlinks. This calls the second layer (resolve_within plus the
    # "resolved path equals the walked path" rule) directly, as if a link had slipped past the
    # walker, for example one swapped in after the directory listing was read.
    outside = vault.parent / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    (vault / "escape.md").symlink_to(outside)
    _write(vault, "ok.md")
    root = vault.resolve()
    assert _safe_file(root, "escape.md") is None
    assert _safe_file(root, "../outside.md") is None
    assert _safe_file(root, "ok.md") == root / "ok.md"
