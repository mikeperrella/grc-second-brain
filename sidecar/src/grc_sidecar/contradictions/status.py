"""The status of a finding, computed by code (ADR-019 decisions 2 and 9; ADR-013 decision 3).

**Code sets the ceiling.** A finding may be `VALUE_CONFLICT` only if the code facts allow it: the
two numbers are of the same kind (a days-versus-months pair never is) and the subjects overlap
strongly. **A model verdict can only keep a finding at its ceiling or lower it.** And a missing
verdict is never stronger than a verdict that lowered it: with no verdict the finding is a
`CANDIDATE` flagged `model_not_consulted` (owner decision N5), the way a missing M5 check never
ends `SUPPORTED`.

`decide_from_verdict` takes the validated verdict (or `None` for no verdict) and turns it into the
plain `bool | None` that `decide` uses: `True` only for `conflict` true with the relation
`same_subject_value_mismatch`, `False` for any other valid reply, `None` for no verdict.
"""

from grc_sidecar.contradictions.models import (
    CandidateReason,
    FindingStatus,
    Flag,
    Pair,
    PairVerdict,
)


def ceiling_allows_conflict(pair: Pair) -> bool:
    return pair.a.number.kind == pair.b.number.kind and pair.overlap == "strong"


def keeps_conflict_of(verdict: PairVerdict | None) -> bool | None:
    """None = no valid verdict; True = the verdict keeps the conflict; False = it lowers it."""
    if verdict is None:
        return None
    return verdict.conflict and verdict.relation == "same_subject_value_mismatch"


def decide(
    pair: Pair, *, keeps_conflict: bool | None, quote_invalid: bool = False
) -> tuple[FindingStatus, tuple[CandidateReason, ...], tuple[Flag, ...]]:
    """`keeps_conflict`: None = no valid model verdict, True = the verdict keeps the conflict,
    False = the verdict lowered it. `quote_invalid` says the model's reply was refused because a
    quote it gave was not in the shown sources (it only adds a reason when there is no verdict)."""
    reasons: list[CandidateReason] = []
    if pair.a.number.kind != pair.b.number.kind:
        reasons.append("days_vs_months")
    if pair.overlap != "strong":
        reasons.append("weak_subject_overlap")
    flags: tuple[Flag, ...] = ()
    if keeps_conflict is None:
        reasons.append("no_model_verdict")
        if quote_invalid:
            reasons.append("model_quote_not_verified")
        flags = ("model_not_consulted",)
    elif not keeps_conflict:
        reasons.append("model_lowered")
    if ceiling_allows_conflict(pair) and keeps_conflict is True:
        return "VALUE_CONFLICT", (), ()
    return "CANDIDATE", tuple(reasons), flags


def decide_from_verdict(
    pair: Pair, verdict: PairVerdict | None, *, quote_invalid: bool = False
) -> tuple[FindingStatus, tuple[CandidateReason, ...], tuple[Flag, ...]]:
    """The status of a pair from the model's validated verdict. The verdict can only keep the
    finding at the ceiling the code facts set, or lower it."""
    return decide(pair, keeps_conflict=keeps_conflict_of(verdict), quote_invalid=quote_invalid)
