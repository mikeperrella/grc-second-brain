// The fixed statements of the 6g screens, each defined ONCE so a component and its test use the same
// string. The words are copied from docs/DESIGN.md 4.1 and 7 (and M6_PLAN C.2 to C.4); they are
// never built from sidecar or file text. A statement here is always rendered in body text, never in
// a tooltip, a collapsed section or an attribute (DESIGN.md 1 rule 2).
//
// The review queue's statements (chunk 6h) are at the end of this file.
//
// The backticks around `run_record_not_kept` in DESIGN.md 4.1 are Markdown code marks; the window
// shows the same words as one plain text node.

/** Folders screen, after a successful choice (DESIGN.md 4.1). */
export const SAVED_STATEMENT = "Saved. Takes effect the next time you start ALIBI.";

/** Folders screen, next to the ledger row (DESIGN.md 4.1). */
export const LEDGER_FOLDER_WARNING =
  "Set the ledger folder before your first questionnaire run if you want to use the review queue. A run without it keeps no run record, and its draft cannot be opened in the queue.";

/** Before Start, when no ledger folder is in use (DESIGN.md 4.1). */
export const NO_LEDGER_BEFORE_START =
  "No ledger folder is in use. This run will keep no run record (run_record_not_kept) and its draft cannot be opened in the review queue. Set the ledger folder and restart ALIBI first, or run anyway.";

/** During a run (DESIGN.md 4.1). */
export const RUN_SPEED_LINE =
  "each answer can take 40 to 60 seconds on a CPU-only laptop (measured on the owner's machine)";

/** After a run whose summary carries `run_record_not_kept` or `run_record_failed` (DESIGN.md 4.1). */
export const NO_RECORD_AFTER_RUN =
  "No run record was kept: this draft cannot be opened in the review queue";

/** `404 run_not_found` on the questionnaire status route (DESIGN.md 4.1). */
export const UNKNOWN_RUN =
  "this run is no longer known (only the last 20 are kept and none survive closing ALIBI); the file is in the exports folder";

/** `404` on the index status route (M6_PLAN C.3). */
export const UNKNOWN_INDEX_JOB = "this index job is no longer known";

/** The unreviewed-draft badge (DESIGN.md 4.2). */
export const UNREVIEWED_DRAFT_BADGE = "UNREVIEWED DRAFT";

/** Loading an inspect (DESIGN.md 7 "Loading"). */
export const READING_ONLY = "reading only, the file is not changed";

/** After the last answer while the status is still `running` (DESIGN.md 4.5). */
export const WRITING_FILE = "writing the answers file";

/** `409 run_in_progress` (DESIGN.md 7 "Busy"). Shown once; ALIBI never retries by itself. */
export const BUSY_STATEMENT = "another job is running";

/** Empty questionnaire folder (DESIGN.md 7 "Empty"). */
export const NO_WORKBOOKS = "no .xlsx or .xlsm files in the folder";

// ----- the review queue (chunk 6h; docs/adr/021 decisions 4, 5 and 7, DESIGN.md 4.1, 4.3, 4.4) ---

/** On every SUPPORTED and PARTIAL row, always visible (ADR-021 decision 7). */
export const REVIEW_SENTENCE =
  "The quote is real company text that was in the context. ALIBI has not checked that it answers this question.";

/** A row whose Status cell was changed after drafting (ADR-021 decision 4). */
export const EDITED_STATUS_LINE =
  "The Status cell in the workbook was changed after drafting. The code's verdict is shown, not the cell.";

/** The label beside the record's status on an edited-status row (ADR-021 decision 4). */
export const RECORDED_BY_ALIBI = "recorded by ALIBI";

/** A row whose Answer cell was changed after drafting (ADR-021 decision 4). */
export const EDITED_ANSWER_LINE =
  "The Answer cell was changed after drafting. The citation belongs to the original answer.";

/** A row whose Evidence cell was changed: DESIGN.md 4.4 words only ("the citation is ALIBI's record, not the cell"). */
export const EDITED_EVIDENCE_LINE =
  "The Evidence cell in the workbook was changed after drafting. The citation shown is ALIBI's record, not the cell.";

/** `draft_refused` with `question_mismatch` (ADR-021 decision 5). */
export const QUESTION_MISMATCH_TEXT =
  "A question in this file was changed after ALIBI drafted it, so ALIBI cannot match the rows to its record and shows none of them. Open the original draft instead, or run the questionnaire again. (Code: question_mismatch)";

/** Always shown in the queue: the queue records no approval (ADR-021 decision 7). */
export const APPROVAL_LINE =
  'This queue is for reading. ALIBI does not record approval. To approve an answer, type "approved" in the Review column of the workbook, then import it into the ledger.';

/** `draft_refused` with `run_record_missing` (ADR-021 decision 5). */
export const RUN_RECORD_MISSING_TEXT =
  "ALIBI has no record of this draft, so it cannot show its answers with verified citations. This happens when the run was made without a ledger folder, or the file was renamed. Set the ledger folder, restart ALIBI and run again.";

/** `draft_refused` with `headers_changed` (ADR-021 decision 5). */
export const HEADERS_CHANGED_TEXT =
  "The ALIBI columns in this file were moved or renamed, so ALIBI cannot match the rows to its record.";

/** `draft_refused` with any other loader code (ADR-021 decision 5). */
export const DRAFT_UNREADABLE_TEXT = "ALIBI could not read this file as a draft.";

// ----- the sidecar's own fixed sentences (chunk 65e; ADR-022 decision 2) -----------------------
//
// These are NOT window wording. Each is the sidecar's sentence exactly as its recorded reply carries
// it (fixtures/contracts/replies/<route>.json, written by sidecar/tests/test_reply_contracts.py),
// copied from those files. ipc/proxy.ts pins each reply field with `z.literal` of the constant here,
// so a sidecar sentence that drifts makes the whole reply invalid instead of showing other words.
// A screen shows the constant, never the string from the reply.

/** Recall and blast radius `statement` (threat model F31-4b): ledger_recall.json, ledger_blast_radius.json. */
export const RECALL_STATEMENT =
  "intact means the cited lines are unchanged since drafting; it does not mean the promise is still true.";

/** Recall and blast radius `scope`: ledger_recall.json, ledger_blast_radius.json. */
export const RECALL_SCOPE =
  "Recall watches the cited lines only; a change elsewhere in the document is not a state change.";

/** Blast radius `preview`, on the report and on every item: ledger_blast_radius.json. */
export const BLAST_PREVIEW_LINE = "this is a preview; nothing was saved or recorded";

/** Blast radius `note` when no live promise matches: ledger_blast_radius.json (case `no_match`). */
export const BLAST_NO_MATCH_LINE = "no live promise cites this path as written";

/** Blast radius `coverage` (F32-4, G36): ledger_blast_radius.json. */
export const BLAST_COVERAGE = "promises only; controls and linking documents are not checked";

/** Reality check `statement`: reality_check.json. */
export const REALITY_STATEMENT = "this export, this claim, this date; not evidence the control works";

/** Reality check `staleness_note`: reality_check.json. */
export const REALITY_STALENESS_NOTE =
  "The date is the one the owner supplied. How old the export is, and whether it is the right export, is the owner's responsibility.";

/** Reality check `duplicate_note`: reality_check.json. */
export const REALITY_DUPLICATE_NOTE =
  "Every row is counted on its own. A login that appears on more than one row can be an exception on one row and not on another.";

/** Contradiction report `says` (threat model F33-9): contradictions_status.json (case `complete`). */
export const CONTRADICTIONS_NUMERIC_ONLY_STATEMENT =
  "This check compares numbers only (durations, percentages, counts, frequencies). A conflict without numbers is not found, and no finding does not mean the policies agree.";

/** Vault graph `says`: vault_graph.json. */
export const GRAPH_SAYS =
  "Lines are the explicit links written in the files. A missing line does not mean two documents are unrelated.";

// ----- the ledger and recall screens (chunk 65f; docs/DESIGN.md 11.1, approved by the owner on --
// ----- 2026-10-19). Each sentence is copied from the section 11.1 table. -----------------------

/** Ledger, always visible (DESIGN.md 11.1; ADR-017 decisions 1, 11). */
export const LEDGER_PROMISE_DEFINITION =
  "A promise is an answer you told ALIBI you sent to a customer. ALIBI never sends anything.";

/** Ledger, always visible (DESIGN.md 11.1; ADR-017 decision 9). */
export const LEDGER_IMMUTABLE_NOTE =
  "Immutable here means ALIBI has no way to change or delete a ledger row. It is not tamper-evident: another program running as you could edit the file.";

/** Void, second step (DESIGN.md 11.1; ADR-017 decision 13). */
export const VOID_STEP_STATEMENT =
  "Voiding cannot be undone. The promise stays in the ledger, marked void, and is left out of recall.";

/** Acknowledge, second step (DESIGN.md 11.1; ADR-017 addendum, G35). */
export const ACKNOWLEDGE_STATEMENT =
  "Acknowledging records that you saw this state on this date. It does not change the promise.";

/** `recall_refused` with `acknowledge_stale` (DESIGN.md 11.1; ADR-017 addendum, G35). */
export const ACKNOWLEDGE_STALE_STATEMENT = "This promise's state changed after you acknowledged it.";

/** Ledger, a promises page over the host's response cap (DESIGN.md 11.1; M6_5_PLAN N6). */
export const PROMISES_PAGE_TOO_LARGE =
  "This page of promises is too large to show. Choose a smaller page size.";

/** Recall, shown before the run action (DESIGN.md 11.1; ADR-017 decision 20). */
export const RECALL_BEFORE_RUN =
  "Checking records a change of state in the ledger when one is found. It never edits a promise.";

// ----- the import screen (chunk 65g; docs/DESIGN.md 11.1, the four "Import" rows, approved by ---
// ----- the owner on 2026-10-19). Each sentence is copied from the section 11.1 table. ---------

/** Import, always visible (DESIGN.md 11.1; ADR-017 decisions 1, 2). */
export const IMPORT_APPROVAL_LINE =
  'Approval happens in the workbook: type "approved" in the Review column, save the file with Save (not Save As), then import it here.';

/** Import, always visible (DESIGN.md 11.1; ADR-014 decision 10, ADR-021 decision 7). */
export const IMPORT_READS_ONLY = "ALIBI only reads the workbook. It never saves it.";

/** Import, second step (DESIGN.md 11.1; ADR-017 decisions 8, 10, 13). */
export const IMPORT_STEP_STATEMENT =
  "Importing creates promises that cannot be deleted, only voided. A new customer cannot be removed.";

/** Import, beside the sent date (DESIGN.md 11.1; ADR-017 decision 11). */
export const IMPORT_DATE_STATEMENT =
  "The date is your statement of when you sent these answers. ALIBI cannot check it.";

// ----- the blast radius screen (chunk 65h; docs/DESIGN.md 11.1, the three "Blast radius" rows, --
// ----- and 11.3, approved by the owner on 2026-10-19). Each sentence is copied from the file. ---
//
// The screen also shows the sidecar's own sentences above (BLAST_PREVIEW_LINE, BLAST_NO_MATCH_LINE,
// RECALL_STATEMENT, RECALL_SCOPE) from these constants. BLAST_COVERAGE stays the sidecar's pinned
// `coverage` field and is NOT shown: the screen shows BLAST_SCREEN_COVERAGE instead.

/** Blast radius, always visible above the results (DESIGN.md 11.1; threat model F32-4, G36). */
export const BLAST_SCREEN_COVERAGE =
  "Blast radius checks promises only. It does not check controls or the documents that link to this file.";

/** Blast radius, always visible while results show (DESIGN.md 11.1 and 11.3; ADR-018 addendum, `flags_now`). */
export const BLAST_NEW_FLAG_LINE = "New: this flag is not on the file as it is now.";

/** The word in front of a new flag, inside a bordered inline box (DESIGN.md 11.3). */
export const BLAST_NEW_FLAG_WORD = "New";

/** Blast radius, always visible next to the proposed-text box (DESIGN.md 11.1; ADR-018 decision 7). */
export const BLAST_PASTE_NOTE =
  "The text you paste stays in this window for this preview only. ALIBI does not save it.";
