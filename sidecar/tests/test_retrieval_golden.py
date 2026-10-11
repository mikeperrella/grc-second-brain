"""Golden retrieval tests on the synthetic sample vault ("Halcyon Ridge") and the real packs.

Fake embedder, so these pin what the keyword side, the merge, the quota, the withdrawn handling and
the expansion do, NOT how well a real model understands paraphrase (that is the manual live
check, `test_retrieval_live.py`, which shares `retrieval_expectations.py` with this file).

The packs are indexed once per test session (about 20 seconds) and copied into each test that
needs its own vault.
"""

import asyncio
import re
import shutil
from pathlib import Path

import pytest

from grc_sidecar.retrieval.models import RetrievalResult, RetrievedChunk
from grc_sidecar.retrieval.query import build_lexical_query
from grc_sidecar.retrieval.selection import DEFAULT_CHAR_BUDGET, framework_char_cap

from .retrieval_expectations import (
    DOC_FILES,
    EXPANSION_QUESTION,
    IDENTIFIER_GROUPS,
    OVERDUE,
    QUESTIONS,
    IdentifierGroup,
)
from .retrievalkit import stack
from .vaultkit import REPO_ROOT, SAMPLE_VAULT

PACKS = REPO_ROOT / "packs"
RETENTION = "data-retention-policy.md"
VENDOR = "vendor-management-policy.md"


@pytest.fixture(scope="session")
def golden_index(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """An index of the sample vault and every committed pack, built once."""
    index_dir = tmp_path_factory.mktemp("golden-index")

    async def build() -> None:
        async with stack(SAMPLE_VAULT, index_dir, packs_dir=PACKS):
            pass

    asyncio.run(build())
    return index_dir


def ask(index_dir: Path, question: str, **kwargs: int) -> RetrievalResult:
    async def scenario() -> RetrievalResult:
        async with stack(SAMPLE_VAULT, index_dir, packs_dir=PACKS, sync=False) as s:
            return await s.retriever.retrieve(question, **kwargs)

    return asyncio.run(scenario())


def seeds(result: RetrievalResult, kind: str) -> list[RetrievedChunk]:
    return [c for c in result.chunks if c.role == "seed" and c.source_kind == kind]


# ----- the three questions, shared with the live check -------------------------------------------


@pytest.mark.parametrize(("question", "doc_ids"), sorted(QUESTIONS.items()))
def test_a_question_returns_the_documents_it_must(
    golden_index: Path, question: str, doc_ids: tuple[str, ...]
) -> None:
    result = ask(golden_index, question)
    found = {c.doc_id for c in result.chunks}
    assert set(doc_ids) <= found, f"{question!r} returned {sorted(found)}"
    assert result.mode == "hybrid"
    for doc_id in doc_ids:
        assert any(c.source_path == DOC_FILES[doc_id] for c in result.chunks)


def test_log_retention_returns_both_sides_of_the_contradiction(golden_index: Path) -> None:
    result = ask(golden_index, "log retention")
    company = [c for c in result.chunks if c.source_kind == "company_policy"]
    # data-retention-policy.md says security logs are kept 90 days; incident-response-plan.md, 365.
    assert any("90 days" in c.text for c in company if c.doc_id == "HR-POL-005")
    assert any("365 days" in c.text for c in company if c.doc_id == "HR-POL-003")


def test_overdue_flags_follow_the_review_dates(golden_index: Path) -> None:
    result = ask(golden_index, "vendor review", max_chunks=8)
    company = [c for c in result.chunks if c.source_kind == "company_policy"]
    by_doc = {c.doc_id: c.review_overdue for c in company}
    for doc_id, overdue in OVERDUE.items():
        assert by_doc[doc_id] is overdue
    assert all(not flag for doc_id, flag in by_doc.items() if doc_id not in OVERDUE)


# ----- an identifier in any written form finds the same control ----------------------------------


def control_chunks(result: RetrievalResult, group: IdentifierGroup) -> list[RetrievedChunk]:
    return [
        c
        for c in result.chunks
        if c.source_kind == "framework_reference"
        and Path(c.source_path).name == group.file_name
        and (group.marker in c.heading_path or group.marker in c.text)
    ]


@pytest.mark.parametrize("group", IDENTIFIER_GROUPS, ids=lambda g: g.label)
def test_every_written_form_returns_the_same_control(  # [NEG] mutation row 29
    golden_index: Path, group: IdentifierGroup
) -> None:
    firsts: dict[str, tuple[str, str]] = {}
    for form in group.forms:
        result = ask(golden_index, form)
        chunks = control_chunks(result, group)
        assert chunks, f"{form!r} did not return the {group.label} control"
        # it comes first among the framework results, not behind unrelated text
        first = next(c for c in result.chunks if c.source_kind == "framework_reference")
        assert first.chunk_id in {c.chunk_id for c in chunks}, f"{form!r}: not the first hit"
        firsts[form] = (first.source_path, first.heading_path)
    # Every form leads with the same control (a long control can span two chunks, and which of
    # them comes first may vary with the vector side, so the heading is compared, not the chunk).
    assert len(set(firsts.values())) == 1, firsts


def test_an_identifier_does_not_find_a_longer_identifier_as_its_control(golden_index: Path) -> None:
    # AC-2 is AC-2, not AC-20 or the enhancement AC-2(4).
    result = ask(golden_index, "AC-2")
    first = next(c for c in result.chunks if c.source_kind == "framework_reference")
    assert first.heading_path.endswith("AC-2 Account Management")


def test_framework_chunks_say_which_pack_they_came_from(golden_index: Path) -> None:
    result = ask(golden_index, "03.01.01")
    first = next(c for c in result.chunks if c.source_kind == "framework_reference")
    assert first.pack_id == "nist-sp800-171-r3"  # so Rev 3 text is never presented as Rev 2


# ----- framework text never crowds out company policy --------------------------------------------


def test_framework_results_never_displace_company_policy_beyond_the_cap(  # [NEG] mutation row 2
    golden_index: Path,
) -> None:
    # A question made of control language: the framework side ranks high for it.
    question = "access enforcement least privilege separation of duties account management"
    result = ask(golden_index, question)
    framework = [c for c in result.chunks if c.source_kind == "framework_reference"]
    company = [c for c in result.chunks if c.source_kind == "company_policy"]
    assert framework, "the fixture must retrieve framework text, or the cap is never reached"
    assert len(framework) <= 2
    assert sum(len(c.text) for c in framework) <= framework_char_cap(DEFAULT_CHAR_BUDGET)
    assert len([c for c in company if c.role == "seed"]) >= 2, "company policy lost its seeds"
    assert result.budget.used_chars <= DEFAULT_CHAR_BUDGET


# ----- the reserved expansion slot ----------------------------------------------------------------

LINK_30 = "as required by the [[vendor-management-policy]]."
LINK_30_PLAIN = "as required by the vendor management policy."
LINK_7 = 'links: ["[[vendor-management-policy]]"]\n'


def vault_variant(tmp_path: Path, *, drop_frontmatter: bool, drop_body: bool) -> Path:
    """A copy of the sample vault with the retention policy's links to the vendor policy removed:
    the frontmatter `links` (line 7), the body link (line 30), or both. Nothing else changes."""
    vault = tmp_path / "variant-vault"
    shutil.copytree(SAMPLE_VAULT, vault)
    path = vault / RETENTION
    text = path.read_text(encoding="utf-8")
    if drop_frontmatter:
        assert LINK_7 in text
        text = text.replace(LINK_7, "")
    if drop_body:
        assert LINK_30 in text
        text = text.replace(LINK_30, LINK_30_PLAIN)
    path.write_text(text, encoding="utf-8", newline="\n")
    return vault


def ask_variant(
    golden_index: Path, vault: Path, index_dir: Path, **kwargs: int
) -> tuple[RetrievalResult, list[str]]:
    async def scenario() -> tuple[RetrievalResult, list[str]]:
        async with stack(
            vault,
            index_dir,
            packs_dir=PACKS,
            reuse_packs_index=golden_index / "packs.sqlite3",
        ) as s:
            assert await s.vault_repo.open_for_deletions()
            resolved = [
                r.link.source_line
                for r in await s.vault_repo.links_for(RETENTION)
                if r.status == "resolved"
            ]
            result = await s.retriever.retrieve(EXPANSION_QUESTION, **kwargs)
            return result, [str(n) for n in resolved]

    return asyncio.run(scenario())


def test_expansion_slot_survives_a_full_seed_set(  # [NEG] mutation row 23
    golden_index: Path, tmp_path: Path
) -> None:
    # With no reserved slot these candidates fill five seed slots (3 company + 2 framework). The
    # reserved slot keeps room for a linked chunk: 2 company + 2 framework + 1 expanded.
    vault = vault_variant(tmp_path, drop_frontmatter=False, drop_body=False)
    result, _ = ask_variant(golden_index, vault, tmp_path / "idx")
    company, framework = seeds(result, "company_policy"), seeds(result, "framework_reference")
    (linked,) = [c for c in result.chunks if c.role == "expanded"]
    assert (len(company), len(framework)) == (2, 2)
    assert len(result.chunks) == 5
    # the precondition: the vendor policy is not itself a seed, so it can only arrive by the link
    assert VENDOR not in {c.source_path for c in company + framework}
    assert linked.source_path == VENDOR
    assert linked.expanded_from[0] in {c.chunk_id for c in company}
    assert all(c.source_path == RETENTION for c in company)


def test_the_body_link_at_line_30_alone_supplies_the_expanded_chunk(
    golden_index: Path, tmp_path: Path
) -> None:
    # data-retention-policy.md line 30: "...as required by the [[vendor-management-policy]]."
    # With the frontmatter link (line 7) removed, only that body link can lead to the vendor policy,
    # and it belongs to the chunk that contains it, the "Deletion" section. (Removing the
    # frontmatter line moves the body link from line 30 to line 29 in this copy.)
    vault = vault_variant(tmp_path, drop_frontmatter=True, drop_body=False)
    result, resolved_lines = ask_variant(golden_index, vault, tmp_path / "idx")
    assert resolved_lines == ["29"], "exactly one resolved link must be left: the body link"
    link_line = int(resolved_lines[0])
    (linked,) = [c for c in result.chunks if c.role == "expanded"]
    assert linked.source_path == VENDOR
    (origin,) = [c for c in result.chunks if c.chunk_id == linked.expanded_from[0]]
    assert origin.source_path == RETENTION
    assert origin.start_line <= link_line <= origin.end_line
    assert origin.heading_path.endswith("Deletion")


def test_the_frontmatter_link_alone_also_supplies_it(golden_index: Path, tmp_path: Path) -> None:
    vault = vault_variant(tmp_path, drop_frontmatter=False, drop_body=True)
    result, resolved_lines = ask_variant(golden_index, vault, tmp_path / "idx")
    assert len(resolved_lines) == 1 and int(resolved_lines[0]) < 10  # the frontmatter line
    (linked,) = [c for c in result.chunks if c.role == "expanded"]
    assert linked.source_path == VENDOR


def test_the_slot_goes_to_a_company_seed_when_no_link_exists(  # [NEG] mutation row 24
    golden_index: Path, tmp_path: Path
) -> None:
    # Same question, same candidates; the ONLY change is that the retention policy's two links to
    # the vendor policy are gone.
    with_links = vault_variant(tmp_path / "a", drop_frontmatter=False, drop_body=False)
    without = vault_variant(tmp_path / "b", drop_frontmatter=True, drop_body=True)
    linked_result, linked_lines = ask_variant(golden_index, with_links, tmp_path / "a-idx")
    result, resolved_lines = ask_variant(golden_index, without, tmp_path / "b-idx")
    assert linked_lines != [] and resolved_lines == [], "no resolved link may leave the seeds"
    assert [c.role for c in linked_result.chunks].count("expanded") == 1
    assert [c.role for c in result.chunks].count("expanded") == 0
    company, framework = seeds(result, "company_policy"), seeds(result, "framework_reference")
    assert (len(company), len(framework)) == (3, 2)
    assert len(result.chunks) == 5

    # The first four seeds are the same sections: the link only decides who gets the last slot.
    # (Chunk ids differ because editing the file changed its hash; the sections are compared.)
    def sections(r: RetrievalResult) -> list[tuple[str, str]]:
        return [(c.source_path, c.heading_path) for c in r.chunks[:4]]

    assert sections(result) == sections(linked_result)


def test_with_expansion_off_the_same_candidates_fill_the_slot_with_a_company_seed(
    golden_index: Path, tmp_path: Path
) -> None:
    vault = vault_variant(tmp_path, drop_frontmatter=False, drop_body=False)
    result, _ = ask_variant(golden_index, vault, tmp_path / "idx", expansion_depth=0)
    assert [c.role for c in result.chunks].count("expanded") == 0
    company, framework = seeds(result, "company_policy"), seeds(result, "framework_reference")
    assert (len(company), len(framework)) == (3, 2)


# ----- withdrawn controls -------------------------------------------------------------------------

WITHDRAWN_TITLE = "Withdrawn controls"


def withdrawn_chunks(result: RetrievalResult) -> list[RetrievedChunk]:
    return [c for c in result.chunks if c.heading_path.endswith(WITHDRAWN_TITLE)]


def packs_lexical(golden_index: Path, question: str) -> list[str]:
    """The headings of the pack chunks the keyword search finds for `question`: a precondition."""

    async def scenario() -> list[str]:
        async with stack(SAMPLE_VAULT, golden_index, packs_dir=PACKS, sync=False) as s:
            assert s.packs_repo is not None and await s.packs_repo.open_for_deletions()
            hits = await s.packs_repo.lexical_search(build_lexical_query(question).match, 20)
            return [h.chunk.heading_path for h in hits]

    return asyncio.run(scenario())


def test_withdrawn_controls_are_excluded_by_default(  # [NEG] mutation row 4
    golden_index: Path,
) -> None:
    # A question that is not an identifier, and that the withdrawn sections match word for word.
    question = "These controls are withdrawn They are listed so old references still resolve"
    assert any(h.endswith(WITHDRAWN_TITLE) for h in packs_lexical(golden_index, question))
    result = ask(golden_index, question, max_chunks=8)
    assert withdrawn_chunks(result) == []
    assert not any(c.withdrawn for c in result.chunks)


def test_an_exact_withdrawn_id_returns_its_labeled_stub(  # [NEG] mutation row 5
    golden_index: Path,
) -> None:
    result = ask(golden_index, "03.01.13")
    stubs = withdrawn_chunks(result)
    assert stubs, "the stub chunk must come back for its own identifier"
    for chunk in stubs:
        assert chunk.withdrawn is True
        assert re.search(r"^- \*\*03\.01\.13\*\*: Withdrawn\.", chunk.text, re.MULTILINE)
        assert chunk.source_kind == "framework_reference"
        assert chunk.pack_id == "nist-sp800-171-r3"


def test_the_withdrawn_exception_needs_the_exact_identifier(  # [NEG] mutation row 5
    golden_index: Path,
) -> None:
    # 03.13.08 is the control that the withdrawn stub 03.01.13 says it is "Addressed by". The stub
    # chunk CONTAINS "03.13.08" and the keyword search finds it, but 03.13.08 is not itself a
    # withdrawn ID, so the stub must stay out. (With an always-on exception the stub chunk comes
    # back for this question; that is what makes this test able to fail.)
    assert any(h.endswith(WITHDRAWN_TITLE) for h in packs_lexical(golden_index, "03.13.08"))
    result = ask(golden_index, "03.13.08", max_chunks=8)
    assert withdrawn_chunks(result) == []
    assert any("03.13.08" in c.heading_path for c in result.chunks), "the real control is found"


def test_a_withdrawn_id_inside_a_longer_question_gets_no_exception(  # [NEG]
    golden_index: Path,
) -> None:
    result = ask(golden_index, "03.01.13 requirement text", max_chunks=8)
    assert withdrawn_chunks(result) == []


def test_a_withdrawn_enhancement_in_800_53_is_labeled_too(golden_index: Path) -> None:
    result = ask(golden_index, "AC-3(1)")
    stubs = withdrawn_chunks(result)
    assert stubs and all(c.withdrawn for c in stubs)
    assert any("AC-3(1)" in c.text and "Withdrawn." in c.text for c in stubs)
