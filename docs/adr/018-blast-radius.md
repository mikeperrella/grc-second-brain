# ADR-018: Blast radius, a preview of what an edit to a policy would do to the promises that rest on it

Status: Accepted (M5.5 PR 2). Proposed 2026-10-14 and built in chunk 2f (`64b6a71`).

## Context

The promise ledger (ADR-017) keeps the exact lines each promise rested on, and recall says whether those lines are still what they were. Recall answers "what has already changed". The owner also needs the question before the edit: "if I save this new text for this policy, which promises would stop resting on what they rested on?" That question has the same answer machinery (the pure `check_promise`, ADR-017 decision 17) but a different input: text that is not on disk yet.

**Scope, stated plainly.** Blast radius in 2f covers **promises only**. It does **not** report controls (the ControlMap) and it does **not** report which documents link to the file (backlinks). Neither is built. `M5_5_PLAN.md` Section 1.3 describes them as intended output; they are not part of this ADR, this route or its tests, and nothing in the product may describe blast radius as covering them until a later chunk builds them and amends this ADR.

## Decision

**1. One route: `POST /ledger/blast-radius`.** It sits behind the same authentication middleware as every route (the token is needed, 401 with no detail otherwise). The request holds no URL, token, credential or host field, and unknown fields are refused (`extra="forbid"`) with a 422 that names the field and never echoes its value. It is a POST because the proposed text is too large for a URL, not because it changes anything: it changes nothing.

**2. Exactly one of two inputs.** `proposed_text` (the whole new content of the file) or `delete_file=true`. Both or neither is a typed 422 `invalid_request` naming the field `body` and echoing nothing. Delete mode runs `check_promise` with the reason `not_found` as the proposed side, so every live promise on that path shows a proposed state of `missing` with detail `not_found`. It previews a file that is gone; it does not model a rename.

**3. The path is a vault-relative POSIX path, checked by shape.** An empty path, a leading `/`, a drive letter, any backslash, a `..` segment or a NUL is a 422 `invalid_request` (field `body.path`, value not echoed). The path is also at most 1,000 characters (a bound on the request). The path is used only to find promises and is never opened as given: the current file is read through the shared vault read function with the **stored** `source_path` of the promise, which applies the path guard (`resolve_within`, symlink and junction refusal).

**3a. Promises are matched to the request path with `casefold` on both sides.** A request path that differs only in letter case from the stored path still finds the promise. The current side reads the stored spelling, so it is the file the promise actually cites. When no live promise matches, the report says so in a fixed line, `no live promise cites this path as written`, with every count zero and no items. A voided-only path reports the same line.

**4. The proposed text goes through the same decode and normalization as an indexed file.** It is encoded to UTF-8 (a text that cannot be encoded is `proposed_text_not_utf8`), refused if over the vault's file size cap (2 MiB, `MAX_FILE_BYTES`) with `proposed_text_too_large` and the two counts (bytes and cap), then passed through `decode_and_normalize`, which refuses a NUL (`proposed_text_has_nul`) and applies the BOM and line-ending rules. What is compared is what the indexer would see. An over-cap text is refused whole, never partly checked. A Pydantic `max_length` of the same number on the field is a first bound only (characters are never more than bytes); the byte cap is the real limit.

**5. Two checks per promise, on the same inputs.** For every live (not voided) promise on the path, `check_promise` runs once against the CURRENT vault file (read once per distinct stored path, through `read_vault_text`; a file that does not exist is its reason, so the result is `missing`) and once against the proposed text, with the same `today` from the injected `Clock`. Each item reports the state now, the state if the proposed text were saved, `would_change` (any difference in state), the proposed result's detail code, every location for `moved`, the flags, and the fixed statement and scope of recall. Counts: promises checked, how many would change, and promises by proposed state. The report and every item carry the fixed line `this is a preview; nothing was saved or recorded`.

**6. It reads the vault, never the index, and never calls a model.** The index can be stale and `LedgerService` has no handle to it. No model is involved, so every result is reproducible and testable offline.

**7. The proposed text is never kept.** It is never persisted, never written to any file (not the vault, not the ledger, not a temporary file), never appended as an event, never logged at any level, never echoed in a response or an error, and never stored beyond the request. Responses hold ids, fixed codes, states, line numbers and counts only. A preview appends nothing, so asking again, or asking many times, does not grow the ledger.

**8. Bounded.** At most 2,000 live promises on one path per call (an **unmeasured starting value**, the same number as recall's); above it the whole call is refused with `blast_radius_refused` / `too_many_promises` and the two counts, never a partial list that looks complete.

**9. Configuration.** With no ledger folder: 503 `ledger_root_not_configured`. With no vault folder: 503 `vault_root_not_configured`, as recall.

**10. Amendment by addition of ADR-017 decision 19.** Decision 19 says recall reads the vault, not the index, and that "blast radius supplies proposed text to the same function instead of a vault read". That sentence is refined, not reversed: the pure `check_promise` is shared and is the only place a state is decided; blast radius **also** reads the vault (for the "now" side, so each item can say whether the state would change) and supplies the proposed text for the second call. ADR-017 is not edited.

## Consequences

Positive: the owner can see, before saving an edit or deleting a policy, which promises it would flag and to what state, with every new location of a moved quote; the preview cannot change the ledger, the vault or any file; the answer is the recall machinery's, so a preview and a later recall of the saved text agree.

Negative or risky, stated as limits:

- **Promises only.** Controls and backlinks are **not built**. A policy edit can matter to the ControlMap or to documents that link to the file; this route says nothing about either.
- **No request-body size limit exists in the sidecar.** An oversize authenticated body is buffered and parsed before it is refused (Pydantic `max_length`, then the byte cap). The authentication middleware checks the header only and does not read the body; the server configuration sets no body limit. Accepted for now: the caller is the authenticated owner's own UI on loopback. To be handled at the M6 proxy; a backlog row is to be added at the PR 2 close. This is a reading of the code, not a measured memory figure.
- **The cap of 2,000 promises is an unmeasured starting value.** A refusal because of it is reported, not worked around.
- **`intact` still means only that the cited lines are unchanged.** The preview inherits recall's scope (decision 18): a change elsewhere in the file is a flag at most, and a different paragraph that would contradict the cited lines does not change a state. Finding contradictions is the separate PR 4 detector.
- **A moved quote says nothing about the text around it** (the limit recorded for recall).
- **`would_change` counts a change of state only.** A change elsewhere in the file shows as the proposed flag `document_changed_elsewhere` while `would_change` stays false.
- **The flags shown are the proposed ones only,** so the owner cannot tell whether a flag is new. Fix when the M6 screen is built.
- **Case-insensitive matching can pair two stored paths that are different files** on a case-sensitive file system. Each stored spelling is read and judged against its own file, so no result is wrong, but one request can list promises for both.
- **Delete mode is a missing file only.** It does not model a rename or a move of the file.
- **Starting values are unmeasured:** the 2,000-promise cap, the 1,000-character path bound and the 2 MiB text cap (the vault's own cap).
- **The preview is a snapshot.** The vault may change between the preview and the edit; the preview says what the proposed text would do against the vault as it was read for this call.

## Built in chunk 2f

`POST /ledger/blast-radius` in `api/ledger.py`; `LedgerService.blast_radius`, `BlastItem`, `BlastReport`, `PREVIEW_LINE` and `NO_MATCH_LINE` in `ledger/service.py`; `BlastRadiusRefusedError` in `errors.py` and its one handler (422 `blast_radius_refused`, fixed code, integer counts) in `api/errors.py`. The repository gained nothing, no dependency was added, and no existing test changed. Tests: `sidecar/tests/test_ledger_blast_radius.py` (43 tests). The threat-model rows for it, F32-1 to F32-5, were written at the PR 2 close.

### Mutation proofs (chunk 2f)

Original sha256 (after `ruff format`): `ledger/service.py` `dee665df4c3f3565539165baeeb9807c881ad6b97dee403220a0f51e019eed38`, `api/ledger.py` `4755a7c36d662bad9ad3d530bdfa9f604484eeca5f8753945651062e6d76a4ff`. Each break was made and undone with the edit tool; after each restore the file's sha256 was identical to its original. Counts are for `test_ledger_blast_radius.py` (43 tests).

| # | Control | Break | Tests that fail | Result |
|---|---|---|---|---|
| 2f-0 | Exactly one of text or delete | the exactly-one check disabled (`api/ledger.py`) | the three `test_exactly_one_of_text_or_delete_is_required` cases, `test_the_proposed_text_is_in_no_log_no_body_and_not_in_the_ledger` | 4 failed, 39 passed |
| 2f-0b | Paths match ignoring letter case | exact comparison instead of `casefold` (`service.py`) | `test_the_path_matches_ignoring_letter_case` | 1 failed, 42 passed |
| 2f-1 | Path shape | the path checks disabled (`api/ledger.py`) | the nine path cases of `test_a_path_that_is_not_vault_relative_posix_is_a_422`, `test_a_refused_path_is_never_echoed`, the canary test | 11 failed, 32 passed |
| 2f-2 | Byte cap | the size check in `_normalize_proposed` disabled | `test_text_over_the_byte_cap_is_refused_with_counts_only` | 1 failed, 42 passed |
| 2f-3 | A preview records nothing | an event appended for every promise whose state would change | `test_a_preview_writes_and_records_nothing` | 1 failed, 42 passed |
| 2f-4 | The proposed text is never written | the proposed text written to a file in the vault folder | `test_a_preview_writes_and_records_nothing` (the tree snapshot) | 1 failed, 42 passed |
| 2f-5 | The proposed text is never echoed | the proposed text used as the refusal code of an error | `test_a_nul_in_the_text_is_refused`, the canary test | 2 failed, 41 passed |
| 2f-6 | The two results are compared | `would_change` always false (only the proposed side is reported, "no change" always) | `test_intact_to_changed_when_the_quote_would_be_gone`, `test_changed_to_intact_when_the_proposed_text_restores_the_sentence`, `test_a_file_that_is_gone_now_would_be_intact_if_the_text_came_back`, `test_a_sentence_added_around_the_quote_is_changed_never_intact`, `test_delete_mode_shows_every_live_promise_as_missing_not_found` | 5 failed, 38 passed |
| 2f-7 | Voided promises are left out | the voided filter removed | `test_a_voided_promise_is_left_out`, `test_a_voided_only_path_also_says_no_live_promise` | 2 failed, 41 passed |
| 2f-8 | The vault is read now | the file texts read on the first call are reused (a stale copy, the index's failure mode) | `test_it_reads_the_vault_now_not_a_copy_from_the_last_call`, `test_a_promise_for_another_file_is_not_listed` | 2 failed, 41 passed |

**Why there is no literal "read the index" mutation.** `LedgerService` has no handle to the index at all (no index folder and no index repository): the only file read in `blast_radius` is `read_vault_text`. Writing an index read would add a dependency in the wrong layer, which is a design change and not a break of this control. The nearest break that can be written is the index's actual failure mode, reusing a copy read earlier, which is 2f-8.

Limits of the proofs: 2f-2 disables only the service's own size check; `decode_and_normalize` has a second cap behind it, so with the first check gone an over-cap text raises an unhandled error instead of a typed refusal, and the test fails on that (a 500 is not the typed 422). 2f-4 is caught by one test, the tree snapshot, which is the control for "no file written anywhere". 2f-5 is expressed by passing the text where the fixed code goes, which the handler then returns; a different echo (in a log line, say) is covered by the canary test's log check, not by a separate break. The route-level body-size behaviour in the limits above was read from the code, not measured.
