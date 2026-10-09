from datetime import date

import pytest
import yaml

from grc_sidecar.errors import (
    FrontmatterFieldError,
    FrontmatterSyntaxError,
    MissingFrontmatterError,
)
from grc_sidecar.vault.frontmatter import (
    MAX_FRONTMATTER_BYTES,
    UNQUOTED_WIKILINK_CODE,
    StrictFrontmatterLoader,
    _event_rejection,
    _load_mapping,
    parse_document,
    parse_frontmatter,
)

from .leakcheck import assert_no_leak
from .vaultkit import DEFAULT_BODY_START, DEFAULT_FRONTMATTER, doc_text, make_doc

BOM = chr(0xFEFF)
VALID = "\n".join(DEFAULT_FRONTMATTER)


def _field_error(*frontmatter_lines: str) -> FrontmatterFieldError:
    with pytest.raises(FrontmatterFieldError) as caught:
        make_doc("body", *frontmatter_lines)
    return caught.value


def test_a_minimal_valid_document_parses() -> None:
    doc = make_doc("hello")
    assert doc.frontmatter.doc_id == "T-001"
    assert doc.frontmatter.type == "policy"
    assert doc.frontmatter.owner == "Test Owner"
    assert doc.frontmatter.review_by == date(2028, 1, 1)
    assert doc.frontmatter.control_mappings == []
    assert doc.frontmatter.links == []
    assert doc.body_start_line == DEFAULT_BODY_START
    assert doc.lines[doc.body_start_line - 1] == "hello"


def test_every_field_can_be_given() -> None:
    doc = make_doc(
        "x",
        "doc_id: A.1_b-2",
        "type: procedure",
        "owner: Someone",
        "review_by: 2030-12-31",
        'control_mappings: ["SOC2:CC6.1", "NIST-800-53:AC-2"]',
        'links: ["[[other-note]]", "plain-target"]',
    )
    assert doc.frontmatter.control_mappings == ["SOC2:CC6.1", "NIST-800-53:AC-2"]
    assert doc.frontmatter.links == ["other-note", "plain-target"]


def test_missing_doc_id_is_error() -> None:  # [NEG]
    error = _field_error("type: policy", "owner: x", "review_by: 2028-01-01")
    assert (error.field, error.code, error.source_path) == ("doc_id", "missing_field", "note.md")


@pytest.mark.parametrize(
    ("missing", "lines"),
    [
        ("type", ("doc_id: T-1", "owner: x", "review_by: 2028-01-01")),
        ("owner", ("doc_id: T-1", "type: policy", "review_by: 2028-01-01")),
        ("review_by", ("doc_id: T-1", "type: policy", "owner: x")),
    ],
)
def test_each_required_field_is_enforced(missing: str, lines: tuple[str, ...]) -> None:  # [NEG]
    error = _field_error(*lines)
    assert (error.field, error.code) == (missing, "missing_field")


@pytest.mark.parametrize("doc_type", ["framework", "note"])
def test_review_by_is_optional_for_framework_and_note(doc_type: str) -> None:
    doc = make_doc("x", "doc_id: T-1", f"type: {doc_type}", "owner: NIST")
    assert doc.frontmatter.review_by is None


def test_an_unknown_type_is_rejected() -> None:  # [NEG]
    error = _field_error("doc_id: T-1", "type: memo", "owner: x", "review_by: 2028-01-01")
    assert (error.field, error.code) == ("type", "invalid_value")


@pytest.mark.parametrize(
    "value",
    ["2026-1-5", "01/05/2026", "2026-02-30", "2026-01-05T10:00:00", "20260105", "tomorrow", "''"],
)
def test_review_by_must_be_an_exact_iso_date(value: str) -> None:  # [NEG]
    error = _field_error("doc_id: T-1", "type: policy", "owner: x", f"review_by: {value}")
    assert (error.field, error.code) == ("review_by", "invalid_value")


@pytest.mark.parametrize("value", ["has space", "a/b", "-leading", "", "x" * 129])
def test_doc_id_must_be_a_simple_identifier(value: str) -> None:  # [NEG]
    error = _field_error(f'doc_id: "{value}"', "type: policy", "owner: x", "review_by: 2028-01-01")
    assert error.field == "doc_id"


def test_control_mappings_must_be_a_list() -> None:  # [NEG]
    error = _field_error(*DEFAULT_FRONTMATTER, "control_mappings: SOC2:CC6.1")
    assert (error.field, error.code) == ("control_mappings", "invalid_value")


def test_dates_and_booleans_stay_strings() -> None:
    data, code, _ = _load_mapping(
        "when: 2026-01-15\nowner: NO\nanswer: yes\nflag: true\nid: 007\nnum: 1e3\nport: 80\nempty:"
    )
    assert code is None
    assert data == {
        "when": "2026-01-15",
        "owner": "NO",
        "answer": "yes",
        "flag": "true",
        "id": "007",
        "num": "1e3",
        "port": "80",
        "empty": None,
    }
    # And they survive all the way into the model as the exact text.
    doc = make_doc("x", "doc_id: 007", "type: policy", "owner: NO", "review_by: 2028-01-01")
    assert (doc.frontmatter.doc_id, doc.frontmatter.owner) == ("007", "NO")


def test_safe_loader_still_resolves_dates_after_import() -> None:
    # Importing our module must not have changed PyYAML for anyone else in the process.
    assert yaml.safe_load("d: 2026-01-15") == {"d": date(2026, 1, 15)}
    assert yaml.safe_load("a: yes") == {"a": True}
    assert yaml.safe_load("n: 007") == {"n": 7}
    assert StrictFrontmatterLoader.yaml_implicit_resolvers is not (
        yaml.SafeLoader.yaml_implicit_resolvers
    )
    assert len(StrictFrontmatterLoader.yaml_implicit_resolvers) < len(
        yaml.SafeLoader.yaml_implicit_resolvers
    )


def test_duplicate_keys_are_rejected() -> None:  # [NEG]
    with pytest.raises(FrontmatterSyntaxError) as caught:
        make_doc("x", *DEFAULT_FRONTMATTER, "owner: Someone Else")
    assert caught.value.code == "yaml_duplicate_key"
    assert caught.value.line is not None


@pytest.mark.parametrize(
    ("lines", "code"),
    [
        (("a: &x 1", "b: *x"), "yaml_anchor"),  # the anchor is refused before the alias
        (("a: &x 1",), "yaml_anchor"),
        (("a: !!python/object/apply:os.getcwd []",), "yaml_tag"),
        (("a: !!str 5",), "yaml_tag"),
        (("a: !custom 5",), "yaml_tag"),
    ],
)
def test_anchors_aliases_and_tags_are_rejected(lines: tuple[str, ...], code: str) -> None:  # [NEG]
    with pytest.raises(FrontmatterSyntaxError) as caught:
        make_doc("x", *DEFAULT_FRONTMATTER, *lines)
    assert caught.value.code == code


@pytest.mark.parametrize("lines", [("- a", "- b"), ("just text",)])
def test_frontmatter_must_be_a_mapping(lines: tuple[str, ...]) -> None:  # [NEG]
    with pytest.raises(FrontmatterSyntaxError) as caught:
        make_doc("x", *lines)
    assert caught.value.code == "frontmatter_not_a_mapping"


def test_broken_yaml_reports_a_line_number() -> None:  # [NEG]
    with pytest.raises(FrontmatterSyntaxError) as caught:
        make_doc("x", *DEFAULT_FRONTMATTER, "bad: [unclosed")
    assert caught.value.code == "yaml_syntax"
    assert caught.value.line is not None and caught.value.line >= len(DEFAULT_FRONTMATTER) + 1


def test_a_file_without_frontmatter_is_an_error() -> None:  # [NEG]
    for text in ["", "# Just a heading\n", "\n---\ndoc_id: x\n---\n"]:
        with pytest.raises(MissingFrontmatterError) as caught:
            parse_document(text, "a.md")
        assert caught.value.code == "missing_frontmatter"
        assert caught.value.source_path == "a.md"


def test_an_unterminated_block_is_an_error() -> None:  # [NEG]
    with pytest.raises(FrontmatterSyntaxError) as caught:
        parse_document("---\ndoc_id: x\ntype: policy\n", "a.md")
    assert caught.value.code == "frontmatter_unterminated"


def test_an_oversized_block_is_an_error() -> None:  # [NEG]
    filler = "k: " + "x" * MAX_FRONTMATTER_BYTES
    with pytest.raises(FrontmatterSyntaxError) as caught:
        make_doc("x", *DEFAULT_FRONTMATTER, filler)
    assert caught.value.code == "frontmatter_too_large"


def test_a_second_block_in_the_body_is_plain_text() -> None:  # [NEG]
    body = "intro\n\n---\ndoc_id: EVIL\ntype: policy\nowner: Mallory\n---\n\nmore"
    doc = make_doc(body)
    assert doc.frontmatter.doc_id == "T-001"
    assert "doc_id: EVIL" in "\n".join(doc.lines)


def test_unknown_keys_are_ignored_and_reported_as_notes() -> None:
    doc = make_doc("x", *DEFAULT_FRONTMATTER, "tags: [a, b]", "aliases: x")
    assert [(n.severity, n.code, n.field) for n in doc.notes] == [
        ("info", "unknown_frontmatter_key", "aliases"),
        ("info", "unknown_frontmatter_key", "tags"),
    ]


@pytest.mark.parametrize(
    "links_lines",
    [
        ("links: [[target]]",),
        ("links:", "  - [[target]]"),
        ("links: [[target|alias]]",),
        ("links: [[target#heading]]",),
    ],
)
def test_unquoted_wikilink_has_specific_error_code(links_lines: tuple[str, ...]) -> None:  # [NEG]
    error = _field_error(*DEFAULT_FRONTMATTER, *links_lines)
    assert (error.field, error.code) == ("links", UNQUOTED_WIKILINK_CODE)
    assert error.code != "invalid_value"


def test_quoted_wikilinks_in_frontmatter_work() -> None:
    doc = make_doc("x", *DEFAULT_FRONTMATTER, 'links: ["[[target|alias]]", "[[t#h]]"]')
    assert doc.frontmatter.links == ["target|alias", "t#h"]
    assert doc.frontmatter_line_of_links == len(DEFAULT_FRONTMATTER) + 2


def test_the_same_content_in_any_line_ending_gives_the_same_document() -> None:
    lf = doc_text("# Title\n\ntext\n")
    variants = [
        lf,
        lf.replace("\n", "\r\n"),
        BOM + lf.replace("\n", "\r\n"),
        lf.replace("\n", "\r"),
    ]
    docs = [parse_document(text, "a.md") for text in variants]
    assert len({d.file_sha256 for d in docs}) == 1
    assert len({d.lines for d in docs}) == 1


def test_errors_name_the_file_and_field_but_never_echo_values() -> None:  # [NEG]
    secret = "marker-" + "value"  # built at run time so this file never contains it whole
    with pytest.raises(FrontmatterFieldError) as caught:
        make_doc(
            "x", f"doc_id: bad id {secret}", "type: policy", "owner: x", "review_by: 2028-01-01"
        )
    error = caught.value
    assert (error.source_path, error.field) == ("note.md", "doc_id")
    assert_no_leak(error, secret, "bad id")
    assert str(error) == "Frontmatter field is missing or invalid"


def test_yaml_syntax_errors_never_echo_values() -> None:  # [NEG]
    secret = "marker-" + "value"
    with pytest.raises(FrontmatterSyntaxError) as caught:
        parse_frontmatter(f"key: [{secret}", "a.md")
    assert_no_leak(caught.value, secret)


def test_an_alias_event_is_refused_by_the_event_check() -> None:  # [NEG]
    # An alias needs an anchor, and anchors are refused first, so the alias branch cannot be
    # reached through real YAML. Check the function directly so the branch is still covered.
    assert _event_rejection(yaml.AliasEvent("x")) == "yaml_alias"
    assert _event_rejection(yaml.ScalarEvent(None, None, (True, False), "v")) is None


@pytest.mark.parametrize(
    "mapping_lines",
    [
        ("control_mappings: [[SOC2:CC6.1]]",),
        ("control_mappings:", "  - [SOC2:CC6.1]"),
        ('control_mappings: ["SOC2:CC6.1", [NIST]]',),
    ],
)
def test_nested_lists_under_control_mappings_get_the_same_specific_code(
    mapping_lines: tuple[str, ...],
) -> None:  # [NEG]
    error = _field_error(*DEFAULT_FRONTMATTER, *mapping_lines)
    assert (error.field, error.code) == ("control_mappings", UNQUOTED_WIKILINK_CODE)
