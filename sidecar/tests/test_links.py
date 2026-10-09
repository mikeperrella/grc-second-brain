import builtins
import os
from pathlib import Path

import pytest

from grc_sidecar.vault.links import (
    ResolutionIndex,
    extract_links,
    rejection_code,
    resolve_link,
    resolve_links,
    split_link_text,
)
from grc_sidecar.vault.models import RawLink

from .vaultkit import DEFAULT_BODY_START, DEFAULT_FRONTMATTER, make_doc

B = DEFAULT_BODY_START


def _raw(target: str, *, fragment: str | None = None, source: str = "a.md") -> RawLink:
    return RawLink(source_path=source, source_line=1, target=target, fragment=fragment)


def _index() -> ResolutionIndex:
    return ResolutionIndex(
        [
            ("a.md", "DOC-A"),
            ("encryption-policy.md", "HR-POL-002"),
            ("policies/access.md", "HR-POL-001"),
            ("team1/notes.md", "N-1"),
            ("team2/notes.md", "N-2"),
        ]
    )


@pytest.mark.parametrize(
    ("inner", "expected"),
    [
        ("target", ("target", None, None)),
        ("target|alias", ("target", "alias", None)),
        ("target#Heading", ("target", None, "Heading")),
        ("target#^block1", ("target", None, "^block1")),
        ("target#Heading|alias", ("target", "alias", "Heading")),
        ("target\\|alias", ("target", "alias", None)),  # the table-cell escape for a pipe
        ("  spaced  | al ", ("spaced", "al", None)),
        ("#Heading", ("", None, "Heading")),
    ],
)
def test_link_text_splits_into_target_alias_and_fragment(
    inner: str, expected: tuple[str, str | None, str | None]
) -> None:
    assert split_link_text(inner) == expected


def test_body_links_are_extracted_with_line_numbers() -> None:
    doc = make_doc("# T\n\nsee [[one]] and [[two|alias]]\n\n![[embedded#part]]\n")
    links = extract_links(doc)
    assert [(link.target, link.alias, link.fragment, link.is_embed) for link in links] == [
        ("one", None, None, False),
        ("two", "alias", None, False),
        ("embedded", None, "part", True),
    ]
    assert [link.source_line for link in links] == [B + 2, B + 2, B + 4]
    assert all(link.origin == "body" and link.source_path == "note.md" for link in links)


def test_frontmatter_links_are_extracted_with_their_origin() -> None:
    doc = make_doc("x", *DEFAULT_FRONTMATTER, 'links: ["[[from-front]]", "bare"]')
    links = extract_links(doc)
    assert [(link.target, link.origin) for link in links] == [
        ("from-front", "frontmatter"),
        ("bare", "frontmatter"),
    ]
    assert all(link.source_line == len(DEFAULT_FRONTMATTER) + 2 for link in links)


def test_links_in_code_fences_are_ignored() -> None:
    body = (
        "[[real]]\n\n```\n[[in-fence]]\n```\n\n~~~md\n[[in-tilde]]\n~~~\n\n"
        "`[[in-span]]` and ``[[in-double]]`` but [[after]]\n"
    )
    targets = [link.target for link in extract_links(make_doc(body))]
    assert targets == ["real", "after"]


def test_an_unclosed_fence_hides_the_rest_of_the_file() -> None:
    targets = [link.target for link in extract_links(make_doc("[[a]]\n\n```\n[[b]]\n[[c]]"))]
    assert targets == ["a"]


def test_malformed_brackets_are_not_links() -> None:
    body = "[[]] [[ ]] [single] [[unclosed and ]]] [[a\nb]]"
    links = extract_links(make_doc(body))
    # `[[]]`, `[single]` and a link broken across lines are not links. `[[ ]]` has a blank target.
    assert [link.target for link in links] == ["", "unclosed and"]
    assert [resolve_link(link, _index()).status for link in links] == ["rejected", "unresolved"]


@pytest.mark.parametrize(
    "target",
    [
        "../../secret",
        "C:\\Windows\\win.ini",
        "c:/windows/win.ini",
        "/etc/passwd",
        "\\\\server\\share\\x",
        "//server/share",
        "https://example.invalid/page",
        "file:///etc/passwd",
        "sub/../../escape",
        "..",
        "a\\b",
        "x\x00y",
        "a" * 600,
    ],
)
def test_escaping_links_are_rejected(target: str) -> None:  # [NEG]
    result = resolve_link(_raw(target), _index())
    assert result.status == "rejected"
    assert result.code is not None
    assert result.target_doc_id is None


def test_rejection_happens_without_touching_the_filesystem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # [NEG]
    def boom(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("link resolution must not touch the filesystem")

    monkeypatch.setattr(os, "stat", boom)
    monkeypatch.setattr(os, "scandir", boom)
    monkeypatch.setattr(os, "listdir", boom)
    monkeypatch.setattr(builtins, "open", boom)
    monkeypatch.setattr(Path, "resolve", boom)
    monkeypatch.setattr(Path, "exists", boom)
    monkeypatch.setattr(Path, "open", boom)
    index = _index()
    for target in ["../../secret", "C:\\x", "/etc/passwd", "encryption-policy", "missing"]:
        resolve_link(_raw(target), index)


def test_resolution_by_path_by_doc_id_and_by_file_name() -> None:
    index = _index()
    assert resolve_link(_raw("encryption-policy"), index).target_doc_id == "HR-POL-002"
    assert resolve_link(_raw("encryption-policy.md"), index).target_doc_id == "HR-POL-002"
    assert resolve_link(_raw("policies/access"), index).target_doc_id == "HR-POL-001"
    assert resolve_link(_raw("access"), index).target_doc_id == "HR-POL-001"  # by file name
    assert resolve_link(_raw("HR-POL-001"), index).target_doc_id == "HR-POL-001"  # by doc_id
    assert resolve_link(_raw("./encryption-policy"), index).target_doc_id == "HR-POL-002"


def test_resolution_ignores_case_and_keeps_the_fragment_separate() -> None:
    index = _index()
    result = resolve_link(_raw("Encryption-Policy", fragment="Key management"), index)
    assert (result.status, result.target_doc_id) == ("resolved", "HR-POL-002")


def test_a_file_name_shared_by_two_documents_is_ambiguous_not_guessed() -> None:
    result = resolve_link(_raw("notes"), _index())
    assert result.status == "ambiguous"
    assert result.target_doc_id is None
    # Spelling out the folder makes it unambiguous.
    assert resolve_link(_raw("team1/notes"), _index()).target_doc_id == "N-1"


def test_a_missing_target_is_unresolved_never_invented() -> None:
    result = resolve_link(_raw("no-such-note"), _index())
    assert (result.status, result.target_doc_id, result.code) == (
        "unresolved",
        None,
        "no_such_document",
    )


def test_a_heading_link_inside_the_same_file_resolves_to_itself() -> None:
    result = resolve_link(_raw("", fragment="Intro"), _index())
    assert (result.status, result.target_doc_id) == ("resolved", "DOC-A")


def test_an_empty_target_without_a_fragment_is_rejected() -> None:  # [NEG]
    assert resolve_link(_raw(""), _index()).status == "rejected"
    assert rejection_code("", None) == "empty_target"


def test_resolve_links_keeps_order_and_resolves_each() -> None:
    results = resolve_links([_raw("encryption-policy"), _raw("nope"), _raw("/x")], _index())
    assert [r.status for r in results] == ["resolved", "unresolved", "rejected"]
