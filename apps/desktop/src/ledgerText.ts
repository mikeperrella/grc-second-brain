// The wording of the ledger, recall and import screens (chunks 65f and 65g) that is NOT in
// docs/DESIGN.md section 11.
//
// The 65f constants and the 65g constants in this file were all approved by the owner on
// 2026-10-19 (the 65g ones are under the "65g DRAFT" comment near the end of this file; the name
// is historical). The sentences of DESIGN.md 11.1
// (approved 2026-10-19) are in fixedTexts.ts, not here. Everything below is either a plain
// functional label (a heading, a button, a field name) or one short sentence a refusal needs. A
// screen shows these constants and never text built from sidecar, file or model data. No
// sentence here states a fact about ALIBI's behaviour beyond what the control it names does.

// ----- navigation (DRAFT) -----------------------------------------------------------------------

export const NAV_GROUP_QUESTIONNAIRES = "Questionnaires";
export const NAV_GROUP_PROMISES = "Promises";
export const NAV_LABEL_FOLDERS = "Folders";
export const NAV_LABEL_INDEX = "Index the vault";
export const NAV_LABEL_QUESTIONNAIRE = "Questionnaire";
export const NAV_LABEL_REVIEW = "Review queue";
export const NAV_LABEL_LEDGER = "Ledger";
export const NAV_LABEL_RECALL = "Recall";

// ----- the ledger screen (DRAFT) -----------------------------------------------------------------

export const LEDGER_TITLE = "Ledger";
export const CUSTOMERS_HEADING = "Customers";
export const PROMISES_HEADING = "Promises";
export const READING_CUSTOMERS = "Reading the customers.";
export const READING_PROMISES = "Reading the promises.";
export const NO_CUSTOMERS = "The ledger has no customers.";
export const NO_PROMISES = "No promises match this filter.";

export const FILTER_CUSTOMER_LABEL = "Customer";
export const FILTER_ALL_CUSTOMERS = "All customers";
export const FILTER_INCLUDE_VOIDED = "Include voided promises";
export const FILTER_PAGE_SIZE_LABEL = "Page size";
export const PAGE_LABEL = "Page";
export const TOTAL_LABEL = "Promises";
export const PREVIOUS_PAGE = "Previous page";
export const NEXT_PAGE = "Next page";
export const USE_PAGE_SIZE = "Use page size";

export const FIELD_CUSTOMER = "Customer";
export const FIELD_SENT_ON = "Sent on";
export const FIELD_CREATED_ON = "Created on";
export const FIELD_QUESTION = "Question";
export const FIELD_ANSWER = "Answer";
export const FIELD_SOURCE = "Source";
export const FIELD_QUOTE = "Quote";
export const PROMISE_VOIDED_LABEL = "Voided";
export const PROMISE_EDITED_LABEL = "Edited after drafting";

export const VOID_REASON_LABEL = "Reason for voiding";
export const VOID_REASON_PLACEHOLDER = "Choose a reason";
export const VOID_BUTTON = "Void this promise";
export const VOID_CONFIRM_BUTTON = "Confirm void";
export const VOID_SENDING = "Voiding the promise.";
export const VOID_DONE = "The promise was voided.";
export const CANCEL_BUTTON = "Cancel";
export const CONFIRM_TARGET_LABEL = "Target";

/** The closed reasons of `VOID_REASONS` (proxy.ts), as labels. */
export const VOID_REASON_WRONG_CUSTOMER = "Wrong customer";
export const VOID_REASON_IMPORTED_IN_ERROR = "Imported in error";
export const VOID_REASON_NOT_ACTUALLY_SENT = "Not actually sent";
export const VOID_REASON_OTHER = "Other";

// ----- refusals (DRAFT; shown with their closed code beside them) ---------------------------------

export const PROMISE_UNKNOWN_TEXT = "That promise is not in the ledger.";
export const PROMISE_VOIDED_TEXT = "That promise is already voided.";
export const LEDGER_REFUSED_OTHER_TEXT = "The ledger refused the request.";
export const RECALL_TOO_MANY_TEXT = "There are too many promises to check at once.";
export const RECALL_TOO_MANY_PATHS_TEXT = "There are too many cited paths to list.";
/** After the approved stale sentence (fixedTexts.ts ACKNOWLEDGE_STALE_STATEMENT). */
export const ACKNOWLEDGE_STALE_NEXT_STEP = "Run the check again, then acknowledge the new state.";
/** `export_refused`, `check_refused` and `graph_refused` (no screen yet). */
export const OTHER_REFUSAL_TEXT = "The sidecar refused the request.";

// ----- the recall screen (DRAFT) -----------------------------------------------------------------

export const RECALL_TITLE = "Recall";
export const RECALL_RUN_BUTTON = "Check promises now";
export const RECALL_CUSTOMER_LABEL = "Customer (optional)";
export const RECALL_RUNNING = "Checking the promises.";
export const RECALL_NO_ITEMS = "No promises were checked.";
export const RECALL_RESULTS_HEADING = "Results";
export const RECALL_CHECKED = "Checked";
export const RECALL_EVENTS_RECORDED = "State changes recorded";

export const ITEM_PROMISE = "Promise";
export const ITEM_STATE = "State";
export const ITEM_PREVIOUS_STATE = "Previous state";
export const ITEM_DETAIL = "Detail";
export const ITEM_UNRECOGNISED_DETAIL = "unrecognised detail";
export const COUNTS_LABEL = "Counts";
export const ITEM_FLAGS = "Flags";
export const ITEM_NO_FLAGS = "none";
export const ITEM_MOVED_TO = "Now found at";
export const ITEM_LOCATIONS_NOT_LISTED = "Places not listed";
export const ITEM_RECORDED = "State change recorded";
export const ITEM_NOT_RECORDED = "No state change recorded";
export const ITEM_ACKNOWLEDGEMENT = "Acknowledgement";
export const ITEM_NOT_ACKNOWLEDGED = "Not acknowledged";
export const ITEM_ACKNOWLEDGED_AS = "Acknowledged as";
/** From ADR-022 decision 5; not in DESIGN.md section 11. */
export const ITEM_ACKNOWLEDGED_BEFORE_STATE = "Acknowledged before ALIBI recorded the state seen";
export const ITEM_ACK_CURRENT = "The acknowledgement is current";
export const ITEM_ACK_OUT_OF_DATE = "The acknowledgement is out of date";

export const ACKNOWLEDGE_BUTTON = "Acknowledge this state";
export const ACKNOWLEDGE_CONFIRM_BUTTON = "Confirm acknowledgement";
export const ACKNOWLEDGE_SENDING = "Recording the acknowledgement.";
export const ACKNOWLEDGE_DONE = "The acknowledgement was recorded. Run the check again to see it in the list.";

export const STATE_INTACT = "Intact";
export const STATE_MOVED = "Moved";
export const STATE_CHANGED = "Changed";
export const STATE_MISSING = "Missing";
export const UNKNOWN_STATE_TEXT = "Unknown state: ALIBI cannot show a label for this value.";

export const FLAG_DOCUMENT_CHANGED_ELSEWHERE = "Document changed elsewhere";
export const FLAG_REVIEW_OVERDUE = "Review overdue";
export const FLAG_FRONTMATTER_INVALID = "Front matter invalid";
export const UNKNOWN_FLAG_TEXT = "Unknown flag: ALIBI cannot show a label for this value.";

// ===== 65g DRAFT ================================================================================
// Every constant from here to the end of the file was added in chunk 65g (the Import screen). They
// were approved by the owner on 2026-10-19; the block name "65g DRAFT" is historical, and scripts
// look for it. Plain functional labels, and one short sentence per refusal or input problem that names what the code means. The import screen's statements are the four
// DESIGN.md 11.1 "Import" rows in fixedTexts.ts, not here.

export const NAV_LABEL_IMPORT = "Import";

export const IMPORT_TITLE = "Import";
export const IMPORT_CHOOSE_HEADING = "Choose a draft";
export const IMPORT_READING_FILES = "Reading the file names in the exports folder.";
export const IMPORT_FILES_CUT_SHORT = "The list was cut short; only the first names are shown.";
export const IMPORT_USE_DRAFT = "Use this draft";
export const IMPORT_SELECTED = "Selected";
export const IMPORT_READING_DRAFT = "Reading the draft";
export const IMPORT_DRAFT_FILE = "Draft";
export const IMPORT_RUN_LABEL = "Run";
export const IMPORT_QUESTIONS_LABEL = "Questions";

export const IMPORT_CUSTOMER_HEADING = "Customer";
export const IMPORT_EXISTING_CUSTOMER = "Existing customer";
export const IMPORT_NO_EXISTING_CUSTOMER = "None (new customer)";
export const IMPORT_NEW_CUSTOMER_NAME = "New customer name";
export const IMPORT_CONFIRM_NEW = "I confirm this is a new customer";
export const IMPORT_NEW_LABEL = "new";
export const IMPORT_SENT_ON_LABEL = "Sent on (YYYY-MM-DD)";

export const IMPORT_PREVIEW_BUTTON = "Preview the import";
export const IMPORT_PREVIEWING = "Previewing the import.";
export const IMPORT_BUTTON = "Import";
export const IMPORT_CONFIRM_BUTTON = "Confirm import";
export const IMPORT_SENDING = "Importing.";
export const IMPORT_NEEDS_PREVIEW = "Preview these exact choices first.";

export const IMPORT_PREVIEW_RESULT_HEADING = "Preview result";
export const IMPORT_RESULT_HEADING = "Import result";
export const IMPORT_CREATED = "Promises created";
export const IMPORT_CREATED_EDITED = "Created with an answer edited after drafting";
export const IMPORT_WOULD_CREATE = "Promises that would be created";
export const IMPORT_WOULD_CREATE_EDITED = "Of those, with an answer edited after drafting";
export const IMPORT_TO_CREATE ="Promises to create (from the preview)";
export const IMPORT_CUSTOMER_CREATED = "Customer created";
export const IMPORT_CUSTOMER_ID = "Customer id";
export const IMPORT_SKIPPED = "Skipped, by code";
export const IMPORT_REVIEW_COUNTS = "Review column values, by kind";
export const IMPORT_OTHER_VALUES = "Other Review values (text from the workbook)";
export const IMPORT_OTHER_VALUE_COUNT = "Rows";
export const IMPORT_OTHER_VALUE_CUT = "cut short by the sidecar";
export const IMPORT_ALREADY_CHANGED = "Already changed since drafting";
export const IMPORT_NOT_AVAILABLE = "not available";
export const IMPORT_YES = "yes";
export const IMPORT_NO = "no";
export const IMPORT_NONE = "none";
export const IMPORT_EXISTING_CUSTOMER_ID = "Existing customer id";

/** Input problems, found before any call. */
export const IMPORT_PROBLEM_NO_DRAFT = "Choose a draft first.";
export const IMPORT_PROBLEM_CUSTOMER_CHOICE =
  "Choose an existing customer or type a new customer name, not both.";
export const IMPORT_PROBLEM_NAME_LENGTH = "The new customer name must be 1 to 1,000 characters.";
export const IMPORT_PROBLEM_NOT_CONFIRMED = "Tick the box to confirm the new customer.";
export const IMPORT_PROBLEM_NO_SENT_ON = "Enter the sent date as YYYY-MM-DD.";
export const IMPORT_PROBLEM_SENT_ON_INVALID = "The sent date is not a real date in the form YYYY-MM-DD.";

/** `import_refused` codes (the sidecar's refusals). */
export const IMPORT_RUN_RECORD_MISSING_TEXT = "ALIBI has no run record for this draft, so it cannot import it.";
export const IMPORT_QUESTION_MISMATCH_TEXT =
  "A question in this file was changed after ALIBI drafted it, so ALIBI cannot match the rows to its record and imports none of them.";
export const IMPORT_CUSTOMER_CHOICE_INVALID_TEXT = "The sidecar needs exactly one of an existing customer and a new name.";
export const IMPORT_NOT_CONFIRMED_TEXT = "A new customer name needs the confirmation box ticked.";
export const IMPORT_SENT_ON_INVALID_TEXT = "The sent date is not a valid date.";
export const IMPORT_SENT_ON_IN_FUTURE_TEXT = "The sent date is after today.";
export const IMPORT_SENT_BEFORE_DRAFTED_TEXT = "The sent date is before the draft was made.";

/** `ledger_refused` codes an import can meet. */
export const CUSTOMER_EXISTS_TEXT =
  "A customer with this name is already in the ledger. Choose that customer instead.";
export const CUSTOMER_UNKNOWN_TEXT = "That customer is not in the ledger.";
export const CUSTOMER_NAME_INVALID_TEXT = "The ledger does not accept this customer name.";

// ===== end of 65g DRAFT =========================================================================

// ===== 65h DRAFT ================================================================================
// Every constant from here to the end of the file was added in chunk 65h (the Blast radius
// screen). They are DRAFTS until the owner approves them. Plain functional labels, and one short
// sentence per refusal or input problem that names what the code means. The screen's statements
// are the DESIGN.md 11.1 "Blast radius" rows and the sidecar's sentences in fixedTexts.ts, not here.

export const NAV_LABEL_BLAST = "Blast radius";

export const BLAST_TITLE = "Blast radius";
export const BLAST_PATH_LABEL = "Cited file";
export const BLAST_PATH_PLACEHOLDER = "Choose a file";
export const BLAST_PATH_PROMISES = "promises";
export const BLAST_READING_PATHS = "Reading the cited files.";
export const BLAST_NO_CITED_PATHS = "No live promise cites any file yet, so there is nothing to preview.";

export const BLAST_CHANGE_LEGEND = "What would change";
export const BLAST_MODE_TEXT = "Preview new text for this file";
export const BLAST_MODE_DELETE = "Preview deleting this file";
export const BLAST_TEXT_LABEL = "Proposed new text of the file";
export const BLAST_BYTES_LABEL = "Size in UTF-8 bytes";
export const BLAST_BYTES_LIMIT = "limit";

export const BLAST_PREVIEW_BUTTON = "Preview";
export const BLAST_PREVIEWING = "Previewing.";
export const BLAST_RESULT_HEADING = "Preview result";
export const BLAST_PREVIEWED_FILE = "File previewed";
export const BLAST_PREVIEWED_CHANGE = "Change previewed";
export const BLAST_CHANGE_TEXT = "new text";
export const BLAST_CHANGE_DELETE = "file deleted";
export const BLAST_CHECKED = "Checked";
export const BLAST_WOULD_CHANGE_COUNT = "Would change state";
export const BLAST_BY_PROPOSED_STATE = "Promises by proposed state";

export const BLAST_ITEM_STATE_NOW = "State now";
export const BLAST_ITEM_STATE_PROPOSED = "Proposed state";
export const BLAST_ITEM_WOULD_CHANGE = "State would change";
export const BLAST_ITEM_PROPOSED_DETAIL = "Proposed detail";
export const BLAST_ITEM_FOUND_AT = "In the proposed text, found at";
export const BLAST_ITEM_FLAGS_NOW = "Flags now";
export const BLAST_ITEM_FLAGS_PROPOSED = "Flags if this change were saved";
export const BLAST_YES = "yes";
export const BLAST_NO = "no";

/** Input problems, found before any call. */
export const BLAST_PROBLEM_NO_PATH = "Choose a cited file first.";
export const BLAST_PROBLEM_NO_MODE = "Choose new text or deleting the file.";
export const BLAST_PROBLEM_TOO_LARGE = "The proposed text is over the limit. Nothing was sent.";

/** `blast_radius_refused` codes (the sidecar's refusals). */
export const BLAST_TOO_MANY_PROMISES_TEXT = "There are too many promises on this file to preview at once.";
export const BLAST_TEXT_TOO_LARGE_TEXT = "The proposed text is larger than ALIBI accepts for one file.";
export const BLAST_TEXT_HAS_NUL_TEXT = "The proposed text contains a NUL character, which ALIBI refuses.";
export const BLAST_TEXT_NOT_UTF8_TEXT = "The proposed text cannot be encoded as UTF-8.";

// ===== end of 65h DRAFT =========================================================================
