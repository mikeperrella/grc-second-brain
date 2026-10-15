# M5 and M5.5 carryover index (NOT CANONICAL)

Not canonical. If this disagrees with an ADR or the threat model, they win.
M5_5_PLAN.md and M6_PLAN.md are committed; M5_PLAN.md is not (it holds M5
deviations 1 to 50, locally only). This is a map, not a copy.

What M6 depends on, and where it is decided:
- Answer status is computed by code; the model can only lower it: ADR-013.
- One heavy job at a time (questionnaire run, contradiction run): ADR-014,
  M55-13. A second gets 409 run_in_progress.
- A run's state is in memory, last 20, lost when ALIBI closes; the status
  route returns completed and total while running: ADR-014 decision 16,
  M6_PLAN.md Check 1.
- The sidecar reads GRC_* settings once at startup; folders may not overlap:
  CLAUDE.md 5.1, ADR-009, ADR-017 decision 7.
- The run record (ledger folder) holds status, reason codes, citation and
  hashes, no text; a run without a ledger folder keeps none (run_record_not_kept):
  ADR-017 decisions 2, 3 and 15, M6_PLAN.md Check 4.
- Until M8 a release build cannot start the sidecar: debug build only.
- Review state is separate from status; approval is typing "approved" in the
  workbook and importing it; ALIBI never saves a reviewed workbook: ADR-017
  decisions 1 and 2, ADR-014 decision 10.
- /readiness is separate from /health: ADR-011.
- Recall (intact) and blast radius statements, flags, scope: ADR-017
  decisions 17 and 18, ADR-018. Screens are M6.5.
- The contradiction run is one JSON response; the model confirms only
  sometimes: ADR-019, M55-12. Screen is M6.5.
- Deviations: M55-1 to M55-20 in M5_5_PLAN.md; M6-1 on in M6_PLAN.md.
