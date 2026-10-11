# ADR-013: Answer status and grounding

Status: Accepted (M5a). Revisits CLAUDE.md 8.4 (the status bands are replaced), amends 8.3 (one shared retry budget) and Section 11 (the SUPPORTED rule). The code is built in M5e and M5f. The evidence freeze (hashes of the eval set and the vault) is filled in at M5g; the live results are filled in at M5h.

## Context

CLAUDE.md 8.4 let the model's own confidence decide the status: verified quote and confidence of at least 0.90 was `SUPPORTED`, 0.40 to 0.90 was `PARTIAL`. Two problems. First, confidence is a number the model writes, so it was one half of a compliance claim that nobody could check. Second, a verified quote proves only that some text exists in a chunk; it says nothing about whether the chunk is company policy, whether the review date has passed, whether the quote answers the question, or whether the file has changed since it was indexed. M4 made those facts available (`source_kind`, `review_overdue`, line ranges, `withheld_unindexed`, `mode`) and ADR-010 left a hand-off: "M5 must refuse `framework_reference` chunks as evidence in code" and decide "no evidence" itself, because retrieval has no relevance floor.

## Decision

**1. Model output can only lower a status.** Evidence status is computed by backend code from deterministic facts. `model_confidence`, a "no evidence" verdict and a reported conflict can move a status down. Nothing the model says can move it up. A wrong blank costs a person a minute; a wrong `SUPPORTED` goes to a customer.

**2. The model cites an opaque, per-request source ID (`S1` to `S5`), not a path.** Code maps the ID back to `source_path`, the chunk and its line range. This replaces `cited_source_path` in the 8.4 schema. The model cannot invent or smuggle a path, and an ID outside the request's context is a failed check. The output schema is:

| Field | Rule |
|---|---|
| `answer` | text, at most 1,200 characters |
| `source_id` | one of the request's IDs, or null |
| `exact_quote` | text, at most 400 characters |
| `model_confidence` | number from 0 to 1 |
| `coverage` | `full`, `partial` or `none` |
| `conflicting_source_ids` | at most 5, each one of the request's IDs |
| `no_evidence` | boolean |

**Schema precedence (downgrade only).** `coverage == "none"`, `no_evidence == true`, `source_id == null` and an empty answer (added in M5e) each mean "no evidence". Any inconsistent combination (for example `no_evidence` true with a quote) is treated as no evidence.

**3. `SUPPORTED` requires every one of these:**

1. the quote is found verbatim in the **raw text** of the chunk the cited ID maps to, with whitespace normalization only (runs of whitespace collapse to one space, the ends are stripped; no case folding, no Markdown stripping, no Unicode folding);
2. that chunk was in the context sent to the model;
3. its `source_kind` is `company_policy`;
4. its review date is current (`review_overdue` is false: retrieval computes it with the injected `Clock`, strictly `review_by < today`);
5. the model names no conflict that is one of the context's source IDs;
6. the quote meets the length floor (decision 6);
7. the cited file is unchanged when the export is written (its `file_sha256` equals the one retrieval saw);
8. `coverage == "full"` and `model_confidence >= 0.90`;
9. for an identifier-only question, the cited chunk contains a whole written form of one of the question's identifiers (decision 7).

**Withdrawn chunks need no check of their own.** They can reach the context (an identifier-only question that names a withdrawn control returns its stub, marked `withdrawn`, `retrieval/withdrawn.py:9-11`), but they come from framework packs, so they are `framework_reference` and check 3 refuses them. Framework chunks may appear in the prompt, labeled "reference only, not evidence of company practice"; they are never accepted as the cited source (`framework_only`). A non-empty retrieval result, a high fused score and `mode == "hybrid"` are never evidence (ADR-010).

**4. `PARTIAL` (new meaning).** Checks 1 to 3, 5 to 7 and 9 pass, the answer is written, and one or more of these limits applies: the review is overdue (`review_overdue`), `coverage` is `partial` (`partial_coverage`), confidence is in [0.40, 0.90) (`moderate_confidence`), retrieval ran `lexical_only` (`lexical_only`), or `withheld_unindexed` is not empty (`evidence_withheld`). The Evidence column states the limits. An overdue review caps at `PARTIAL`, not `NEEDS_REVIEW`: the quote is verified company text and the reviewer sees the flag. This matches ADR-010's cap language and the lexical and withheld caps. `SUPPORTED` still needs a current review date.

**5. `NEEDS_REVIEW` leaves the answer cell blank.** Everything else is `NEEDS_REVIEW`, including every "no evidence" result and confidence below 0.40 (`low_confidence`). A blank cell carries reason codes, never a guess.

**6. Minimum quote length: at least 20 normalized characters and at least 4 whitespace-separated tokens, and the quote may not lie entirely inside a Markdown heading line.** A one-word, identifier-only or heading-only quote would pass a substring check and prove nothing: `HR-POL-004`, `MFA` and `Data Retention Policy` must all fail. "All staff must use MFA." (23 characters, 5 tokens) passes. The identifier-only and near-miss eval items measure whether the floor is wrong; changing it needs the owner's approval and an amendment here.

**7. Identifier guard.** If the question is identifier-only (`build_lexical_query(question).identifiers_only`), the cited chunk must contain a **whole** written form of at least one of the question's identifiers, otherwise the cell is blank with `identifier_not_in_source`. A substring check would accept `AC-2` inside `AC-20` and `3.1.1` inside `13.1.1`, the traps ADR-010 built for retrieval, so the match is bounded: for each written form (`LexicalQuery.identifier_forms`, from `retrieval/identifiers.py`), a regex with `IGNORECASE` that rejects a preceding letter, digit, dot or hyphen, accepts enhancement children such as `AC-2(1)` (the ADR-010 child rule), accepts a sentence-final dot, and rejects a following digit, letter, hyphen or `.` plus alphanumeric. Case-insensitive matching is required, not optional: `identify()` upper-cases families for NIST 800-53, CSF and SOC 2, but returns generic hyphenated identifiers as typed (`HR-POL-005`). This stops the M4 live example (the question `3.1.1` retrieving `encryption-policy.md`) from becoming an answer before G7 (`control_mappings` search, owner M5.5) exists.

**8. Exact lines.** Offsets of the normalized quote map back to offsets in the raw chunk text; the line is the chunk's `start_line` plus the newlines before the match. The Evidence column prints `vault/relative/path.md:L12-L13`, never an absolute path.

**9. Several reasons at once.** The result carries every reason that applies, in a fixed precedence order (`reasons`; `reason` is the first). The Reason column joins them with `; `. An overdue source retrieved while a file is withheld records both.

**10. `withheld_unindexed` and `mode`.** `withheld_unindexed` lists every document whose file changed after indexing, **vault-wide**, not only those relevant to the question (`retrieval/service.py:217-231`). A verified answer is capped at `PARTIAL` with `evidence_withheld`; a "no evidence" result becomes blank with `evidence_withheld`, because absence cannot be asserted when a file is withheld. `mode == "lexical_only"` caps a verified answer at `PARTIAL` (`lexical_only`); a no-evidence result stays `no_evidence` with a note. A run refuses to start if any file is withheld (a rule of the run orchestration, written up in ADR-014 when it is built in M5i to M5k), so within a run this only occurs when a file is edited mid-run.

**11. Ollama fails partway through a run.** The remaining questions are blank with `model_unavailable`, the draft is still written, and the run summary says `complete: false`.

**12. Retries (CLAUDE.md 8.3).** One shared budget of at most 2 attempts per question, covering schema failures and grounding failures together. Attempt 2 sends the same two messages (ADR-012, decision 2) with the user message rebuilt, the exact error text inside its data block (pydantic's documentation URL removed, length capped). After the second failure the status is `NEEDS_REVIEW`. The error text never reaches a log above DEBUG.

**13. Contradictions.** The detector is M5.5. In M5, "no known contradiction" means one thing: the model did not name a conflict that is one of the context's source IDs. A named, verified conflict lowers the status to `NEEDS_REVIEW` (`possible_conflict`). A conflict ID that is not in the context is ignored. The 90 versus 365 day log-retention conflict in the demo vault may therefore be answered by the model with one number until M5.5; the eval labels it blank, and a failure is recorded as the evidence M5.5's detector needs (decision 15).

**14. Review state is separate from evidence status.** Every export row carries `review_state = unreviewed`. Nothing in M5 is approved. M5.5 and M6 add approval as a second field, so statuses are not renamed. The output file's column headers carry `[UNREVIEWED DRAFT]`, and so does its file name.

**15. Honest blanks depend on the model, not on code (pre-registered).** The deterministic checks prove that a quote is real, current company text that was in the context. **They cannot prove that it answers the question.** Every blank-labeled category (absent topic, near miss, conflict, identifier-only, framework bait) therefore depends on the model choosing "no evidence". The code guards are partial: identifier-only has the identifier guard; framework bait has only the framework refusal, which stops the model citing framework text but not citing an unrelated company sentence for the same question; absent topic, near miss and conflict have no guard. Pre-registered before any run:

- A blank-labeled item in the **dev** split that is answered at live check A is reported, and becomes a tuning candidate for M5m. It is **not** a design change unless a deterministic check misfired (a check that should have refused and did not, or one that refused wrongly).
- Results on the **held-out** split at live check B are final and are reported as counts ("3 of 4"), never percentages. With about 18 held-out items every number is a small-sample observation, not a rate.
- A conflict item that fails is recorded as the evidence for M5.5's detector. It stays labeled blank, because blank is the truth.
- At M5n the product brief's "Honest blanks" wording is made to match the measured held-out result, not the other way round.
- The overdue vendor policy item is labeled **PARTIAL with reason `review_overdue`** (the answer is written), not blank.

**16. Evaluation set and freeze.** The labeled set is committed in M5b, before any prompt code exists, and reviewed by the owner at STOP 1. Fixes from that review are applied in M5g, and the sha256 of both splits and of every vault file are recorded in "Evidence freeze" below. That is the freeze. After the first live run, any label edit or prompt change needs an entry in the tuning log below and the owner's approval. The acceptance criterion from the product brief: every item labeled blank ends blank. Everything else is reported as measured counts with the command and its full output; there are no target numbers and no estimates.

## Consequences

Positive: a status is a function of checkable facts, so it can be tested and mutation-checked; the model cannot raise it; the export says what it is; the identifier trap that M4's live check found cannot become an answer; every limit is written in the Reason column.

Negative or risky:

- **The deterministic checks do not make an answer correct.** A true quote from a real policy that does not answer the question passes checks 1 to 9. The control is human approval (M6), which does not exist yet; until then every export is an internal, unreviewed draft.
- `PARTIAL` now covers several different limits. A reader needs the Reason column to tell them apart.
- A vault-wide `withheld_unindexed` means one unrelated edit during a run caps every later answer at `PARTIAL`. That is conservative on purpose and rare.
- The minimum quote length is a judgement call; a genuinely short policy sentence below 20 characters is blanked.
- Raising the bar for `SUPPORTED` will lower the count of `SUPPORTED` answers compared with the old confidence-only rule. That is the point, and it is reported as measured counts only.

## Evidence freeze (M5g)

Filled in at M5g: sha256 of the dev split, the held-out split and every vault file, with the command that produced them.

## Tuning log

Empty. Any label edit or prompt change after the first live run is recorded here with the reason and the owner's approval.

## What M5e built, and four things it settled

Code: `questionnaire/grounding.py` (the quote checks), `questionnaire/status.py` (`judge`, `finalize_rejection`, `lower_for_changed_source`), `questionnaire/models.py` (`AnswerResult`, `Rejection`, `Citation`, the reason codes and their fixed order), and `file_sha256` on `RetrievedChunk` and `DocInfo`. Tests: `test_grounding.py`, `test_status.py`, and two tests in `test_retrieval_golden.py`.

1. **The reason order is fixed, and `evidence_withheld` comes before `no_evidence`.** Decision 9 says every reason that applies is recorded; decision 10 says a withheld file makes absence impossible to assert. With one global order, the second only holds if `evidence_withheld` precedes `no_evidence`, so it does: a no-evidence result with a file withheld reads `evidence_withheld; no_evidence`, and the first reason is the main one. The order is `REASON_ORDER` in `models.py`; blocking reasons come before the limits that only cap an answer at PARTIAL. An early version of the code had them the other way round and contradicted this ADR; a test caught it.
2. **An empty answer counts as no evidence** (an addition to the schema-precedence list in decision 2). A verified quote with no answer text has nothing to write in the cell, so it is treated like `no_evidence` rather than as a `SUPPORTED` answer with a blank.
3. **A `NEEDS_REVIEW` result may keep its citation.** When the quote was verified but the result is blank for `low_confidence` or `possible_conflict`, the citation (path, exact lines, quote) is kept for the reviewer; the answer cell is still blank. A result with no verified quote has no citation.
4. **The retry message never repeats what the model wrote.** A rejection names the failed check and the source id ("exact_quote does not appear word for word in S2") and nothing from the reply, so a hostile reply cannot steer the next attempt through the error text. (Schema errors from the strict parse do quote the offending value; those travel inside a data block, ADR-012.)

`file_sha256` was added to `RetrievedChunk` as approved at the plan review. Proof that no existing field changed: before the change, the complete output of 13 golden retrieval queries (every field of every result and chunk, sorted-key JSON) was hashed and pinned in `test_m4_golden_results_identical_with_file_sha256`; after the change, the same test, which removes only the new `file_sha256` key before hashing, passed unchanged. Rows 19 and 20 below show each side can fail on its own.

## Mutation proofs (M5e)

Procedure as in ADR-009 and ADR-011 (back up and record sha256, apply one named break, run the named tests, require "N failed", restore with the inverse edit, show the sha256 equals the original, show the tests pass again). Originals: `questionnaire/grounding.py` = `533809f2983693ef6b18d1208e8384b8487b14be4c40b5a6bc7f11ec9921a533`, `questionnaire/status.py` = `3c9d0a81a19b3157d8130165539560bece3cb5456fbe9a39b85849bec87e4049`, `questionnaire/models.py` = `55b1b8fe8f384dfa5533c6decc275f32fbfa5f7ae59feccafecf687088fd98ef`, `retrieval/service.py` = `ed79ed30ebedea4cf8c6396f1cf68e5db320f17d82d67e83bf134a3b9cb40600` (taken after the one added line `file_sha256=document.file_sha256`, which is the whole difference from the committed M4 file). Every restore matched.

| # | Control | Break | Tests that failed | Result |
|---|---|---|---|---|
| 1 | The quote match is case-sensitive | `casefold()` on both sides of the `find` | `test_grounding.py::test_anything_beyond_whitespace_is_not_verbatim` (the two case variants) | 2 failed, 155 passed |
| 2 | Normalization changes whitespace and nothing else | `normalize()` also folds case | 21 tests across `test_grounding.py` and `test_status.py` (normalization cases, Markdown kept, the offset map, the labels of M5b, framework, short-quote and identifier cases, the property test) | 21 failed, 136 passed |
| 3 | A citation names exact lines | the end line is one too high | 15 tests: every multi-line and line-offset case in `test_grounding.py`, `::test_every_label_phrase_is_located_on_its_labeled_lines`, `test_status.py::test_the_citation_names_exact_lines_and_never_an_absolute_path` | 15 failed, 142 passed |
| 4a | Minimum quote length: characters | `MIN_QUOTE_CHARS` 20 changed to 1 | `::test_the_minimum_quote_length` (the 18, 19 and collapsed-spacing cases), `::test_the_thresholds_are_the_documented_ones`, `test_status.py::test_every_failed_check_is_recorded_not_just_the_first` | 5 failed, 152 passed |
| 4b | Minimum quote length: words | `MIN_QUOTE_TOKENS` 4 changed to 1 | the unit cases (21 characters in 3 words, 20 characters in 1 word, 22 in 3). This first run found a gap: no test at the decision level isolated the word rule, because every short quote there was also short in characters. `test_status.py::test_a_long_quote_of_too_few_words_is_rejected` was added; with the same break it fails | 4 failed, then the new test: 1 failed |
| 5 | A heading-only quote is refused | heading detection forced to false | `test_grounding.py::test_a_heading_only_quote_is_flagged`, `::test_only_atx_heading_lines_count_as_headings`, `test_status.py::test_a_heading_only_quote_is_rejected_even_if_it_is_long` | 3 failed, 155 passed |
| 6 | An identifier must be whole | the pattern replaced by a plain substring test | the 15 trap and glue cases (`AC-20`, `CC6.10`, `A.5.152`, `3.1.10`, `13.1.1`, a letter, digit, dot, hyphen or underscore around it, a continuing dot) and `test_status.py::test_a_longer_identifier_in_the_chunk_is_not_the_identifier` | 16 failed, 142 passed |
| 7 | The identifier match ignores case | `re.IGNORECASE` removed | `::test_a_whole_identifier_is_found` (3 case variants). The first run found a second gap: `ac-2` still passed at the decision level, because `identify()` upper-cases NIST identifiers before they get here. `test_status.py::test_a_generic_identifier_typed_in_lower_case_matches_the_text_in_upper_case` (`hr-pol-005`, which `identify()` returns as typed) was added; with the same break it fails | 3 failed, then the new test: 1 failed |
| 8 | The cited chunk must be in the context | an unknown id falls back to the first source | `test_status.py::test_a_cited_source_that_was_not_in_the_context_is_rejected` (the quote is verbatim in a chunk the model was never shown, so only this check can refuse it) | 1 failed, 67 passed |
| 9 | Framework text is refused as evidence (including a withdrawn stub) | the kind check disabled | `::test_a_framework_quote_is_never_supported_even_when_everything_else_is_perfect` (verbatim, in context, current, confidence 1.0), `::test_a_withdrawn_framework_stub_is_refused_the_same_way`, `::test_every_failed_check_is_recorded_not_just_the_first`, the property test | 4 failed, 64 passed |
| 10 | An overdue review caps at PARTIAL | the overdue reason never added | `::test_an_overdue_review_caps_at_partial_and_the_answer_is_written`, `::test_every_limit_that_applies_is_recorded_in_the_fixed_order`, `::test_a_conflict_and_a_cap_are_both_recorded`, `::test_the_earlier_reasons_survive_the_export_check`, the property test | 5 failed, 63 passed |
| 11 | Model confidence cannot upgrade a status | `SUPPORTED` decided by confidence alone | the partial-coverage, overdue, keyword-only and withheld cases, and the property test (confidence 1.0 in each) | 5 failed, 63 passed |
| 12 | A `NEEDS_REVIEW` cell is blank | the answer text kept on a blocked result | `::test_low_confidence_leaves_the_cell_blank_but_keeps_the_verified_evidence`, `::test_a_conflict_the_model_names_blanks_the_cell`, `::test_a_needs_review_result_never_carries_an_answer` | 3 failed, 65 passed |
| 13 | A named conflict blanks the cell | the conflict reason never added | `::test_a_conflict_the_model_names_blanks_the_cell`, `::test_a_conflict_and_a_cap_are_both_recorded`, `::test_a_needs_review_result_never_carries_an_answer` | 3 failed, 65 passed |
| 14 | Keyword-only retrieval and withheld files are limits | `context_limits` returns nothing | the two cap tests, the all-limits order test, the conflict-plus-cap test, both no-evidence annotation tests, the retry-exhausted test, the property test | 8 failed, 60 passed |
| 15 | Every failed check is recorded | a rejection keeps only its first reason | `::test_every_failed_check_is_recorded_not_just_the_first` | 1 failed, 67 passed |
| 16 | Schema precedence | only the `no_evidence` flag counts | `::test_anything_that_says_no_evidence_is_no_evidence_and_blank` (coverage none, null source, empty and blank answers, a quote with no source, coverage none with a quote) and `::test_a_needs_review_result_never_carries_an_answer` | 7 failed, 61 passed |
| 17 | A changed cited file blanks the answer at export | the check always returns the result unchanged | `::test_a_changed_cited_file_blanks_a_supported_answer_at_export`, `::test_a_vanished_cited_file_blanks_the_answer_too`, `::test_the_earlier_reasons_survive_the_export_check` | 3 failed, 65 passed |
| 18 | `evidence_withheld` leads `no_evidence` | the two swapped in `REASON_ORDER` | `::test_no_evidence_with_a_withheld_file_leads_with_evidence_withheld`, `::test_the_reason_order_covers_every_reason_exactly_once` | 2 failed, 66 passed |
| 19 | `file_sha256` carries the file's hash | filled with the chunk's own `text_sha256` | `test_retrieval_golden.py::test_every_chunk_carries_the_hash_of_the_file_it_came_from` (the golden snapshot still passes, as it should: it excludes this field) | 1 failed, 1 passed |
| 20 | No existing retrieval field changed | `heading_path` upper-cased in `_chunk_of` | `test_retrieval_golden.py::test_m4_golden_results_identical_with_file_sha256` (the hash test still passes) | 1 failed, 1 passed |

Notes and known limits:

- **The property test** (`test_model_output_can_only_lower_a_status_never_raise_it`) runs 32 combinations of facts (company or framework, quote real or not, overdue, keyword-only, withheld) against 144 kinds of reply (6 confidences, 3 coverages, with or without a named conflict, `no_evidence` set or not, a source or null) plus the best possible reply: 4,640 replies judged in the main loop. The ceiling the facts allow is computed by a small oracle written separately from the code under test; the result must never exceed it, and the best possible reply must reach it exactly. It is a check of the rule as written here. It does not show the rule is the right rule.
- **Two expectations of mine were wrong when first written** and were corrected, not worked around: a quote missing only its final period is a legitimate fragment (the rule is a substring match, and length is the length floor's job), and a quote of `two\n\nline` collapses to `two line`. The code was right in both. A third finding, the reason order, was a real defect in the code (point 1 above).
- **Identifier matching uses `[A-Za-z0-9_.-]` before a form and `[A-Za-z0-9_-]` after it**, an underscore added to the character classes in the plan's description (an underscore glues two words into one identifier, like a letter does).
- **The quote text kept in a citation is the model's quote after whitespace normalization**, not the raw chunk text, so a quote that spans two lines is stored on one line. The exact lines say where it is.
- Nothing here talks to a model. What fraction of real model replies pass these checks is measured at live check A.
