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

**Schema precedence (downgrade only).** `coverage == "none"`, `no_evidence == true` and `source_id == null` each mean "no evidence". Any inconsistent combination (for example `no_evidence` true with a quote) is treated as no evidence.

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

## Mutation proofs (M5e)

Recorded in M5e (verifier and status) and M5f (service). Procedure as in ADR-009 and ADR-011.
