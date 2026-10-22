// State for the Contradictions screen (chunk 65i; ADR-019 and its 2026-10-18 addendum, ADR-022
// decisions 2 and 8, threat model F33-9, F33-10, F33-11; docs/DESIGN.md 4.5, 8 and 11.1). Pure:
// no React, no invoke of its own (the hook passes `invoke` in).
//
// Flow: optional scope and cap → Start (`contradictions_start`, answers 202 with a run id) → poll
// `contradictions_status` about once a second with THAT run id → complete, failed or cancelled.
// There is no cancel route, so there is no cancel control.
//
// This module is the ONE place both routes are named. Every decision lives here so it is tested
// without a DOM: what is sent (unset values are left out, never null or empty), what is polled
// (the run id from the start reply, only while `running`), when polling stops (any terminal status
// moves the phase out of `running`), and which answers are dropped (a status for another run).
//
// What the screen may and may not say (DESIGN.md 4.5): pairs done and pairs total are numbers;
// while the total is not known yet the pairs are "still being counted", never a 0; no estimate, no
// percent bar, and never the word "stuck". Findings are never stored by the window beyond this
// state, and a reply field is never shown as a statement: every statement is a window constant.

import {
  CONTRA_FLAG_MODEL_NOT_CONSULTED,
  CONTRA_MODEL_STATUS_AVAILABLE,
  CONTRA_MODEL_STATUS_MODEL_MISSING,
  CONTRA_MODEL_STATUS_NON_LOCAL_REFUSED,
  CONTRA_MODEL_STATUS_PARTIAL,
  CONTRA_MODEL_STATUS_UNAVAILABLE,
  CONTRA_NOT_AVAILABLE,
  CONTRA_PAIRS_COUNTING,
  CONTRA_PAIRS_DONE,
  CONTRA_PAIRS_OF,
  CONTRA_PROBLEM_CAP,
  CONTRA_PROBLEM_SCOPE,
  CONTRA_REASON_DAYS_VS_MONTHS,
  CONTRA_REASON_MODEL_LOWERED,
  CONTRA_REASON_MODEL_QUOTE_NOT_VERIFIED,
  CONTRA_REASON_NO_MODEL_VERDICT,
  CONTRA_REASON_WEAK_SUBJECT_OVERLAP,
  CONTRA_RELATION_SAME_SUBJECT_VALUE_MISMATCH,
  CONTRA_RELATION_SCOPE_DIFFERENCE,
  CONTRA_RELATION_UNCLEAR,
  CONTRA_RELATION_UNRELATED,
  CONTRA_COUNT_CHUNKS_READ,
  CONTRA_COUNT_CLAIMS_FOUND,
  CONTRA_COUNT_EXCLUDED_CHUNKS,
  CONTRA_COUNT_FINDINGS,
  CONTRA_COUNT_MODEL_CALLS,
  CONTRA_COUNT_NO_VERDICT,
  CONTRA_COUNT_OVER_CAP,
  CONTRA_COUNT_PAIRS_GENERATED,
  CONTRA_COUNT_WITHHELD_STALE,
} from "./ledgerText";
import {
  CONTRADICTIONS_LABEL_CANDIDATE,
  CONTRADICTIONS_LABEL_VALUE_CONFLICT,
} from "./fixedTexts";
import {
  CANDIDATE_REASONS,
  FINDING_FLAGS,
  FINDING_STATUSES,
  MODEL_USES,
  RELATIONS,
  callRoute,
  contradictionScope,
  type CallResult,
  type Invoke,
  type RouteReply,
  type RouteRequest,
} from "./ipc/proxy";
import {
  describeContradictionError,
  describeContradictionFailure,
  describeNonReply,
  type Described,
  type LedgerErrorBody,
} from "./messages";

type StatusBody = Extract<RouteReply<"contradictions_status">, { status: 200 }>["body"];
export type ContradictionReport = NonNullable<StatusBody["report"]>;
export type Finding = ContradictionReport["findings"][number];
export type FindingStatusName = (typeof FINDING_STATUSES)[number];
export type CandidateReasonName = (typeof CANDIDATE_REASONS)[number];
export type FindingFlagName = (typeof FINDING_FLAGS)[number];
export type RelationName = (typeof RELATIONS)[number];
export type ModelUseName = (typeof MODEL_USES)[number];

function assertNever(value: never): never {
  // Reached only if a type and its switch disagree at run time.
  throw new Error(`unhandled value: ${String(value)}`);
}

// ----- the inputs -----------------------------------------------------------------------------

/** What the person typed. Both are optional; an empty box means "not set". */
export interface ContradictionInputs {
  scope: string;
  cap: string;
}

export const INITIAL_CONTRADICTION_INPUTS: ContradictionInputs = { scope: "", cap: "" };

export type ContradictionProblem = "scope_invalid" | "cap_invalid";

/** The words of an input problem. No wildcard: a new problem breaks compilation here. */
export function contradictionProblemText(problem: ContradictionProblem): string {
  switch (problem) {
    case "scope_invalid":
      return CONTRA_PROBLEM_SCOPE;
    case "cap_invalid":
      return CONTRA_PROBLEM_CAP;
  }
}

/** The cap as a whole number 1 to 100, or `null` when the text is empty or is not one. */
function capNumber(text: string): number | null {
  if (!/^[0-9]{1,3}$/.test(text)) {
    return null;
  }
  const value = Number(text);
  return value >= 1 && value <= 100 ? value : null;
}

/**
 * The first problem with the inputs, or `null` when a run can be started. The scope is checked
 * with the request schema's own predicate (the host's `scope_is_valid`), so the window and the
 * host cannot disagree.
 */
export function contradictionProblem(inputs: ContradictionInputs): ContradictionProblem | null {
  if (inputs.scope !== "" && !contradictionScope.safeParse(inputs.scope).success) {
    return "scope_invalid";
  }
  if (inputs.cap !== "" && capNumber(inputs.cap) === null) {
    return "cap_invalid";
  }
  return null;
}

/**
 * The start request. A value that is not set is LEFT OUT: never null, never an empty string.
 * Call only when `contradictionProblem` is `null`.
 */
export function startRequest(inputs: ContradictionInputs): Extract<RouteRequest, { route: "contradictions_start" }> {
  const scope = inputs.scope === "" ? {} : { scope: inputs.scope };
  const cap = capNumber(inputs.cap);
  return { route: "contradictions_start", ...scope, ...(cap === null ? {} : { cap }) };
}

/**
 * The one place a run is started (the hook calls this). With an input problem it returns `null`
 * and `invoke` is never called. The request schema in callRoute checks the same rules again.
 */
export function sendStart(
  invoke: Invoke,
  inputs: ContradictionInputs,
): Promise<CallResult<"contradictions_start">> | null {
  if (contradictionProblem(inputs) !== null) {
    return null;
  }
  return callRoute<"contradictions_start">(invoke, startRequest(inputs));
}

/** The one place a run's status is asked for. `runId` is the id from the start reply. */
export function pollStatus(invoke: Invoke, runId: string): Promise<CallResult<"contradictions_status">> {
  return callRoute<"contradictions_status">(invoke, { route: "contradictions_status", run_id: runId });
}

export interface StartGate {
  /** True only when no start is in flight; it then marks one as in flight. */
  tryBegin(): boolean;
  /** The start has settled (or was never sent): the next `tryBegin` may succeed. */
  end(): void;
}

/**
 * Lets only one start be in flight. The hook holds one gate for its life. It keeps its own flag, so
 * two quick clicks on Start cannot both read a stale render-time state and send two requests (the
 * sidecar would answer 202 to one and 409 to the other, and a 409 arriving first would hide the
 * run that is going). Same idea as `createConfirmController` in confirm.ts.
 */
export function createStartGate(): StartGate {
  let inFlight = false;
  return {
    tryBegin: () => {
      if (inFlight) {
        return false;
      }
      inFlight = true;
      return true;
    },
    end: () => {
      inFlight = false;
    },
  };
}

// ----- the state ------------------------------------------------------------------------------

/** What a finished run shows. A `complete` run with no report is its own phase, never "no findings". */
export type ContradictionRun =
  | { phase: "none" }
  | { phase: "starting" }
  /** A refusal of the start, including "no vault folder is in use" and a 409: never an empty result. */
  | { phase: "refused"; failure: Described }
  | {
      phase: "running";
      runId: string;
      /** `null` until the sidecar has counted the pairs. */
      pairsTotal: number | null;
      pairsDone: number;
      /** The last poll failed (the run itself may be fine); polling goes on. */
      problem: Described | null;
    }
  | { phase: "complete"; report: ContradictionReport }
  /** `complete` but with `report: null`: shown as an error block. */
  | { phase: "report_missing" }
  | { phase: "failed"; failure: Described }
  | { phase: "cancelled" }
  /** `404 run_not_found` while polling. */
  | { phase: "unknown_run" };

export interface ContradictionsState {
  inputs: ContradictionInputs;
  run: ContradictionRun;
}

export const INITIAL_CONTRADICTIONS_STATE: ContradictionsState = {
  inputs: INITIAL_CONTRADICTION_INPUTS,
  run: { phase: "none" },
};

export type ContradictionsEvent =
  | { type: "input_changed"; field: "scope" | "cap"; value: string }
  | { type: "start_requested" }
  | { type: "start_result"; result: CallResult<"contradictions_start"> }
  /** `runId` is the id the poll was made for, so a late answer for an older run is recognised. */
  | { type: "status_result"; runId: string; result: CallResult<"contradictions_status"> };

/** A run is going (or about to): Start and the inputs are locked. */
export function runIsActive(run: ContradictionRun): boolean {
  return run.phase === "starting" || run.phase === "running";
}

export function reduceContradictions(state: ContradictionsState, event: ContradictionsEvent): ContradictionsState {
  switch (event.type) {
    case "input_changed":
      return runIsActive(state.run) ? state : { ...state, inputs: { ...state.inputs, [event.field]: event.value } };
    case "start_requested":
      // Starting drops the previous result (DESIGN.md 11.1: starting another run drops findings).
      return runIsActive(state.run) || contradictionProblem(state.inputs) !== null
        ? state
        : { ...state, run: { phase: "starting" } };
    case "start_result": {
      if (state.run.phase !== "starting") {
        return state;
      }
      const result = event.result;
      if (result.kind !== "reply") {
        return { ...state, run: { phase: "refused", failure: describeNonReply(result) } };
      }
      const reply = result.reply;
      if (reply.status !== 202) {
        return { ...state, run: { phase: "refused", failure: describeContradictionError(reply.body as LedgerErrorBody) } };
      }
      // The run id comes only from this reply.
      return { ...state, run: { phase: "running", runId: reply.body.run_id, pairsTotal: null, pairsDone: 0, problem: null } };
    }
    case "status_result": {
      const run = state.run;
      // A late answer for another run, or one nobody waits for, changes nothing.
      if (run.phase !== "running" || event.runId !== run.runId) {
        return state;
      }
      const result = event.result;
      if (result.kind !== "reply") {
        return { ...state, run: { ...run, problem: describeNonReply(result) } };
      }
      const reply = result.reply;
      if (reply.status === 404) {
        return { ...state, run: { phase: "unknown_run" } };
      }
      if (reply.status !== 200) {
        return { ...state, run: { ...run, problem: describeContradictionError(reply.body as LedgerErrorBody) } };
      }
      const body = reply.body;
      if (body.run_id !== run.runId) {
        return state;
      }
      switch (body.status) {
        case "running":
          return {
            ...state,
            run: { ...run, pairsTotal: body.pairs_total, pairsDone: body.pairs_done, problem: null },
          };
        case "complete":
          return {
            ...state,
            run: body.report === null ? { phase: "report_missing" } : { phase: "complete", report: body.report },
          };
        case "failed":
          return { ...state, run: { phase: "failed", failure: describeContradictionFailure(body.failure) } };
        case "cancelled":
          return { ...state, run: { phase: "cancelled" } };
        default:
          return assertNever(body.status);
      }
    }
  }
}

/** The run id to poll, or `null` when nothing should be polled. Only a running run is polled. */
export function contradictionPollTarget(state: ContradictionsState): string | null {
  return state.run.phase === "running" ? state.run.runId : null;
}

// ----- progress -------------------------------------------------------------------------------

/**
 * The progress words of a running run: "Pairs done: 2 of 55", or, while the total is not known,
 * only that the pairs are still being counted (never a 0, never an estimate or a percent).
 */
export function progressText(pairsDone: number, pairsTotal: number | null): string {
  if (pairsTotal === null) {
    return CONTRA_PAIRS_COUNTING;
  }
  return `${CONTRA_PAIRS_DONE}: ${pairsDone} ${CONTRA_PAIRS_OF} ${pairsTotal}`;
}

// ----- the words of a finding -----------------------------------------------------------------

export type FindingShape = "triangle-exclamation" | "half-circle";

export type FindingStatusDescription =
  | { kind: "known"; status: FindingStatusName; label: string; shape: FindingShape }
  | { kind: "unknown" };

function describeKnownStatus(status: FindingStatusName): { label: string; shape: FindingShape } {
  switch (status) {
    case "VALUE_CONFLICT":
      return { label: CONTRADICTIONS_LABEL_VALUE_CONFLICT, shape: "triangle-exclamation" };
    case "CANDIDATE":
      return { label: CONTRADICTIONS_LABEL_CANDIDATE, shape: "half-circle" };
    default:
      return assertNever(status);
  }
}

function isOf<T extends string>(list: readonly T[], value: string): value is T {
  return (list as readonly string[]).includes(value);
}

/** A status string from the sidecar. Never throws; an unknown value is `unknown`. */
export function describeFindingStatus(value: string): FindingStatusDescription {
  if (!isOf(FINDING_STATUSES, value)) {
    return { kind: "unknown" };
  }
  return { kind: "known", status: value, ...describeKnownStatus(value) };
}

export type LabelDescription<T extends string> = { kind: "known"; value: T; label: string } | { kind: "unknown" };

function describeKnownReason(reason: CandidateReasonName): string {
  switch (reason) {
    case "days_vs_months":
      return CONTRA_REASON_DAYS_VS_MONTHS;
    case "weak_subject_overlap":
      return CONTRA_REASON_WEAK_SUBJECT_OVERLAP;
    case "no_model_verdict":
      return CONTRA_REASON_NO_MODEL_VERDICT;
    case "model_lowered":
      return CONTRA_REASON_MODEL_LOWERED;
    case "model_quote_not_verified":
      return CONTRA_REASON_MODEL_QUOTE_NOT_VERIFIED;
    default:
      return assertNever(reason);
  }
}

export function describeReason(value: string): LabelDescription<CandidateReasonName> {
  return isOf(CANDIDATE_REASONS, value) ? { kind: "known", value, label: describeKnownReason(value) } : { kind: "unknown" };
}

function describeKnownFlag(flag: FindingFlagName): string {
  switch (flag) {
    case "model_not_consulted":
      return CONTRA_FLAG_MODEL_NOT_CONSULTED;
    default:
      return assertNever(flag);
  }
}

export function describeFlag(value: string): LabelDescription<FindingFlagName> {
  return isOf(FINDING_FLAGS, value) ? { kind: "known", value, label: describeKnownFlag(value) } : { kind: "unknown" };
}

function describeKnownRelation(relation: RelationName): string {
  switch (relation) {
    case "same_subject_value_mismatch":
      return CONTRA_RELATION_SAME_SUBJECT_VALUE_MISMATCH;
    case "scope_difference":
      return CONTRA_RELATION_SCOPE_DIFFERENCE;
    case "unrelated":
      return CONTRA_RELATION_UNRELATED;
    case "unclear":
      return CONTRA_RELATION_UNCLEAR;
    default:
      return assertNever(relation);
  }
}

export function describeRelation(value: string): LabelDescription<RelationName> {
  return isOf(RELATIONS, value) ? { kind: "known", value, label: describeKnownRelation(value) } : { kind: "unknown" };
}

function describeKnownModelStatus(status: ModelUseName): string {
  switch (status) {
    case "available":
      return CONTRA_MODEL_STATUS_AVAILABLE;
    case "unavailable":
      return CONTRA_MODEL_STATUS_UNAVAILABLE;
    case "model_missing":
      return CONTRA_MODEL_STATUS_MODEL_MISSING;
    case "non_local_refused":
      return CONTRA_MODEL_STATUS_NON_LOCAL_REFUSED;
    case "partial":
      return CONTRA_MODEL_STATUS_PARTIAL;
    default:
      return assertNever(status);
  }
}

export function describeModelStatus(value: string): LabelDescription<ModelUseName> {
  return isOf(MODEL_USES, value) ? { kind: "known", value, label: describeKnownModelStatus(value) } : { kind: "unknown" };
}

// ----- the report's counts --------------------------------------------------------------------

/**
 * The integer counts of a report, in a fixed order, each as the plain integer the sidecar sent
 * (no locale formatting). A count that is missing or null is "not available", never 0. The
 * findings count is the length of the findings list.
 */
export function reportCountRows(report: ContradictionReport): ReadonlyArray<readonly [string, string]> {
  const show = (value: number | null | undefined): string => (typeof value === "number" ? String(value) : CONTRA_NOT_AVAILABLE);
  return [
    [CONTRA_COUNT_FINDINGS, show(report.findings.length)],
    [CONTRA_COUNT_OVER_CAP, show(report.over_cap)],
    [CONTRA_COUNT_NO_VERDICT, show(report.no_verdict)],
    [CONTRA_COUNT_MODEL_CALLS, show(report.model_calls)],
    [CONTRA_COUNT_CLAIMS_FOUND, show(report.claims_found)],
    [CONTRA_COUNT_PAIRS_GENERATED, show(report.pairs_generated)],
    [CONTRA_COUNT_WITHHELD_STALE, show(report.withheld_stale)],
    [CONTRA_COUNT_EXCLUDED_CHUNKS, show(report.excluded_chunks)],
    [CONTRA_COUNT_CHUNKS_READ, show(report.chunks_read)],
  ];
}
