"""Graph expansion from the seeds along explicit links (CLAUDE.md 8.2, ADR-010, M4f).

Small synthetic vaults. Every question uses `max_chunks=2`, which leaves exactly one seed slot and
the one expansion slot, so the seed is the single best chunk and what the expansion slot holds is
visible. Nothing here needs Ollama.
"""

import asyncio
from pathlib import Path

import pytest

from grc_sidecar.retrieval.models import RetrievalResult, RetrievedChunk
from grc_sidecar.vault.models import Chunk

from .indexkit import write_doc
from .repokit import open_raw
from .retrievalkit import Stack, stack

QUESTION = "zebrafish"
SEED_TEXT = "# Alpha\n\nThe zebrafish control applies everywhere."


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    return tmp_path / "vault"


@pytest.fixture
def index_dir(tmp_path: Path) -> Path:
    return tmp_path / "index"


def run_question(
    vault: Path, index_dir: Path, *, question: str = QUESTION, **kwargs: int
) -> RetrievalResult:
    async def scenario() -> RetrievalResult:
        async with stack(vault, index_dir) as s:
            return await s.retriever.retrieve(question, max_chunks=2, **kwargs)

    return asyncio.run(scenario())


def expanded(result: RetrievalResult) -> list[RetrievedChunk]:
    return [c for c in result.chunks if c.role == "expanded"]


def filler(words: int) -> str:
    return " ".join(f"filler{n}" for n in range(words))


def test_a_body_link_leads_to_the_first_chunk_of_the_target(vault: Path, index_dir: Path) -> None:
    write_doc(vault, "alpha.md", f"{SEED_TEXT} See [[beta]].")
    write_doc(vault, "beta.md", "# Beta\n\n## First\n\nfirst section text.\n\n## Second\n\nsecond.")
    result = run_question(vault, index_dir)
    (linked,) = expanded(result)
    assert linked.source_path == "beta.md"
    assert linked.heading_path.endswith("First")  # the lead chunk, not "Second"
    (seed,) = [c for c in result.chunks if c.role == "seed"]
    assert seed.source_path == "alpha.md"
    assert linked.expanded_from == (seed.chunk_id,)
    assert (linked.lexical_rank, linked.vector_rank) == (None, None)  # reached, not ranked
    assert linked.source_kind == "company_policy"


def test_a_heading_fragment_selects_the_chunk_under_that_heading(
    vault: Path, index_dir: Path
) -> None:
    write_doc(vault, "alpha.md", f"{SEED_TEXT} See [[beta#Second]].")
    write_doc(vault, "beta.md", "# Beta\n\n## First\n\nfirst section text.\n\n## Second\n\nsecond.")
    (linked,) = expanded(run_question(vault, index_dir))
    assert linked.heading_path.endswith("Second")
    assert "second." in linked.text


@pytest.mark.parametrize("fragment", ["#Missing heading", "#^block-id"])
def test_a_fragment_that_matches_nothing_falls_back_to_the_lead_chunk(
    vault: Path, index_dir: Path, fragment: str
) -> None:
    write_doc(vault, "alpha.md", f"{SEED_TEXT} See [[beta{fragment}]].")
    write_doc(vault, "beta.md", "# Beta\n\n## First\n\nfirst section text.\n\n## Second\n\nsecond.")
    (linked,) = expanded(run_question(vault, index_dir))
    assert linked.heading_path.endswith("First")


def test_a_frontmatter_link_applies_to_every_chunk_of_its_document(
    vault: Path, index_dir: Path
) -> None:
    # The seed is the SECOND section; the link is declared in the frontmatter, so it still leads
    # on from whichever chunk of the document was found.
    body = "# Alpha\n\n## One\n\nplain words only.\n\n## Two\n\nThe zebrafish control."
    write_doc(vault, "alpha.md", body, links=["beta"])
    write_doc(vault, "beta.md", "# Beta\n\nbeta text.")
    result = run_question(vault, index_dir)
    (seed,) = [c for c in result.chunks if c.role == "seed"]
    assert seed.heading_path.endswith("Two")
    (linked,) = expanded(result)
    assert linked.source_path == "beta.md"


def test_a_body_link_belongs_to_the_chunk_it_is_written_in(vault: Path, index_dir: Path) -> None:
    body = (
        "# Alpha\n\n## One\n\nThe zebrafish control. See [[beta]].\n\n"
        "## Two\n\nplain words only. See [[gamma]]."
    )
    write_doc(vault, "alpha.md", body)
    write_doc(vault, "beta.md", "# Beta\n\nbeta text.")
    write_doc(vault, "gamma.md", "# Gamma\n\ngamma text.")
    result = run_question(vault, index_dir)
    (seed,) = [c for c in result.chunks if c.role == "seed"]
    assert seed.heading_path.endswith("One")
    (linked,) = expanded(result)
    assert linked.source_path == "beta.md"  # not gamma: its link is in the other section


def test_expansion_never_follows_similarity(vault: Path, index_dir: Path) -> None:  # [NEG] row 6
    # beta and gamma are about the same thing as alpha, but nothing links to them.
    write_doc(vault, "alpha.md", SEED_TEXT)
    write_doc(vault, "beta.md", "# Beta\n\nThe zebrafish control applies here too.")
    write_doc(vault, "gamma.md", "# Gamma\n\nAnother zebrafish control paragraph.")
    result = run_question(vault, index_dir)
    assert expanded(result) == []
    assert all(c.expanded_from == () for c in result.chunks)


def test_unresolved_ambiguous_and_rejected_links_are_not_followed(  # [NEG] mutation row 7
    vault: Path, index_dir: Path
) -> None:
    (vault / "sub1").mkdir(parents=True)
    (vault / "sub2").mkdir(parents=True)
    write_doc(vault, "sub1/dup.md", "# Dup\n\nfirst copy.", doc_id="DUP-1")
    write_doc(vault, "sub2/dup.md", "# Dup\n\nsecond copy.", doc_id="DUP-2")
    body = f"{SEED_TEXT} See [[nowhere]], [[dup]] and [[../escape]]."
    write_doc(vault, "alpha.md", body)

    async def scenario() -> tuple[set[str], RetrievalResult]:
        async with stack(vault, index_dir) as s:
            assert await s.vault_repo.open_for_deletions()
            statuses = {r.status for r in await s.vault_repo.links_for("alpha.md")}
            return statuses, await s.retriever.retrieve(QUESTION, max_chunks=2)

    statuses, result = asyncio.run(scenario())
    assert statuses == {"unresolved", "ambiguous", "rejected"}, "the fixture needs all three"
    assert expanded(result) == []


def test_a_link_row_marked_unresolved_is_not_followed_even_if_it_has_a_target(  # [NEG] row 7
    vault: Path, index_dir: Path
) -> None:
    # Normally an unresolved link has no target, so "no target" would stop it anyway. Here the
    # index row is damaged: it says `unresolved` but carries a target. The status alone must stop
    # it, so this test fails if the status check is removed while the no-target check stays.
    write_doc(vault, "alpha.md", f"{SEED_TEXT} See [[nowhere]].")
    write_doc(vault, "beta.md", "# Beta\n\nbeta text.", doc_id="BETA")

    async def build() -> None:
        async with stack(vault, index_dir):
            pass

    asyncio.run(build())
    con = open_raw(index_dir / "vault.sqlite3")
    try:
        changed = con.execute(
            "UPDATE links SET target_doc_id = 'BETA' WHERE status = 'unresolved'"
        ).rowcount
    finally:
        con.close()
    assert changed == 1, "the fixture needs exactly one unresolved link"

    async def ask() -> RetrievalResult:
        async with stack(vault, index_dir, sync=False) as s:
            return await s.retriever.retrieve(QUESTION, max_chunks=2)

    assert expanded(asyncio.run(ask())) == []


def test_expansion_stops_at_the_depth_limit(vault: Path, index_dir: Path) -> None:  # [NEG] row 8
    # alpha -> beta -> gamma -> delta. beta and gamma are too big for the budget, delta fits, so
    # delta is reachable only if the walk goes three levels deep.
    write_doc(vault, "alpha.md", f"{SEED_TEXT} See [[beta]].")
    write_doc(vault, "beta.md", f"# Beta\n\n{filler(120)} See [[gamma]].")
    write_doc(vault, "gamma.md", f"# Gamma\n\n{filler(120)} See [[delta]].")
    write_doc(vault, "delta.md", "# Delta\n\ndelta text.")
    for depth in (1, 2):
        result = run_question(vault, index_dir, expansion_depth=depth, char_budget=400)
        assert expanded(result) == [], f"depth {depth} must not reach delta"
        assert result.budget.used_chars <= 400


def test_depth_two_follows_a_second_link_when_the_first_does_not_fit(  # [NEG]
    vault: Path, index_dir: Path
) -> None:
    write_doc(vault, "alpha.md", f"{SEED_TEXT} See [[beta]].")
    write_doc(vault, "beta.md", f"# Beta\n\n{filler(120)} See [[gamma]].")  # too big
    write_doc(vault, "gamma.md", "# Gamma\n\ngamma text.")
    assert expanded(run_question(vault, index_dir, expansion_depth=1, char_budget=400)) == []
    (linked,) = expanded(run_question(vault, index_dir, expansion_depth=2, char_budget=400))
    assert linked.source_path == "gamma.md"


def test_expansion_can_be_turned_off(vault: Path, index_dir: Path) -> None:
    write_doc(vault, "alpha.md", f"{SEED_TEXT} See [[beta]].")
    write_doc(vault, "beta.md", "# Beta\n\nbeta text.")
    assert expanded(run_question(vault, index_dir, expansion_depth=0)) == []


def test_a_cycle_adds_nothing_twice(vault: Path, index_dir: Path) -> None:  # [NEG] mutation row 9
    # alpha <-> beta. beta is too big to be taken, so the second level reaches alpha again: the
    # seed itself, which must not come back as an "expanded" chunk.
    write_doc(vault, "alpha.md", f"{SEED_TEXT} See [[beta]].")
    write_doc(vault, "beta.md", f"# Beta\n\n{filler(120)} Back to [[alpha]].")
    result = run_question(vault, index_dir, expansion_depth=2, char_budget=400)
    ids = [c.chunk_id for c in result.chunks]
    assert len(ids) == len(set(ids)), "a chunk appeared twice"
    assert expanded(result) == []


def test_a_cycle_with_room_expands_once(vault: Path, index_dir: Path) -> None:
    write_doc(vault, "alpha.md", f"{SEED_TEXT} See [[beta]].")
    write_doc(vault, "beta.md", "# Beta\n\nbeta text. Back to [[alpha]].")
    result = run_question(vault, index_dir, expansion_depth=2)
    assert [c.source_path for c in expanded(result)] == ["beta.md"]
    ids = [c.chunk_id for c in result.chunks]
    assert len(ids) == len(set(ids))


def test_the_linked_chunk_must_fit_the_budget_and_is_reported_when_it_does_not(
    vault: Path, index_dir: Path
) -> None:  # [NEG]
    write_doc(vault, "alpha.md", f"{SEED_TEXT} See [[beta]].")
    write_doc(vault, "beta.md", f"# Beta\n\n{filler(120)}")
    result = run_question(vault, index_dir, char_budget=400)
    assert expanded(result) == []
    assert result.budget.used_chars <= 400
    beta_ids = {c.chunk_id for c in asyncio.run(_chunks(vault, index_dir, "beta.md"))}
    assert beta_ids & set(result.budget.dropped_for_budget)


async def _chunks(vault: Path, index_dir: Path, path: str) -> list[Chunk]:
    async with stack(vault, index_dir) as s:
        assert await s.vault_repo.open_for_deletions()
        return await s.vault_repo.chunks_for(path)


def test_both_a_frontmatter_and_a_body_link_to_one_target_give_one_chunk(
    vault: Path, index_dir: Path
) -> None:
    write_doc(vault, "alpha.md", f"{SEED_TEXT} See [[beta]].", links=["beta"])
    write_doc(vault, "beta.md", "# Beta\n\nbeta text.")
    result = run_question(vault, index_dir)
    assert [c.source_path for c in expanded(result)] == ["beta.md"]


def test_expansion_never_takes_a_seed_slot(vault: Path, index_dir: Path) -> None:
    write_doc(vault, "alpha.md", f"{SEED_TEXT} See [[beta]].")
    write_doc(vault, "beta.md", "# Beta\n\nbeta text.")
    write_doc(vault, "gamma.md", "# Gamma\n\nOther zebrafish words.")

    async def scenario() -> tuple[RetrievalResult, RetrievalResult]:
        async with stack(vault, index_dir) as s:
            on = await s.retriever.retrieve(QUESTION, max_chunks=3)
            off = await s.retriever.retrieve(QUESTION, max_chunks=3, expansion_depth=0)
            return on, off

    on, off = asyncio.run(scenario())
    seeds_on = [c.chunk_id for c in on.chunks if c.role == "seed"]
    seeds_off = [c.chunk_id for c in off.chunks if c.role == "seed"]
    assert seeds_on == seeds_off[:2], "the same two seeds, with or without expansion"
    assert len(on.chunks) <= 3 and len(off.chunks) <= 3


def test_the_slot_goes_to_a_company_seed_when_there_is_no_link(  # [NEG] mutation row 24
    vault: Path, index_dir: Path
) -> None:
    write_doc(vault, "alpha.md", SEED_TEXT)
    write_doc(vault, "beta.md", "# Beta\n\nThe zebrafish control applies here too.")
    result = run_question(vault, index_dir)
    assert len(result.chunks) == 2, "the reserved slot must not stay empty"
    assert [c.role for c in result.chunks] == ["seed", "seed"]


def test_an_expanded_chunk_carries_its_own_provenance(vault: Path, index_dir: Path) -> None:
    write_doc(vault, "alpha.md", f"{SEED_TEXT} See [[beta]].")
    write_doc(vault, "beta.md", "# Beta\n\nbeta text.")

    async def scenario() -> None:
        async with stack(vault, index_dir) as s:
            result = await s.retriever.retrieve(QUESTION, max_chunks=2)
            (linked,) = expanded(result)
            stored = {c.chunk_id: c for c in await s.vault_repo.chunks_for("beta.md")}
            assert stored[linked.chunk_id].text == linked.text
            assert (linked.start_line, linked.end_line) == (
                stored[linked.chunk_id].start_line,
                stored[linked.chunk_id].end_line,
            )
            assert linked.doc_type == "policy" and linked.review_by is not None

    asyncio.run(scenario())


def test_the_stack_helper_exposes_what_these_tests_use() -> None:
    assert {"vault_repo", "retriever"} <= set(Stack.__dataclass_fields__)
