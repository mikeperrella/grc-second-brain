"""Candidate pairs (ADR-019 decision 7): comparable kinds, different values, overlap, the cap."""

import pytest

from grc_sidecar.contradictions.models import Claim
from grc_sidecar.contradictions.pairs import (
    apply_cap,
    comparable,
    generate_pairs,
    overlap_of,
)
from grc_sidecar.contradictions.service import build_report

from .contradictionkit import claims_of, text_chunk


def claim(text: str, chunk_id: str = "c1") -> Claim:
    (found,) = claims_of(text, chunk_id=chunk_id)
    return found


def everywhere(*claims: Claim) -> dict[str, frozenset[str]]:
    """Every claim may be compared with every chunk (the lexical search is tested elsewhere)."""
    chunks = frozenset(c.chunk_id for c in claims)
    return {c.claim_id: chunks for c in claims}


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("Logs are kept 90 days.", "Logs are kept 30 days.", True),
        (
            "Logs are kept 90 days.",
            "Logs are kept 3 months.",
            True,
        ),  # a candidate, never a conflict
        ("Logs are kept 90 days.", "Logs are kept 24 hours.", True),  # both become days
        ("Reviews run twice a year.", "Reviews run once a year.", True),
        ("Reviews run twice a year.", "Reviews run 90 days.", False),  # frequency vs duration
        ("Reviews run twice a year.", "Reviews run 3 months.", False),
        ("Reviews run in ten business days.", "Reviews run in 10 days.", False),
        ("Pass is 12 characters.", "Pass is 12 attempts.", False),
        ("Uptime is 99%.", "Uptime is 99 days.", False),
    ],
)
def test_which_kinds_are_comparable(a: str, b: str, expected: bool) -> None:
    assert comparable(claim(a, "x"), claim(b, "y")) is expected


def test_overlap_is_strong_inside_weak_beside_and_none_apart() -> None:
    security = claim("| Security logs | 90 days |")
    assert overlap_of(security, claim("security logs are kept for 365 days.", "y")) == "strong"
    assert overlap_of(
        claim("Backups are kept 35 days."), claim("Database backups go 30 days.", "y")
    ) == ("strong")
    assert overlap_of(claim("| Application logs | 14 days |"), security) == "weak"
    assert (
        overlap_of(
            claim("Fire drills are held twice a year."), claim("Tests run once a year.", "y")
        )
        is None
    )


def test_the_near_miss_is_a_weak_candidate_and_never_strong() -> None:
    # "Application logs 14 days" against "Security logs 90 days": they share only "logs".
    app = claim("| Application logs | 14 days |", "a")
    sec = claim("| Security logs | 90 days |", "b")
    (pair,) = generate_pairs([app, sec], everywhere(app, sec))
    assert pair.overlap == "weak"


def test_equal_values_make_no_pair() -> None:
    a, b = claim("Logs are kept 90 days."), claim("Logs are kept 90 days.", "y")
    assert generate_pairs([a, b], everywhere(a, b)) == []
    c, d = claim("Logs are kept 24 hours."), claim("Logs are kept 1 day.", "y")
    assert generate_pairs([c, d], everywhere(c, d)) == []  # the same after conversion


def test_a_frequency_is_never_paired_with_a_duration() -> None:
    a, b = claim("Access is reviewed twice a year."), claim("Access is reviewed 90 days.", "y")
    assert generate_pairs([a, b], everywhere(a, b)) == []


def test_days_against_months_is_a_pair_with_different_kinds() -> None:
    a, b = claim("Chat is kept 90 days."), claim("Chat is kept 6 months.", "y")
    (pair,) = generate_pairs([a, b], everywhere(a, b))
    assert {pair.a.number.kind, pair.b.number.kind} == {"days", "months"}


def test_a_claim_is_only_compared_with_its_neighbours() -> None:
    a, b = claim("Logs are kept 90 days."), claim("Logs are kept 30 days.", "y")
    assert generate_pairs([a, b], {a.claim_id: frozenset(), b.claim_id: frozenset()}) == []
    assert len(generate_pairs([a, b], {a.claim_id: frozenset({"y"}), b.claim_id: frozenset()})) == 1


def test_each_unordered_pair_appears_once_in_file_order() -> None:
    a, b = claim("Logs are kept 90 days.", "x"), claim("Logs are kept 30 days.", "y")
    forward = generate_pairs([a, b], everywhere(a, b))
    backward = generate_pairs([b, a], everywhere(a, b))
    assert len(forward) == len(backward) == 1
    assert forward[0].pair_id == backward[0].pair_id
    assert forward[0].a.chunk_id == "x"


def test_strong_pairs_come_before_weak_ones() -> None:
    strong1 = claim("Security logs are kept 90 days.", "x")
    strong2 = claim("Security logs are kept 30 days.", "y")
    weak = claim("| Audit logs | 7 days |", "z")
    pairs = generate_pairs([weak, strong1, strong2], everywhere(strong1, strong2, weak))
    assert pairs[0].overlap == "strong"
    assert pairs[-1].overlap == "weak"


def test_apply_cap_counts_what_it_leaves_out() -> None:
    assert apply_cap([1, 2, 3, 4, 5], 2) == ([1, 2], 3)
    assert apply_cap([1], 5) == ([1], 0)


def test_the_cap_limits_the_findings_and_counts_the_rest() -> None:  # [NEG] DoS (F33-7)
    texts = {
        f"c{n}": text_chunk(f"Visitor badge records are kept for {n * 10} days.", chunk_id=f"c{n}")
        for n in range(1, 7)
    }
    claims = [c for t in texts.values() for c in claims_of(t.text, chunk_id=t.chunk_id)]
    every = frozenset(texts)
    neighbours = {c.claim_id: every for c in claims}
    report = build_report(texts, claims, neighbours, cap=4)
    assert report.pairs_generated == 15  # 6 chunks, every pair of different values
    assert len(report.findings) == 4
    assert report.dropped == {"over_cap": 11}
    assert len(build_report(texts, claims, neighbours, cap=100).findings) == 15
