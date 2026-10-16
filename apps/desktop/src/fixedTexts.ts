// The fixed statements of the 6g screens, each defined ONCE so a component and its test use the same
// string. The words are copied from docs/DESIGN.md 4.1 and 7 (and M6_PLAN C.2 to C.4); they are
// never built from sidecar or file text. A statement here is always rendered in body text, never in
// a tooltip, a collapsed section or an attribute (DESIGN.md 1 rule 2).
//
// Words NOT written here on purpose: the review queue's refusal explanation and the approval line
// (DESIGN.md 4.1, set in chunk 6h).
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

/**
 * The review queue is chunk 6h. This is the reason shown on the disabled "Open in review queue"
 * action in 6g; 6h replaces it.
 */
export const REVIEW_QUEUE_PLACEHOLDER = "The review queue is not built yet (chunk 6h).";
