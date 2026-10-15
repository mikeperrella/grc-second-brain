"""The status of a finding (ADR-019 decisions 2 and 9): code sets the ceiling, a verdict can only
keep or lower it, and a missing verdict is never stronger than a lowered one (owner decision N5)."""

import pytest

from grc_sidecar.contradictions.models import Claim, Pair
from grc_sidecar.contradictions.pairs import generate_pairs
from grc_sidecar.contradictions.status import ceiling_allows_conflict, decide

from .contradictionkit import claims_of


def pair_of(a_text: str, b_text: str) -> Pair:
    a: Claim = claims_of(a_text, chunk_id="x")[0]
    b: Claim = claims_of(b_text, chunk_id="y")[0]
    (pair,) = generate_pairs([a, b], {a.claim_id: frozenset("y"), b.claim_id: frozenset("x")})
    return pair


STRONG = ("Security logs are kept 90 days.", "Security logs are kept 365 days.")
WEAK = ("| Application logs | 14 days |", "| Security logs | 90 days |")
DAYS_MONTHS = ("Chat is kept 90 days.", "Chat is kept 6 months.")


@pytest.mark.parametrize(
    "texts", [STRONG, WEAK, DAYS_MONTHS], ids=["strong", "weak", "days_months"]
)
def test_with_no_verdict_nothing_is_a_value_conflict(texts: tuple[str, str]) -> None:  # [NEG] N5
    status, reasons, flags = decide(pair_of(*texts), keeps_conflict=None)
    assert status == "CANDIDATE"
    assert flags == ("model_not_consulted",)
    assert "no_model_verdict" in reasons


def test_the_reasons_name_what_lowered_the_finding() -> None:
    assert decide(pair_of(*STRONG), keeps_conflict=None)[1] == ("no_model_verdict",)
    assert decide(pair_of(*WEAK), keeps_conflict=None)[1] == (
        "weak_subject_overlap",
        "no_model_verdict",
    )
    assert decide(pair_of(*DAYS_MONTHS), keeps_conflict=None)[1] == (
        "days_vs_months",
        "no_model_verdict",
    )


def test_a_verdict_that_keeps_the_conflict_reaches_the_ceiling_and_no_further() -> None:
    assert decide(pair_of(*STRONG), keeps_conflict=True) == ("VALUE_CONFLICT", (), ())
    # The near-miss and a days-versus-months pair stay candidates whatever the verdict says.
    for texts in (WEAK, DAYS_MONTHS):
        assert not ceiling_allows_conflict(pair_of(*texts))
        assert decide(pair_of(*texts), keeps_conflict=True)[0] == "CANDIDATE"


def test_a_verdict_that_lowers_the_conflict_makes_a_candidate() -> None:
    status, reasons, flags = decide(pair_of(*STRONG), keeps_conflict=False)
    assert (status, reasons, flags) == ("CANDIDATE", ("model_lowered",), ())


def test_the_near_miss_is_never_a_value_conflict() -> None:  # [NEG]
    for verdict in (None, True, False):
        assert decide(pair_of(*WEAK), keeps_conflict=verdict)[0] != "VALUE_CONFLICT"
