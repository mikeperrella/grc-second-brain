"""The synthetic sample vault parses cleanly and still contains its deliberate defects."""

import re
from datetime import date

import pytest

from grc_sidecar.vault.chunker import chunk_document
from grc_sidecar.vault.clock import FixedClock, is_stale
from grc_sidecar.vault.links import ResolutionIndex, extract_links, resolve_links
from grc_sidecar.vault.models import ParsedDocument
from grc_sidecar.vault.scan import scan_vault

from .vaultkit import SAMPLE_README, SAMPLE_VAULT

EXPECTED_DOC_IDS = {
    "HR-POL-001": "access-control-and-mfa-policy.md",
    "HR-POL-002": "encryption-policy.md",
    "HR-POL-003": "incident-response-plan.md",
    "HR-POL-004": "vendor-management-policy.md",
    "HR-POL-005": "data-retention-policy.md",
    "HR-PRC-001": "mfa-enrollment-procedure.md",
}
MAPPING_SHAPES = {
    "SOC2": re.compile(r"^SOC2:(CC|A|C|PI|P)\d+\.\d+$"),
    "NIST-800-53": re.compile(r"^NIST-800-53:[A-Z]{2}-\d+(\(\d+\))?$"),
    "ISO27001": re.compile(r"^ISO27001:A\.\d+\.\d+$"),
}


@pytest.fixture(scope="module")
def docs() -> dict[str, ParsedDocument]:
    result = scan_vault(SAMPLE_VAULT)
    assert result.ok and result.issues == ()
    return {doc.frontmatter.doc_id: doc for doc in result.documents}


def test_the_sample_vault_has_exactly_the_documented_documents(
    docs: dict[str, ParsedDocument],
) -> None:
    assert {doc_id: doc.source_path for doc_id, doc in docs.items()} == EXPECTED_DOC_IDS


def test_the_readme_lives_outside_the_vault_root_so_it_is_never_indexed() -> None:
    assert SAMPLE_README.is_file()
    assert SAMPLE_README.parent == SAMPLE_VAULT.parent
    assert not (SAMPLE_VAULT / "README.md").exists()
    assert list(SAMPLE_VAULT.glob("*.md")) and not list(SAMPLE_VAULT.glob("**/README*"))


def test_everything_is_labelled_synthetic() -> None:
    for name in EXPECTED_DOC_IDS.values():
        text = (SAMPLE_VAULT / name).read_text(encoding="utf-8")
        assert "SYNTHETIC SAMPLE DATA" in text, name
        assert "fictional" in text.lower(), name
    readme = SAMPLE_README.read_text(encoding="utf-8").lower()
    assert "synthetic" in readme and "fictional" in readme


def test_every_wikilink_in_the_sample_vault_resolves(docs: dict[str, ParsedDocument]) -> None:
    index = ResolutionIndex((d.source_path, d.frontmatter.doc_id) for d in docs.values())
    forms = {"plain": 0, "alias": 0, "fragment": 0, "frontmatter": 0}
    for doc in docs.values():
        for resolved in resolve_links(extract_links(doc), index):
            assert resolved.status == "resolved", (doc.source_path, resolved.link.target)
            assert resolved.target_doc_id in docs
            link = resolved.link
            forms["frontmatter"] += link.origin == "frontmatter"
            forms["alias"] += link.alias is not None
            forms["fragment"] += link.fragment is not None
            forms["plain"] += link.alias is None and link.fragment is None
    assert all(count >= 1 for count in forms.values()), forms


def test_control_mappings_use_real_looking_identifiers(docs: dict[str, ParsedDocument]) -> None:
    for doc in docs.values():
        mappings = doc.frontmatter.control_mappings
        assert mappings, doc.source_path
        for mapping in mappings:
            framework = mapping.split(":", 1)[0]
            assert MAPPING_SHAPES[framework].match(mapping), mapping


def test_the_documented_contradiction_is_present(docs: dict[str, ParsedDocument]) -> None:
    retention = "\n".join(c.text for c in chunk_document(docs["HR-POL-005"]))
    incident = "\n".join(c.text for c in chunk_document(docs["HR-POL-003"]))
    assert "| Security logs | 90 days |" in retention
    assert "security logs are retained for 365 days" in incident


def test_exactly_one_document_is_stale_under_a_fixed_clock(docs: dict[str, ParsedDocument]) -> None:
    today = FixedClock(date(2026, 1, 1)).today()
    stale = {doc_id for doc_id, doc in docs.items() if is_stale(doc.frontmatter.review_by, today)}
    assert stale == {"HR-POL-004"}
    assert docs["HR-POL-004"].frontmatter.review_by == date(2024, 6, 30)


def test_the_stale_result_depends_on_the_injected_clock_only(
    docs: dict[str, ParsedDocument],
) -> None:
    before = FixedClock(date(2024, 1, 1)).today()
    far_future = FixedClock(date(2040, 1, 1)).today()
    assert not any(is_stale(d.frontmatter.review_by, before) for d in docs.values())
    assert all(is_stale(d.frontmatter.review_by, far_future) for d in docs.values())


def test_every_document_chunks_into_something_citable(docs: dict[str, ParsedDocument]) -> None:
    for doc in docs.values():
        chunks = chunk_document(doc)
        assert chunks, doc.source_path
        assert all(c.start_line >= doc.body_start_line for c in chunks)
