// State for the Reality check screen (chunk 65j; ADR-016 decisions 5, 6, 7 and 11, ADR-022, threat
// model F26-7 and F43; docs/DESIGN.md 11.1). Pure: no React, no invoke of its own (the hook passes
// `invoke` in).
//
// Flow: the export is chosen from the Imports folder listing (names only; the window never sends a
// path) → `reality_inspect` reads it (counts and the values it holds) → the person chooses which
// authenticator types count as MFA, which are phone, and which statuses are in scope, picks one
// policy claim from `reality_claims`, types the export date and CONFIRMS the MFA types → one
// `reality_check`. Nothing is sent by the check until every step is done.
//
// This module is the ONE place the three reality routes are named. Every decision lives here so it is
// tested without a DOM: what is sent (every value comes from the listing, the inspect reply, the
// claims reply or the person's own typing; an unset value is left out, never null), which answers are
// dropped (a late one), and what the result may say.
//
// What the result may say: ONE of the two DESIGN.md 11.1 sentences, chosen from the reply's own
// `finding` word. The window computes no verdict of its own. When the counts or the file hash
// disagree with the reply's finding, there is no sentence at all, only an error block. A statement is
// never taken from the reply: every statement is a window constant. The personal data (the logins)
// lives in this state only, and the pure reset below drops it when the person leaves the screen.

import { REALITY_RESULT_EXCEPTIONS, REALITY_RESULT_NONE } from "./fixedTexts";
import { listFiles, type FileList, type FolderCall } from "./ipc/folders";
import {
  callRoute,
  isCalendarDate,
  type CallResult,
  type Invoke,
  type RouteReply,
  type RouteRequest,
} from "./ipc/proxy";
import {
  REALITY_LABEL_ADMIN_UNKNOWN,
  REALITY_LABEL_ADMINS_WITHOUT_MFA,
  REALITY_LABEL_DATA_ROWS,
  REALITY_LABEL_IN_SCOPE,
  REALITY_LABEL_LOGINS_NOT_LISTED,
  REALITY_LABEL_MALFORMED,
  REALITY_LABEL_PHONE_ONLY,
  REALITY_LABEL_REPEATED_LOGINS,
  REALITY_LABEL_SIZE,
  REALITY_LABEL_SKIPPED_NOT_LISTED,
  REALITY_LABEL_STATUSES_NOT_LISTED,
  REALITY_LABEL_TYPES_NOT_LISTED,
  REALITY_LABEL_WITH_MFA,
  REALITY_LABEL_WITHOUT_MFA,
  REALITY_NOT_AVAILABLE,
  REALITY_PROBLEM_DATE_INVALID,
  REALITY_PROBLEM_NO_CLAIM,
  REALITY_PROBLEM_NO_DATE,
  REALITY_PROBLEM_NO_FILE,
  REALITY_PROBLEM_NO_MFA,
  REALITY_PROBLEM_NO_STATUS,
  REALITY_PROBLEM_NOT_CONFIRMED,
  REALITY_PROBLEM_NOT_READ,
} from "./ledgerText";
import {
  describeFolderFailure,
  describeNonReply,
  describeRealityError,
  fixedErrorMessage,
  type Described,
  type LedgerErrorBody,
} from "./messages";

type InspectBody = Extract<RouteReply<"reality_inspect">, { status: 200 }>["body"];
type ClaimsBody = Extract<RouteReply<"reality_claims">, { status: 200 }>["body"];
export type CheckReport = Extract<RouteReply<"reality_check">, { status: 200 }>["body"];
export type ExportInspection = InspectBody;
export type ClaimsReport = ClaimsBody;
export type ValueCount = InspectBody["statuses"][number];
export type ClaimCandidate = ClaimsBody["candidates"][number];
export type ListedLogin = CheckReport["exceptions"][number];

// ----- the inputs -----------------------------------------------------------------------------

/**
 * What the person has chosen. Every value is read from a listing or a reply (the chosen file name,
 * the type and status values, the claim) except the export date, which the person types.
 * NOTHING is chosen at the start: ADR-016 decision 6 makes the MFA set the owner's policy decision.
 */
export interface RealityInputs {
  file: string | null;
  mfaTypes: readonly string[];
  phoneTypes: readonly string[];
  statuses: readonly string[];
  /** The candidate exactly as the claims reply gave it. */
  claim: ClaimCandidate | null;
  exportedAt: string;
  /** The person confirmed the MFA types. Any change to the types, phone types or statuses clears it. */
  confirmed: boolean;
}

export const INITIAL_REALITY_INPUTS: RealityInputs = {
  file: null,
  mfaTypes: [],
  phoneTypes: [],
  statuses: [],
  claim: null,
  exportedAt: "",
  confirmed: false,
};

export type ChoiceKind = "mfa" | "phone" | "status";

export type InputChange =
  /** The position in the inspect reply's list. The value is read from the list, never typed. */
  | { kind: ChoiceKind; index: number }
  /** The position in the claims list, or `null` for none. */
  | { kind: "claim"; index: number | null }
  | { kind: "date"; text: string }
  | { kind: "confirm"; confirmed: boolean };

// ----- the views ------------------------------------------------------------------------------

export type FilesView =
  | { phase: "idle" }
  | { phase: "loading" }
  /** Includes "no imports folder is in use": never shown as an empty list. */
  | { phase: "failed"; failure: Described }
  /** `files` may be empty: the folder holds no .csv file. */
  | { phase: "listed"; files: readonly string[]; truncated: boolean };

export type InspectView =
  | { phase: "none" }
  | { phase: "loading"; file: string }
  | { phase: "failed"; failure: Described }
  | { phase: "inspected"; file: string; report: ExportInspection };

export type ClaimsView =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "failed"; failure: Described }
  | { phase: "listed"; report: ClaimsReport };

export type CheckView =
  | { phase: "none" }
  | { phase: "loading" }
  | { phase: "failed"; failure: Described }
  /** The report and the hash the inspect reply gave for the same file. */
  | { phase: "checked"; report: CheckReport; inspectSha256: string };

export interface RealityState {
  files: FilesView;
  inspect: InspectView;
  claims: ClaimsView;
  inputs: RealityInputs;
  /** Goes up on every input change and every reset, so an answer for older inputs is dropped. */
  generation: number;
  check: CheckView;
}

export const INITIAL_REALITY_STATE: RealityState = {
  files: { phase: "idle" },
  inspect: { phase: "none" },
  claims: { phase: "idle" },
  inputs: INITIAL_REALITY_INPUTS,
  generation: 0,
  check: { phase: "none" },
};

// ----- reading the lists ----------------------------------------------------------------------

/** The inspect reply's values of one kind (statuses or types), or none before the export is read. */
function valuesOf(state: RealityState, kind: ChoiceKind): readonly ValueCount[] {
  if (state.inspect.phase !== "inspected") {
    return [];
  }
  return kind === "status" ? state.inspect.report.statuses : state.inspect.report.authenticator_types;
}

/** A value the sidecar cut short cannot be chosen: what would be sent is not what the file holds. */
export function isChoosable(entry: ValueCount): boolean {
  return !entry.truncated;
}

/** The chosen values in the inspect list's order, each once, each exactly as listed. */
function chosenValues(list: readonly ValueCount[], picked: readonly string[]): string[] {
  const out: string[] = [];
  for (const entry of list) {
    if (isChoosable(entry) && picked.includes(entry.value) && !out.includes(entry.value)) {
      out.push(entry.value);
    }
  }
  return out;
}

function inputsOf(kind: ChoiceKind, inputs: RealityInputs): readonly string[] {
  switch (kind) {
    case "mfa":
      return inputs.mfaTypes;
    case "phone":
      return inputs.phoneTypes;
    case "status":
      return inputs.statuses;
  }
}

/** Whether the value at `index` of the list is currently chosen. */
export function isChosen(state: RealityState, kind: ChoiceKind, index: number): boolean {
  const entry = valuesOf(state, kind)[index];
  return entry !== undefined && isChoosable(entry) && inputsOf(kind, state.inputs).includes(entry.value);
}

/** Whether the claim at `index` of the claims list is the chosen one. */
export function isClaimChosen(state: RealityState, index: number): boolean {
  const chosen = state.inputs.claim;
  const listed = state.claims.phase === "listed" ? state.claims.report.candidates[index] : undefined;
  return chosen !== null && listed !== undefined && sameClaim(chosen, listed);
}

function sameClaim(a: ClaimCandidate, b: ClaimCandidate): boolean {
  return a.path === b.path && a.start_line === b.start_line && a.end_line === b.end_line && a.quote === b.quote;
}

/** The file name at `index` of the listing, exactly as listed, or `null`. */
export function fileAt(files: FilesView, index: number): string | null {
  return files.phase === "listed" ? (files.files[index] ?? null) : null;
}

// ----- input problems -------------------------------------------------------------------------

export type RealityProblem =
  | "no_file"
  | "not_read"
  | "no_mfa"
  | "no_status"
  | "no_claim"
  | "no_date"
  | "date_invalid"
  | "not_confirmed";

/** The words of an input problem. No wildcard: a new problem breaks compilation here. */
export function realityProblemText(problem: RealityProblem): string {
  switch (problem) {
    case "no_file":
      return REALITY_PROBLEM_NO_FILE;
    case "not_read":
      return REALITY_PROBLEM_NOT_READ;
    case "no_mfa":
      return REALITY_PROBLEM_NO_MFA;
    case "no_status":
      return REALITY_PROBLEM_NO_STATUS;
    case "no_claim":
      return REALITY_PROBLEM_NO_CLAIM;
    case "no_date":
      return REALITY_PROBLEM_NO_DATE;
    case "date_invalid":
      return REALITY_PROBLEM_DATE_INVALID;
    case "not_confirmed":
      return REALITY_PROBLEM_NOT_CONFIRMED;
  }
}

/**
 * The first thing missing before a check can be sent, or `null`. The MFA types must be CONFIRMED
 * (the last step), so nothing is sent with types the person has not confirmed.
 */
export function realityProblem(state: RealityState): RealityProblem | null {
  const inputs = state.inputs;
  if (inputs.file === null) {
    return "no_file";
  }
  if (state.inspect.phase !== "inspected" || state.inspect.file !== inputs.file) {
    return "not_read";
  }
  if (chosenValues(valuesOf(state, "mfa"), inputs.mfaTypes).length === 0) {
    return "no_mfa";
  }
  if (chosenValues(valuesOf(state, "status"), inputs.statuses).length === 0) {
    return "no_status";
  }
  const claim = inputs.claim;
  if (
    claim === null ||
    state.claims.phase !== "listed" ||
    !state.claims.report.candidates.some((candidate) => sameClaim(candidate, claim))
  ) {
    return "no_claim";
  }
  if (inputs.exportedAt === "") {
    return "no_date";
  }
  if (!isCalendarDate(inputs.exportedAt)) {
    return "date_invalid";
  }
  if (!inputs.confirmed) {
    return "not_confirmed";
  }
  return null;
}

// ----- what is sent ---------------------------------------------------------------------------

export interface CheckFields {
  file: string;
  claim: { path: string; start_line: number; end_line: number; quote: string };
  mfa_types: string[];
  phone_types: string[];
  active_statuses: string[];
  exported_at: string;
}

/** The fields of a check, or `null` while a problem stops it. Every value is read from a reply. */
export function currentCheckFields(state: RealityState): CheckFields | null {
  if (realityProblem(state) !== null) {
    return null;
  }
  const { file, claim, exportedAt } = state.inputs;
  if (file === null || claim === null) {
    return null;
  }
  return {
    file,
    // Exactly the four fields the claims reply gave, and no other field of the candidate.
    claim: { path: claim.path, start_line: claim.start_line, end_line: claim.end_line, quote: claim.quote },
    mfa_types: chosenValues(valuesOf(state, "mfa"), state.inputs.mfaTypes),
    // The host needs the list; an empty list means no phone type was named (never null).
    phone_types: chosenValues(valuesOf(state, "phone"), state.inputs.phoneTypes),
    active_statuses: chosenValues(valuesOf(state, "status"), state.inputs.statuses),
    exported_at: exportedAt,
  };
}

export function checkRequest(fields: CheckFields): Extract<RouteRequest, { route: "reality_check" }> {
  return { route: "reality_check", ...fields };
}

/** The one place the export is read. `file` is a name from the Imports listing. */
export function sendInspect(invoke: Invoke, file: string): Promise<CallResult<"reality_inspect">> {
  return callRoute<"reality_inspect">(invoke, { route: "reality_inspect", file });
}

/** The one place the policy claims are listed. */
export function sendClaims(invoke: Invoke): Promise<CallResult<"reality_claims">> {
  return callRoute<"reality_claims">(invoke, { route: "reality_claims" });
}

/** The one place the Imports folder is listed (names only). */
export function listImports(invoke: Invoke): Promise<FolderCall<FileList>> {
  return listFiles(invoke, "imports");
}

/**
 * The one place a check is sent. With a problem it returns `null` and `invoke` is never called. The
 * request schema in callRoute checks the same rules again.
 */
export function sendCheck(invoke: Invoke, state: RealityState): Promise<CallResult<"reality_check">> | null {
  const fields = currentCheckFields(state);
  if (fields === null) {
    return null;
  }
  return callRoute<"reality_check">(invoke, checkRequest(fields));
}

// ----- failures -------------------------------------------------------------------------------

/** Fixed words for a call that did not give its 200 body, or `null` when it did. */
export function realityFailureOf(
  result: CallResult<"reality_inspect" | "reality_claims" | "reality_check">,
): Described | null {
  if (result.kind !== "reply") {
    return describeNonReply(result);
  }
  const reply = result.reply as { status: number; body: unknown };
  if (reply.status === 200) {
    return null;
  }
  return describeRealityError(reply.body as LedgerErrorBody);
}

function importsNotConfigured(): Described {
  return {
    category: "error",
    text: fixedErrorMessage("import_root_not_configured"),
    code: "import_root_not_configured",
    models: [],
    showNoDownload: false,
  };
}

// ----- the result -----------------------------------------------------------------------------

export type ResultReading =
  | { kind: "sentence"; text: string }
  /** The finding and the counts do not agree: an error block, no sentence. */
  | { kind: "disagree" }
  /** The check counted another file than the one inspected: an error block, no sentence. */
  | { kind: "hash_mismatch" }
  /** A finding word outside the closed set: an error block, never a label. */
  | { kind: "unknown_finding" };

/**
 * The result sentence, taken from the reply's `finding` word and nothing else. `exceptions_found`
 * is the first DESIGN.md 11.1 sentence, `no_exceptions_in_export` the second. The counts and the
 * hash are only CHECKED against the finding: a disagreement gives no sentence at all.
 */
export function readResult(report: CheckReport, inspectSha256: string): ResultReading {
  // A string, so a word outside the closed set (past the schema) lands in the default.
  const finding: string = report.finding;
  switch (finding) {
    case "exceptions_found":
      if (report.sha256 !== inspectSha256) {
        return { kind: "hash_mismatch" };
      }
      return report.without_mfa > 0 ? { kind: "sentence", text: REALITY_RESULT_EXCEPTIONS } : { kind: "disagree" };
    case "no_exceptions_in_export":
      if (report.sha256 !== inspectSha256) {
        return { kind: "hash_mismatch" };
      }
      return report.without_mfa === 0 && report.exceptions.length === 0 && report.exceptions_not_listed === 0
        ? { kind: "sentence", text: REALITY_RESULT_NONE }
        : { kind: "disagree" };
    default:
      return { kind: "unknown_finding" };
  }
}

/** A count as the reply sent it. A count that is not there is "not available", never 0. */
export function countText(value: number | null | undefined): string {
  return value === null || value === undefined ? REALITY_NOT_AVAILABLE : String(value);
}

/** The label and count of every count of the inspect reply (the lists are shown apart). */
export function inspectCountRows(report: ExportInspection): ReadonlyArray<readonly [string, string]> {
  return [
    [REALITY_LABEL_SIZE, countText(report.size_bytes)],
    [REALITY_LABEL_DATA_ROWS, countText(report.data_rows)],
    [REALITY_LABEL_MALFORMED, countText(report.malformed_rows)],
    [REALITY_LABEL_REPEATED_LOGINS, countText(report.duplicate_logins)],
    [REALITY_LABEL_STATUSES_NOT_LISTED, countText(report.statuses_not_listed)],
    [REALITY_LABEL_TYPES_NOT_LISTED, countText(report.authenticator_types_not_listed)],
  ];
}

/** The label and count of every count of the check reply, the phone-only count under its own label. */
export function checkCountRows(report: CheckReport): ReadonlyArray<readonly [string, string]> {
  return [
    [REALITY_LABEL_IN_SCOPE, countText(report.in_scope)],
    [REALITY_LABEL_WITH_MFA, countText(report.with_mfa)],
    [REALITY_LABEL_WITHOUT_MFA, countText(report.without_mfa)],
    [REALITY_LABEL_ADMINS_WITHOUT_MFA, countText(report.admins_without_mfa)],
    [REALITY_LABEL_ADMIN_UNKNOWN, countText(report.admin_unknown)],
    [REALITY_LABEL_PHONE_ONLY, countText(report.phone_only)],
    [REALITY_LABEL_MALFORMED, countText(report.malformed_rows)],
    [REALITY_LABEL_REPEATED_LOGINS, countText(report.duplicate_logins)],
    [REALITY_LABEL_SKIPPED_NOT_LISTED, countText(report.skipped_not_listed)],
    [REALITY_LABEL_LOGINS_NOT_LISTED, countText(report.exceptions_not_listed)],
  ];
}

// ----- the reducer ----------------------------------------------------------------------------

export type RealityEvent =
  | { type: "files_requested" }
  | { type: "files_result"; call: FolderCall<FileList> }
  | { type: "claims_requested" }
  | { type: "claims_result"; result: CallResult<"reality_claims"> }
  /** The position in the listing; the file name is read from it. */
  | { type: "file_chosen"; index: number }
  | { type: "inspect_result"; file: string; result: CallResult<"reality_inspect"> }
  | { type: "input_changed"; change: InputChange }
  /** `generation` is the one the request was built from. */
  | { type: "check_requested"; generation: number }
  | { type: "check_result"; generation: number; result: CallResult<"reality_check"> }
  /** The person left the screen: the file, the types, the claim, the result and the logins are forgotten. */
  | { type: "reset" };

/**
 * What a screen change does to the Reality check state: when the screen now shown is another one,
 * the state is reset (the logins, the chosen file and types and the result are dropped). On the
 * Reality check screen itself it does nothing. screens.tsx sends this on every screen change.
 */
export function realityEventOnScreen(shown: string): { type: "reset" } | null {
  return shown === "reality" ? null : { type: "reset" };
}

/** True when nothing of the listing, the replies, a choice or a result is held. */
function isClean(state: RealityState): boolean {
  const inputs = state.inputs;
  return (
    state.files.phase === "idle" &&
    state.inspect.phase === "none" &&
    state.claims.phase === "idle" &&
    state.check.phase === "none" &&
    inputs.file === null &&
    inputs.mfaTypes.length === 0 &&
    inputs.phoneTypes.length === 0 &&
    inputs.statuses.length === 0 &&
    inputs.claim === null &&
    inputs.exportedAt === "" &&
    !inputs.confirmed
  );
}

function toggled(list: readonly string[], value: string): readonly string[] {
  return list.includes(value) ? list.filter((item) => item !== value) : [...list, value];
}

/** Toggle one value of a choice. Any change to a choice clears the confirmation: the person confirms what is shown now. */
function withChoice(inputs: RealityInputs, kind: ChoiceKind, value: string): RealityInputs {
  switch (kind) {
    case "mfa":
      return { ...inputs, mfaTypes: toggled(inputs.mfaTypes, value), confirmed: false };
    case "phone":
      return { ...inputs, phoneTypes: toggled(inputs.phoneTypes, value), confirmed: false };
    case "status":
      return { ...inputs, statuses: toggled(inputs.statuses, value), confirmed: false };
  }
}

/** Apply one input change; `null` when it changes nothing (an index with no value, or one cut short). */
function applyChange(state: RealityState, change: InputChange): RealityInputs | null {
  const inputs = state.inputs;
  switch (change.kind) {
    case "mfa":
    case "phone":
    case "status": {
      const entry = valuesOf(state, change.kind)[change.index];
      if (entry === undefined || !isChoosable(entry)) {
        return null;
      }
      return withChoice(inputs, change.kind, entry.value);
    }
    case "claim": {
      if (change.index === null) {
        return { ...inputs, claim: null };
      }
      const listed = state.claims.phase === "listed" ? state.claims.report.candidates[change.index] : undefined;
      return listed === undefined ? null : { ...inputs, claim: listed };
    }
    case "date":
      return { ...inputs, exportedAt: change.text };
    case "confirm":
      return { ...inputs, confirmed: change.confirmed };
  }
}

export function reduceReality(state: RealityState, event: RealityEvent): RealityState {
  switch (event.type) {
    case "reset":
      // Already clean: the same object, so no re-render happens. Otherwise the generation goes up,
      // so a late answer for the old inputs is dropped.
      if (isClean(state)) {
        return state;
      }
      return { ...INITIAL_REALITY_STATE, generation: state.generation + 1 };
    case "files_requested":
      return { ...state, files: { phase: "loading" } };
    case "files_result": {
      // A late answer nobody waits for (after a reset) changes nothing.
      if (state.files.phase !== "loading") {
        return state;
      }
      const call = event.call;
      if (call.kind !== "ok") {
        const failure =
          call.kind === "folder_error" && call.error === "not_configured"
            ? importsNotConfigured()
            : describeFolderFailure(call);
        return { ...state, files: { phase: "failed", failure } };
      }
      const files: FilesView = { phase: "listed", files: call.value.files, truncated: call.value.truncated };
      if (state.inputs.file !== null && call.value.files.includes(state.inputs.file)) {
        return { ...state, files };
      }
      // The chosen file is gone from the folder (or none was chosen): forget it and what came of it.
      return {
        ...state,
        files,
        inspect: { phase: "none" },
        inputs: { ...state.inputs, file: null, mfaTypes: [], phoneTypes: [], statuses: [], confirmed: false },
        generation: state.generation + 1,
        check: { phase: "none" },
      };
    }
    case "claims_requested":
      return { ...state, claims: { phase: "loading" } };
    case "claims_result": {
      if (state.claims.phase !== "loading") {
        return state;
      }
      const failure = realityFailureOf(event.result);
      if (failure !== null) {
        return {
          ...state,
          claims: { phase: "failed", failure },
          inputs: { ...state.inputs, claim: null },
          generation: state.generation + 1,
          check: { phase: "none" },
        };
      }
      if (event.result.kind !== "reply" || event.result.reply.status !== 200) {
        return state;
      }
      const report = event.result.reply.body;
      // A chosen claim stays chosen only while the same sentence is still listed.
      const chosen = state.inputs.claim;
      const kept = chosen !== null && report.candidates.some((candidate) => sameClaim(candidate, chosen));
      return {
        ...state,
        claims: { phase: "listed", report },
        inputs: kept ? state.inputs : { ...state.inputs, claim: null },
        generation: kept ? state.generation : state.generation + 1,
        check: kept ? state.check : { phase: "none" },
      };
    }
    case "file_chosen": {
      const file = fileAt(state.files, event.index);
      if (file === null) {
        return state;
      }
      // A new file: the types and statuses of the old one are gone, and so is any result.
      return {
        ...state,
        inspect: { phase: "loading", file },
        inputs: { ...state.inputs, file, mfaTypes: [], phoneTypes: [], statuses: [], confirmed: false },
        generation: state.generation + 1,
        check: { phase: "none" },
      };
    }
    case "inspect_result": {
      // A late answer for another file, or one nobody waits for, changes nothing.
      if (
        state.inspect.phase !== "loading" ||
        state.inspect.file !== event.file ||
        state.inputs.file !== event.file
      ) {
        return state;
      }
      const failure = realityFailureOf(event.result);
      if (failure !== null) {
        return { ...state, inspect: { phase: "failed", failure } };
      }
      if (event.result.kind !== "reply" || event.result.reply.status !== 200) {
        return state;
      }
      return { ...state, inspect: { phase: "inspected", file: event.file, report: event.result.reply.body } };
    }
    case "input_changed": {
      const inputs = applyChange(state, event.change);
      if (inputs === null) {
        return state;
      }
      // Any change drops the shown result: a result is never shown beside other inputs.
      return { ...state, inputs, generation: state.generation + 1, check: { phase: "none" } };
    }
    case "check_requested":
      if (
        event.generation !== state.generation ||
        state.check.phase === "loading" ||
        realityProblem(state) !== null
      ) {
        return state;
      }
      return { ...state, check: { phase: "loading" } };
    case "check_result": {
      // A late answer for other inputs, or one nobody waits for, changes nothing.
      if (event.generation !== state.generation || state.check.phase !== "loading") {
        return state;
      }
      const failure = realityFailureOf(event.result);
      if (failure !== null) {
        return { ...state, check: { phase: "failed", failure } };
      }
      if (event.result.kind !== "reply" || event.result.reply.status !== 200 || state.inspect.phase !== "inspected") {
        return state;
      }
      return {
        ...state,
        check: { phase: "checked", report: event.result.reply.body, inspectSha256: state.inspect.report.sha256 },
      };
    }
  }
}
