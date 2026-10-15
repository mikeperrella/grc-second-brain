"""The status of a finding, computed by code (ADR-019 decisions 2 and 9; ADR-013 decision 3).

**Code sets the ceiling.** A finding may be `VALUE_CONFLICT` only if the code facts allow it: the
two numbers are of the same kind (a days-versus-months pair never is) and the subjects overlap
strongly. **A model verdict can only keep a finding at its ceiling or lower it.** And a missing
verdict is never stronger than a verdict that lowered it: with no verdict the finding is a
`CANDIDATE` flagged `model_not_consulted` (owner decision N5), the way a missing M5 check never
ends `SUPPORTED`.

In chunk 4a-2 there is no model, so the verdict is always `None` and no finding can be
`VALUE_CONFLICT`. The argument is a plain `bool | None` so the rule is testable now; chunk 4b
replaces it with the validated verdict.
"""

from grc_sidecar.contradictions.models import CandidateReason, FindingStatus, Flag, Pair


def ceiling_allows_conflict(pair: Pair) -> bool:
    return pair.a.number.kind == pair.b.number.kind and pair.overlap == "strong"


def decide(
    pair: Pair, *, keeps_conflict: bool | None
) -> tuple[FindingStatus, tuple[CandidateReason, ...], tuple[Flag, ...]]:
    """`keeps_conflict`: None = no valid model verdict, True = the verdict keeps the conflict,
    False = the verdict lowered it."""
    reasons: list[CandidateReason] = []
    if pair.a.number.kind != pair.b.number.kind:
        reasons.append("days_vs_months")
    if pair.overlap != "strong":
        reasons.append("weak_subject_overlap")
    flags: tuple[Flag, ...] = ()
    if keeps_conflict is None:
        reasons.append("no_model_verdict")
        flags = ("model_not_consulted",)
    elif not keeps_conflict:
        reasons.append("model_lowered")
    if ceiling_allows_conflict(pair) and keeps_conflict is True:
        return "VALUE_CONFLICT", (), ()
    return "CANDIDATE", tuple(reasons), flags
