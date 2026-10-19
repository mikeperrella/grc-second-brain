// State for the Import screen (chunk 65g; ADR-022 decisions 1, 4 and 7; ADR-017 decisions 1, 2, 10
// and 11; docs/DESIGN.md 7 and 11). Pure: no React, no invoke.
//
// Flow: list the draft names in the exports folder (`folder_list_files`, kind exports) -> choose
// one -> read it (`draft_rows`, read only), which gives the run record's FULL `run_id` -> choose a
// customer (an existing one, or a new name with an explicit confirm) and the sent date -> PREVIEW
// (`ledger_import` with `preview: true`: every check and count, nothing written) -> the real import
// through the two-step confirm (confirm.ts, ledgerActions.ts `importPromises`).
//
// The rules this file holds:
//   * The request is built from the inputs by `importFields`, which refuses (returns null) unless
//     exactly one of an existing customer id and a new name is set, a new name has 1 to 1,000 code
//     points and is confirmed, and the sent date passes `isCalendarDate`. An unset id or name is
//     LEFT OUT, never sent as null.
//   * The run id comes only from the draft_rows reply for the chosen file. It is never typed and
//     never an 8-hex prefix.
//   * The import is possible only when a preview for IDENTICAL inputs succeeded (`canImport`).
//     Changing any input forgets the preview.
//   * A reply is filed as a preview or as an import result by its own `preview` flag; a preview
//     reply is never stored or shown as an import result, and the other way round.
//
// `ledger_import` with `preview: false` is built in ledgerActions.ts only
// (scripts/ledgerSources.test.mjs scans for it). This file builds only the preview request.

import type { FileList, FolderCall } from "./ipc/folders";
import {
  codePointCount,
  isCalendarDate,
  type CallResult,
  type RouteReply,
  type RouteRequest,
} from "./ipc/proxy";
import { integerCounts } from "./ledger";
import {
  IMPORT_PROBLEM_CUSTOMER_CHOICE,
  IMPORT_PROBLEM_NAME_LENGTH,
  IMPORT_PROBLEM_NO_DRAFT,
  IMPORT_PROBLEM_NOT_CONFIRMED,
  IMPORT_PROBLEM_NO_SENT_ON,
  IMPORT_PROBLEM_SENT_ON_INVALID,
} from "./ledgerText";
import {
  describeApiError,
  describeFolderFailure,
  describeLedgerError,
  describeNonReply,
  fixedErrorMessage,
  type Described,
} from "./messages";

type ImportRequest = Extract<RouteRequest, { route: "ledger_import" }>;
type ImportBody = Extract<RouteReply<"ledger_import">, { status: 200 }>["body"];
export type ImportReport = ImportBody;

/** Every field of the import request except the route and `preview`. */
export type ImportFields = Omit<ImportRequest, "route" | "preview">;

/** The sidecar's limit on a new customer name (route.rs and api/ledger.py). */
export const MAX_NEW_NAME_POINTS = 1_000;

// ----- the inputs -----------------------------------------------------------------------------

/** What the person chose. An empty string means "not set" for the name and the date. */
export interface ImportInputs {
  file: string | null;
  existingCustomerId: string | null;
  newCustomerName: string;
  confirmNew: boolean;
  sentOn: string;
}

export const INITIAL_INPUTS: ImportInputs = {
  file: null,
  existingCustomerId: null,
  newCustomerName: "",
  confirmNew: false,
  sentOn: "",
};

export type InputChange =
  | { kind: "existing_customer"; customerId: string | null }
  | { kind: "new_customer_name"; name: string }
  | { kind: "confirm_new"; confirmNew: boolean }
  | { kind: "sent_on"; sentOn: string };

/** A closed reason the inputs cannot be previewed yet. */
export type InputProblem =
  | "no_draft"
  | "customer_choice"
  | "name_length"
  | "not_confirmed"
  | "no_sent_on"
  | "sent_on_invalid";

/** The words for an input problem. No wildcard: a new problem breaks compilation here. */
export function inputProblemText(problem: InputProblem): string {
  switch (problem) {
    case "no_draft":
      return IMPORT_PROBLEM_NO_DRAFT;
    case "customer_choice":
      return IMPORT_PROBLEM_CUSTOMER_CHOICE;
    case "name_length":
      return IMPORT_PROBLEM_NAME_LENGTH;
    case "not_confirmed":
      return IMPORT_PROBLEM_NOT_CONFIRMED;
    case "no_sent_on":
      return IMPORT_PROBLEM_NO_SENT_ON;
    case "sent_on_invalid":
      return IMPORT_PROBLEM_SENT_ON_INVALID;
  }
}

/** The first problem with these inputs and this run id, or `null` when a preview may be sent. */
export function inputProblem(inputs: ImportInputs, runId: string | null): InputProblem | null {
  if (inputs.file === null || runId === null) {
    return "no_draft";
  }
  const hasExisting = inputs.existingCustomerId !== null;
  const hasNew = inputs.newCustomerName !== "";
  if (hasExisting === hasNew) {
    return "customer_choice";
  }
  if (hasNew) {
    const points = codePointCount(inputs.newCustomerName);
    if (points < 1 || points > MAX_NEW_NAME_POINTS) {
      return "name_length";
    }
    if (!inputs.confirmNew) {
      return "not_confirmed";
    }
  }
  if (inputs.sentOn === "") {
    return "no_sent_on";
  }
  if (!isCalendarDate(inputs.sentOn)) {
    return "sent_on_invalid";
  }
  return null;
}

/**
 * The request fields for these inputs, or `null` when there is an input problem (nothing is then
 * sent). Exactly one of `existing_customer_id` and `new_customer_name` is present; the other is
 * left out, never null. `confirm_new` is the box's value for a new name and false for an existing
 * customer.
 */
export function importFields(inputs: ImportInputs, runId: string | null): ImportFields | null {
  if (inputProblem(inputs, runId) !== null || inputs.file === null || runId === null) {
    return null;
  }
  const customer =
    inputs.existingCustomerId !== null
      ? { existing_customer_id: inputs.existingCustomerId, confirm_new: false }
      : { new_customer_name: inputs.newCustomerName, confirm_new: inputs.confirmNew };
  return { run_id: runId, file: inputs.file, ...customer, sent_on: inputs.sentOn };
}

/** The preview request: every field, and `preview: true`. Writes nothing in the sidecar. */
export function previewRequest(fields: ImportFields): ImportRequest {
  return { route: "ledger_import", ...fields, preview: true };
}

/** A key that is equal for two field sets exactly when every field is equal. */
export function fieldsKey(fields: ImportFields): string {
  return JSON.stringify([
    fields.run_id,
    fields.file,
    fields.existing_customer_id ?? null,
    fields.new_customer_name ?? null,
    fields.confirm_new,
    fields.sent_on,
  ]);
}

// ----- the state ------------------------------------------------------------------------------

export type ImportFilesState =
  | { phase: "idle" }
  | { phase: "loading" }
  /** Includes "no exports folder is in use": never shown as an empty list. */
  | { phase: "failed"; failure: Described }
  | { phase: "listed"; files: readonly string[]; truncated: boolean };

export type ImportDraftState =
  | { phase: "none" }
  | { phase: "loading" }
  /** A refusal of the draft read. `counts` holds integers only (question_mismatch). */
  | { phase: "failed"; failure: Described; counts: readonly (readonly [string, number])[] }
  | { phase: "loaded"; runId: string; fileName: string; questionCount: number };

/** A failed preview or import: fixed words, integer counts, and for `customer_exists` the id. */
export interface ImportFailure {
  failure: Described;
  counts: readonly (readonly [string, number])[];
  /** Only for `ledger_refused` with `customer_exists`: the id from the refusal, never a name. */
  existingCustomerId: string | null;
}

export type PreviewState =
  | { phase: "none" }
  | { phase: "loading"; key: string }
  | ({ phase: "failed"; key: string } & ImportFailure)
  /** A reply whose own `preview` flag is true. `fields` is exactly what the preview sent. */
  | { phase: "previewed"; key: string; fields: ImportFields; report: ImportReport };

export type OutcomeState =
  | { phase: "none" }
  | { phase: "sending" }
  | ({ phase: "failed" } & ImportFailure)
  /** A reply whose own `preview` flag is false: what the import did. */
  | { phase: "imported"; report: ImportReport };

export interface ImportState {
  files: ImportFilesState;
  inputs: ImportInputs;
  draft: ImportDraftState;
  preview: PreviewState;
  outcome: OutcomeState;
}

export const INITIAL_IMPORT_STATE: ImportState = {
  files: { phase: "idle" },
  inputs: INITIAL_INPUTS,
  draft: { phase: "none" },
  preview: { phase: "none" },
  outcome: { phase: "none" },
};

export type ImportEvent =
  | { type: "files_requested" }
  | { type: "files_result"; call: FolderCall<FileList> }
  | { type: "file_selected"; file: string }
  | { type: "draft_requested" }
  /** `file` is the file the call was made for, so a late answer can be recognised. */
  | { type: "draft_result"; file: string; result: CallResult<"draft_rows"> }
  | { type: "input_changed"; change: InputChange }
  | { type: "preview_requested"; key: string }
  | { type: "preview_result"; key: string; fields: ImportFields; result: CallResult<"ledger_import"> }
  | { type: "import_started" }
  | { type: "import_result"; result: CallResult<"ledger_import"> };

/** The run id of the loaded draft for the chosen file, or `null`. */
export function runIdOf(state: ImportState): string | null {
  return state.draft.phase === "loaded" && state.draft.fileName === state.inputs.file ? state.draft.runId : null;
}

/** The fields the current inputs would send, or `null`. */
export function currentFields(state: ImportState): ImportFields | null {
  return importFields(state.inputs, runIdOf(state));
}

/**
 * Whether the import (step one) may be offered: a preview of exactly the current inputs succeeded
 * and no import is being sent.
 */
export function canImport(state: ImportState): boolean {
  const fields = currentFields(state);
  return (
    fields !== null &&
    state.preview.phase === "previewed" &&
    state.preview.key === fieldsKey(fields) &&
    state.outcome.phase !== "sending"
  );
}

/** What step one of an import would confirm: the previewed fields, or `null` when not allowed. */
export interface ImportTarget {
  fields: ImportFields;
  /** The preview's count of promises it would create, exactly as sent. */
  previewCreated: number;
}

export function importTarget(state: ImportState): ImportTarget | null {
  if (!canImport(state) || state.preview.phase !== "previewed") {
    return null;
  }
  return { fields: state.preview.fields, previewCreated: state.preview.report.created };
}

/** Which kind of result a reply is, from its own `preview` flag. */
export function reportKind(report: ImportReport): "preview" | "import" {
  return report.preview ? "preview" : "import";
}

/** `already_changed_since_draft` as shown: the integer, or `null` for "not available". Never 0 for null. */
export function alreadyChanged(report: ImportReport): number | null {
  return report.already_changed_since_draft;
}

function exportsNotConfigured(): Described {
  return {
    category: "error",
    text: fixedErrorMessage("export_root_not_configured"),
    code: "export_root_not_configured",
    models: [],
    showNoDownload: false,
  };
}

function invalidResponse(): ImportFailure {
  return { failure: describeNonReply({ kind: "invalid_response" }), counts: [], existingCustomerId: null };
}

/** A failed import or preview call as fixed words; `null` for a 200 success body. */
function importFailureOf(result: CallResult<"ledger_import">): ImportFailure | null {
  if (result.kind !== "reply") {
    return { failure: describeNonReply(result), counts: [], existingCustomerId: null };
  }
  const reply = result.reply;
  if (reply.status === 200) {
    return null;
  }
  const body = reply.body;
  const counts = body.error === "import_refused" ? integerCounts(body.counts) : [];
  const existingCustomerId =
    body.error === "ledger_refused" && body.code === "customer_exists" ? (body.customer_id ?? null) : null;
  return { failure: describeLedgerError(body), counts, existingCustomerId };
}

/** What changing one input does to the inputs. Never changes the input object. */
export function applyInputChange(inputs: ImportInputs, change: InputChange): ImportInputs {
  switch (change.kind) {
    case "existing_customer":
      return { ...inputs, existingCustomerId: change.customerId };
    case "new_customer_name":
      return { ...inputs, newCustomerName: change.name };
    case "confirm_new":
      return { ...inputs, confirmNew: change.confirmNew };
    case "sent_on":
      return { ...inputs, sentOn: change.sentOn };
  }
}

export function reduceImport(state: ImportState, event: ImportEvent): ImportState {
  switch (event.type) {
    case "files_requested":
      return { ...state, files: { phase: "loading" } };
    case "files_result": {
      const call = event.call;
      if (call.kind !== "ok") {
        const failure =
          call.kind === "folder_error" && call.error === "not_configured"
            ? exportsNotConfigured()
            : describeFolderFailure(call);
        return { ...state, files: { phase: "failed", failure } };
      }
      const files: ImportFilesState = { phase: "listed", files: call.value.files, truncated: call.value.truncated };
      if (state.inputs.file !== null && call.value.files.includes(state.inputs.file)) {
        return { ...state, files };
      }
      // The chosen file is gone from the folder: forget it, its draft and any preview of it.
      return {
        ...state,
        files,
        inputs: { ...state.inputs, file: null },
        draft: { phase: "none" },
        preview: { phase: "none" },
      };
    }
    case "file_selected":
      if (state.outcome.phase === "sending") {
        return state;
      }
      if (state.inputs.file === event.file) {
        return state;
      }
      return {
        ...state,
        inputs: { ...state.inputs, file: event.file },
        draft: { phase: "none" },
        preview: { phase: "none" },
        outcome: { phase: "none" },
      };
    case "draft_requested":
      return state.inputs.file === null ? state : { ...state, draft: { phase: "loading" } };
    case "draft_result": {
      if (state.inputs.file !== event.file || state.draft.phase !== "loading") {
        return state;
      }
      const result = event.result;
      if (result.kind !== "reply") {
        return { ...state, draft: { phase: "failed", failure: describeNonReply(result), counts: [] } };
      }
      const reply = result.reply;
      if (reply.status !== 200) {
        const counts = reply.body.error === "draft_refused" ? integerCounts(reply.body.counts) : [];
        return { ...state, draft: { phase: "failed", failure: describeApiError(reply.body), counts } };
      }
      return {
        ...state,
        draft: {
          phase: "loaded",
          runId: reply.body.run_id,
          fileName: event.file,
          questionCount: reply.body.question_count,
        },
      };
    }
    case "input_changed":
      if (state.outcome.phase === "sending") {
        return state;
      }
      // Any change forgets the preview: the import needs a preview of exactly these inputs.
      return {
        ...state,
        inputs: applyInputChange(state.inputs, event.change),
        preview: { phase: "none" },
        outcome: { phase: "none" },
      };
    case "preview_requested":
      return { ...state, preview: { phase: "loading", key: event.key }, outcome: { phase: "none" } };
    case "preview_result": {
      // A late answer for inputs that are no longer the ones being previewed changes nothing.
      if (state.preview.phase !== "loading" || state.preview.key !== event.key) {
        return state;
      }
      const failure = importFailureOf(event.result);
      if (failure !== null) {
        return { ...state, preview: { phase: "failed", key: event.key, ...failure } };
      }
      if (event.result.kind !== "reply" || event.result.reply.status !== 200) {
        return state;
      }
      const report = event.result.reply.body;
      // A reply that does not say it is a preview is never kept as one.
      if (reportKind(report) !== "preview") {
        return { ...state, preview: { phase: "failed", key: event.key, ...invalidResponse() } };
      }
      return { ...state, preview: { phase: "previewed", key: event.key, fields: event.fields, report } };
    }
    case "import_started":
      return { ...state, outcome: { phase: "sending" } };
    case "import_result": {
      if (state.outcome.phase !== "sending") {
        return state;
      }
      const failure = importFailureOf(event.result);
      if (failure !== null) {
        return { ...state, outcome: { phase: "failed", ...failure } };
      }
      if (event.result.kind !== "reply" || event.result.reply.status !== 200) {
        return state;
      }
      const report = event.result.reply.body;
      // A reply that says it is a preview is never shown as an import result.
      if (reportKind(report) !== "import") {
        return { ...state, outcome: { phase: "failed", ...invalidResponse() } };
      }
      // The preview described the ledger before this import; it no longer does.
      return { ...state, outcome: { phase: "imported", report }, preview: { phase: "none" } };
    }
  }
}
