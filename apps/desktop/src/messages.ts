// Plain-words messages for every closed code the 6g screens can meet (DESIGN.md 7 "Typed refusal or
// error": each code from a closed set has its own fixed message; no raw sidecar text ever reaches
// the window).
//
// Every function here is an exhaustive switch over a union built from the contract lists in
// ipc/proxy.ts and ipc/folders.ts, with no wildcard: a code added to a contract without a message
// here stops the build (assertNever) and fails messages.test.ts. Messages are fixed strings. The
// only dynamic parts are model NAMES (`models_missing`, shown as plain text by the component) and
// the closed code word itself.

import { NO_DOWNLOAD_STATEMENT } from "./readiness";
import {
  ACKNOWLEDGE_STALE_STATEMENT,
  BUSY_STATEMENT,
  DRAFT_UNREADABLE_TEXT,
  HEADERS_CHANGED_TEXT,
  QUESTION_MISMATCH_TEXT,
  RUN_RECORD_MISSING_TEXT,
  UNKNOWN_RUN,
} from "./fixedTexts";
import {
  ACKNOWLEDGE_STALE_NEXT_STEP,
  BLAST_TEXT_HAS_NUL_TEXT,
  BLAST_TEXT_NOT_UTF8_TEXT,
  BLAST_TEXT_TOO_LARGE_TEXT,
  BLAST_TOO_MANY_PROMISES_TEXT,
  CONTRA_FAILED_DETECTOR_ERROR_TEXT,
  CONTRA_FAILED_INDEX_NOT_BUILT_TEXT,
  CONTRA_FAILED_INTERNAL_ERROR_TEXT,
  CONTRA_FAILED_NO_WORD_TEXT,
  CONTRA_UNKNOWN_RUN_TEXT,
  CUSTOMER_EXISTS_TEXT,
  CUSTOMER_NAME_INVALID_TEXT,
  CUSTOMER_UNKNOWN_TEXT,
  IMPORT_CUSTOMER_CHOICE_INVALID_TEXT,
  IMPORT_NOT_CONFIRMED_TEXT,
  IMPORT_QUESTION_MISMATCH_TEXT,
  IMPORT_RUN_RECORD_MISSING_TEXT,
  IMPORT_SENT_BEFORE_DRAFTED_TEXT,
  IMPORT_SENT_ON_IN_FUTURE_TEXT,
  IMPORT_SENT_ON_INVALID_TEXT,
  LEDGER_REFUSED_OTHER_TEXT,
  OTHER_REFUSAL_TEXT,
  PROMISE_UNKNOWN_TEXT,
  PROMISE_VOIDED_TEXT,
  RECALL_TOO_MANY_PATHS_TEXT,
  RECALL_TOO_MANY_TEXT,
} from "./ledgerText";
import type { FolderErrorWord, FolderProblem } from "./ipc/folders";
import { FIXED_ERROR_WORDS } from "./ipc/proxy";
import type {
  ApiError,
  BLAST_RADIUS_REFUSAL_CODES,
  CONTRADICTION_FAILURES,
  CallResult,
  DRAFT_REFUSAL_CODES,
  IMPORT_REFUSAL_CODES,
  LEDGER_REFUSAL_CODES,
  MAPPING_REFUSAL_CODES,
  ProxyError,
  RECALL_REFUSAL_CODES,
  RouteName,
  RouteReply,
  WORKBOOK_REFUSAL_CODES,
} from "./ipc/proxy";

type FixedErrorWord = (typeof FIXED_ERROR_WORDS)[number];
type WorkbookRefusalCode = (typeof WORKBOOK_REFUSAL_CODES)[number];
type MappingRefusalCode = (typeof MAPPING_REFUSAL_CODES)[number];

function assertNever(value: never): never {
  // Reached only if a type and its switch disagree at run time.
  throw new Error(`unhandled code: ${String(value)}`);
}

/** What a screen shows for a failure: fixed words, a code, and (only for models) the names. */
export interface Described {
  /** `busy` is a 409 `run_in_progress`; `models` carries names; everything else is `error`. */
  category: "busy" | "models" | "error";
  text: string;
  /** The closed code word, shown in monospace beside the text. Always matches /^[a-z0-9_:]+$/. */
  code: string;
  /** Missing model names (plain text nodes). Empty unless `category` is `models`. */
  models: readonly string[];
  /** True when the no-download sentence must be shown with this failure. */
  showNoDownload: boolean;
}

function error(code: string, text: string): Described {
  return { category: "error", text, code, models: [], showNoDownload: false };
}

/** A code word the window may print: lowercase letters, digits, underscore. Else `null`. */
export function safeCode(value: string): string | null {
  return /^[a-z0-9_]{1,64}$/.test(value) ? value : null;
}

// ----- the host's own errors ------------------------------------------------------------------

export function proxyErrorMessage(code: ProxyError): string {
  switch (code) {
    case "not_ready":
      return "The sidecar is not ready yet. Try again when the status bar says healthy.";
    case "unauthorized":
      return "The sidecar did not accept the request. Restart ALIBI.";
    case "unreachable":
      return "ALIBI could not reach the sidecar.";
    case "timeout":
      return "The sidecar did not answer in time.";
    case "too_large":
      return "The sidecar's answer was larger than ALIBI accepts.";
    case "bad_request":
      return "The host refused to send the request. Nothing was sent.";
    case "bad_response":
      return "The sidecar's answer could not be read.";
    case "internal":
      return "The host hit an internal error.";
    default:
      return assertNever(code);
  }
}

export function folderErrorMessage(code: FolderErrorWord): string {
  switch (code) {
    case "unavailable":
      return "That folder is not available (it is missing or is not a folder).";
    case "overlap":
      return "That folder overlaps another ALIBI folder, so it was not saved.";
    case "not_configured":
      return "No folder of this kind is in use. Choose one on the Folders screen; it takes effect the next time you start ALIBI.";
    case "not_listable":
      return "ALIBI cannot list the files of this kind of folder.";
    case "save_failed":
      return "The folder could not be saved.";
    default:
      return assertNever(code);
  }
}

export function folderProblemMessage(problem: FolderProblem): string {
  switch (problem) {
    case "unavailable":
      return "the saved folder is missing or is not a folder";
    case "not_absolute":
      return "the saved folder is not a full path";
    case "overlap":
      return "the saved folder overlaps another ALIBI folder";
    default:
      return assertNever(problem);
  }
}

// ----- the sidecar's typed errors -------------------------------------------------------------

const CHOOSE_FOLDER = "Choose one on the Folders screen; it takes effect the next time you start ALIBI.";

export function fixedErrorMessage(word: FixedErrorWord): string {
  switch (word) {
    case "questionnaire_root_not_configured":
      return `No questionnaires folder is in use. ${CHOOSE_FOLDER}`;
    case "import_root_not_configured":
      return `No imports folder is in use. ${CHOOSE_FOLDER}`;
    case "export_root_not_configured":
      return `No exports folder is in use. ${CHOOSE_FOLDER}`;
    case "vault_root_not_configured":
      return `No vault folder is in use. ${CHOOSE_FOLDER}`;
    case "ledger_root_not_configured":
      return `No ledger folder is in use. ${CHOOSE_FOLDER}`;
    case "ledger_unusable":
      return "The ledger folder is in use but ALIBI cannot use it.";
    case "ledger_store_error":
      return "The ledger could not be read or written.";
    case "questionnaire_root_unusable":
      return "The questionnaires folder is in use but ALIBI cannot use it.";
    case "run_in_progress":
      return `Not started: ${BUSY_STATEMENT}. Wait for it to finish, then try again. ALIBI does not retry by itself.`;
    case "run_not_found":
      return UNKNOWN_RUN;
    case "vault_changed_reindex":
      return "Files in the vault changed after they were indexed. Index the vault again, then start the run.";
    case "index_not_built":
      return "The vault has not been indexed yet. Index the vault first.";
    case "retrieval_unavailable":
      return "Search over the vault is not available, so no answers can be drafted.";
    case "path_not_allowed":
      return "That file name is not allowed.";
    case "file_not_found":
      return "That file was not found in its folder.";
    case "control_map_too_large":
      return "The control map is too large.";
    case "auth_controls_invalid":
      return "The authorization controls file is not valid.";
    default:
      return assertNever(word);
  }
}

export function workbookRefusalMessage(code: WorkbookRefusalCode): string {
  switch (code) {
    case "file_too_large":
      return "The workbook is larger than ALIBI accepts.";
    case "uncompressed_too_large":
      return "The workbook is larger than ALIBI accepts once unpacked.";
    case "too_many_parts":
      return "The workbook has more internal parts than ALIBI accepts.";
    case "ratio_too_high":
      return "The workbook is compressed far more than normal, so ALIBI refused it.";
    case "not_a_zip":
      return "The file is not a workbook package.";
    case "legacy_or_encrypted":
      return "The file is an old-format or encrypted workbook, which ALIBI cannot read.";
    case "unsupported_type":
      return "The workbook type is not supported.";
    case "no_workbook_part":
      return "The package has no workbook inside.";
    case "unreadable":
      return "The workbook could not be read.";
    case "doctype":
      return "The workbook contains a document type declaration, which ALIBI refuses.";
    case "entity":
      return "The workbook contains an entity declaration, which ALIBI refuses.";
    case "utf32":
      return "The workbook uses a text encoding ALIBI refuses.";
    case "encoding_not_allowed":
      return "The workbook uses a text encoding ALIBI does not allow.";
    case "duplicate_part":
      return "The workbook package lists the same part twice.";
    default:
      return assertNever(code);
  }
}

export function mappingRefusalMessage(code: MappingRefusalCode): string {
  switch (code) {
    case "no_mapping":
      return "No question column was chosen.";
    case "sheet_not_found":
      return "The chosen sheet is not in the workbook.";
    case "bad_position":
      return "The chosen header row or column is outside the sheet.";
    case "sheet_too_large":
      return "The chosen sheet is larger than ALIBI accepts.";
    case "too_many_questions":
      return "The sheet has more questions than ALIBI answers in one run.";
    case "already_has_answer_columns":
      return "The sheet already has ALIBI answer columns, so it looks like a draft. Choose the original questionnaire.";
    default:
      return assertNever(code);
  }
}

type DraftRefusalCode = (typeof DRAFT_REFUSAL_CODES)[number];

/**
 * A whole-queue refusal as fixed words (ADR-021 decision 5). The three ledger words have their own
 * text; every loader code (workbook or mapping refusal) is "could not read this file as a draft".
 */
export function draftRefusedMessage(code: DraftRefusalCode): string {
  switch (code) {
    case "question_mismatch":
      return QUESTION_MISMATCH_TEXT;
    case "run_record_missing":
      return RUN_RECORD_MISSING_TEXT;
    case "headers_changed":
      return HEADERS_CHANGED_TEXT;
    case "file_too_large":
    case "uncompressed_too_large":
    case "too_many_parts":
    case "ratio_too_high":
    case "not_a_zip":
    case "legacy_or_encrypted":
    case "unsupported_type":
    case "no_workbook_part":
    case "unreadable":
    case "doctype":
    case "entity":
    case "utf32":
    case "encoding_not_allowed":
    case "duplicate_part":
    case "no_mapping":
    case "sheet_not_found":
    case "bad_position":
    case "sheet_too_large":
    case "too_many_questions":
    case "already_has_answer_columns":
      return DRAFT_UNREADABLE_TEXT;
    default:
      return assertNever(code);
  }
}

/** Turn one typed sidecar error body into fixed words. */
export function describeApiError(body: ApiError): Described {
  switch (body.error) {
    case "models_unavailable":
      return {
        category: "models",
        text: "The models ALIBI needs are not available.",
        code: "models_unavailable",
        models: body.models_missing,
        showNoDownload: true,
      };
    case "workbook_refused":
      return error(`workbook_refused:${body.code}`, workbookRefusalMessage(body.code));
    case "mapping_refused":
      return error(`mapping_refused:${body.code}`, mappingRefusalMessage(body.code));
    case "draft_refused":
      return error(`draft_refused:${body.code}`, draftRefusedMessage(body.code));
    case "invalid_request":
      return error(
        "invalid_request",
        "The sidecar did not accept the request ALIBI built. Nothing was changed.",
      );
    case "run_in_progress":
      return {
        category: "busy",
        text: fixedErrorMessage("run_in_progress"),
        code: "run_in_progress",
        models: [],
        showNoDownload: false,
      };
    default:
      // The remaining members are the closed FIXED_ERROR_WORDS.
      return error(body.error, fixedErrorMessage(body.error));
  }
}

// ----- the M6.5 refusal families the ledger and recall screens can meet (chunk 65f) ------------

type LedgerRefusalCode = (typeof LEDGER_REFUSAL_CODES)[number];
type RecallRefusalCode = (typeof RECALL_REFUSAL_CODES)[number];

/**
 * Every typed error body an M6.5 route can carry. `M65ApiError` is not exported from ipc/proxy.ts,
 * so it is read from a route's reply type: the reply union minus its 200 member.
 */
export type LedgerErrorBody = Exclude<RouteReply<"ledger_void">, { status: 200 }>["body"];

function isFixedBody(body: LedgerErrorBody): body is Extract<LedgerErrorBody, { error: FixedErrorWord }> {
  return (FIXED_ERROR_WORDS as readonly string[]).includes(body.error);
}

export function ledgerRefusalMessage(code: LedgerRefusalCode): string {
  switch (code) {
    case "promise_unknown":
      return PROMISE_UNKNOWN_TEXT;
    case "customer_name_invalid":
      return CUSTOMER_NAME_INVALID_TEXT;
    case "customer_exists":
      return CUSTOMER_EXISTS_TEXT;
    case "customer_unknown":
      return CUSTOMER_UNKNOWN_TEXT;
    case "run_exists":
    case "run_unknown":
    case "promise_exists":
    case "event_invalid":
      return LEDGER_REFUSED_OTHER_TEXT;
    default:
      return assertNever(code);
  }
}

type ImportRefusalCode = (typeof IMPORT_REFUSAL_CODES)[number];

/** Each `import_refused` code has its own words (chunk 65g). No wildcard. */
export function importRefusalMessage(code: ImportRefusalCode): string {
  switch (code) {
    case "run_record_missing":
      return IMPORT_RUN_RECORD_MISSING_TEXT;
    case "headers_changed":
      return HEADERS_CHANGED_TEXT;
    case "question_mismatch":
      return IMPORT_QUESTION_MISMATCH_TEXT;
    case "customer_choice_invalid":
      return IMPORT_CUSTOMER_CHOICE_INVALID_TEXT;
    case "new_customer_not_confirmed":
      return IMPORT_NOT_CONFIRMED_TEXT;
    case "sent_on_invalid":
      return IMPORT_SENT_ON_INVALID_TEXT;
    case "sent_on_in_future":
      return IMPORT_SENT_ON_IN_FUTURE_TEXT;
    case "sent_before_drafted":
      return IMPORT_SENT_BEFORE_DRAFTED_TEXT;
    default:
      return assertNever(code);
  }
}

export function recallRefusalMessage(code: RecallRefusalCode): string {
  switch (code) {
    case "too_many_promises":
      return RECALL_TOO_MANY_TEXT;
    case "promise_voided":
      return PROMISE_VOIDED_TEXT;
    case "acknowledge_stale":
      return `${ACKNOWLEDGE_STALE_STATEMENT} ${ACKNOWLEDGE_STALE_NEXT_STEP}`;
    case "too_many_paths":
      return RECALL_TOO_MANY_PATHS_TEXT;
    default:
      return assertNever(code);
  }
}

type BlastRadiusRefusalCode = (typeof BLAST_RADIUS_REFUSAL_CODES)[number];

/** Each `blast_radius_refused` code has its own words (chunk 65h). No wildcard. */
export function blastRadiusRefusalMessage(code: BlastRadiusRefusalCode): string {
  switch (code) {
    case "too_many_promises":
      return BLAST_TOO_MANY_PROMISES_TEXT;
    case "proposed_text_too_large":
      return BLAST_TEXT_TOO_LARGE_TEXT;
    case "proposed_text_has_nul":
      return BLAST_TEXT_HAS_NUL_TEXT;
    case "proposed_text_not_utf8":
      return BLAST_TEXT_NOT_UTF8_TEXT;
    default:
      return assertNever(code);
  }
}

/**
 * Turn one typed error body of a ledger route into fixed words. The M6 core families are handled
 * by describeApiError; the M6.5 families each have a typed case, with no catch-all label.
 */
export function describeLedgerError(body: LedgerErrorBody): Described {
  if (isFixedBody(body)) {
    return describeApiError(body);
  }
  switch (body.error) {
    case "models_unavailable":
    case "workbook_refused":
    case "mapping_refused":
    case "draft_refused":
    case "invalid_request":
      return describeApiError(body);
    case "ledger_refused":
      return error(`ledger_refused:${body.code}`, ledgerRefusalMessage(body.code));
    case "recall_refused":
      return error(`recall_refused:${body.code}`, recallRefusalMessage(body.code));
    case "import_refused":
      return error(`import_refused:${body.code}`, importRefusalMessage(body.code));
    case "blast_radius_refused":
      return error(`blast_radius_refused:${body.code}`, blastRadiusRefusalMessage(body.code));
    case "export_refused":
    case "check_refused":
    case "graph_refused":
      return error(`${body.error}:${body.code}`, OTHER_REFUSAL_TEXT);
    default:
      return assertNever(body);
  }
}

// ----- the contradictions screen (chunk 65i) ----------------------------------------------------

type ContradictionFailure = (typeof CONTRADICTION_FAILURES)[number];

/**
 * The words of a failed run. Each failure word has its own text, and a failed run with no word
 * (`null`) has its own text too: it is never blank. No wildcard.
 */
export function contradictionFailureMessage(word: ContradictionFailure | null): string {
  switch (word) {
    case "index_not_built":
      return CONTRA_FAILED_INDEX_NOT_BUILT_TEXT;
    case "detector_error":
      return CONTRA_FAILED_DETECTOR_ERROR_TEXT;
    case "internal_error":
      return CONTRA_FAILED_INTERNAL_ERROR_TEXT;
    case null:
      return CONTRA_FAILED_NO_WORD_TEXT;
    default:
      return assertNever(word);
  }
}

/** A failed run as fixed words and a code. The code of a run with no word is `failure_word_missing`. */
export function describeContradictionFailure(word: ContradictionFailure | null): Described {
  return error(word ?? "failure_word_missing", contradictionFailureMessage(word));
}

/**
 * Turn one typed error body of a contradictions route into fixed words. `run_not_found` has its
 * own text here (the questionnaire text names a number of runs this route does not keep); every
 * other body goes through describeLedgerError, so each typed refusal keeps its own message.
 */
export function describeContradictionError(body: LedgerErrorBody): Described {
  if (body.error === "run_not_found") {
    return error("run_not_found", CONTRA_UNKNOWN_RUN_TEXT);
  }
  return describeLedgerError(body);
}

/** Every call outcome that is not a sidecar reply. */
export type NonReply = Exclude<CallResult<RouteName>, { kind: "reply" }>;

export function describeNonReply(result: NonReply): Described {
  switch (result.kind) {
    case "proxy_error":
      return error(result.error, proxyErrorMessage(result.error));
    case "invalid_response":
      return error("invalid_response", "The answer did not match what ALIBI expects, so it was not used.");
    case "rejected":
      return error("rejected", "The host did not allow this request.");
    case "invalid_request":
      return error("invalid_request", "ALIBI could not build a valid request. Nothing was sent.");
    default:
      return assertNever(result);
  }
}

/** Fixed text for a folder call that did not give a value. */
export function describeFolderFailure(
  call:
    | { kind: "folder_error"; error: FolderErrorWord }
    | { kind: "invalid_response" }
    | { kind: "rejected" },
): Described {
  switch (call.kind) {
    case "folder_error":
      return error(call.error, folderErrorMessage(call.error));
    case "invalid_response":
      return error("invalid_response", "The host's answer was not what ALIBI expects, so it was not used.");
    case "rejected":
      return error("rejected", "The host did not allow this request.");
    default:
      return assertNever(call);
  }
}

export { NO_DOWNLOAD_STATEMENT };
