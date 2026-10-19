// The wording of the ledger and recall screens (chunk 65f) that is NOT in docs/DESIGN.md section 11.
//
// EVERY constant in this file is a DRAFT for the owner's approval. The sentences of DESIGN.md 11.1
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
/** `export_refused`, `check_refused`, `import_refused`, `blast_radius_refused`, `graph_refused`. */
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
