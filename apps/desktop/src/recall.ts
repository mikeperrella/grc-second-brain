// State for the Recall screen (ADR-017 decisions 17 and 20, threat model F31-4b, docs/DESIGN.md 11).
// Pure: no React, no invoke.
//
// Recall checks each promise's cited lines against the vault and records a state change in the
// ledger when it finds one. The state words and flag words come from the exhaustive switches below:
// a value that is not in the closed set is `unknown` and the window shows an error block for it,
// never a label. The statement and scope the screen shows are the window's constants
// (fixedTexts.ts RECALL_STATEMENT and RECALL_SCOPE), never the strings in the reply.

import type { RECALL_FLAGS, RECALL_STATES, CallResult, RouteReply, RouteRequest } from "./ipc/proxy";
import { failureOf, integerCounts, type WriteState } from "./ledger";
import type { AcknowledgeTarget } from "./ledgerActions";
import {
  FLAG_DOCUMENT_CHANGED_ELSEWHERE,
  FLAG_FRONTMATTER_INVALID,
  FLAG_REVIEW_OVERDUE,
  STATE_CHANGED,
  STATE_INTACT,
  STATE_MISSING,
  STATE_MOVED,
} from "./ledgerText";
import type { Described } from "./messages";

type RecallBody = Extract<RouteReply<"ledger_recall">, { status: 200 }>["body"];
export type RecallReport = RecallBody;
export type RecallItem = RecallBody["items"][number];

export type RecallStateName = (typeof RECALL_STATES)[number];
export type RecallFlagName = (typeof RECALL_FLAGS)[number];

// ----- state and flag words, from exhaustive switches -----------------------------------------

/** Four outlines that differ in shape as well as fill, so state is never colour alone. */
export type RecallShape = "ring" | "diamond" | "triangle-exclamation" | "square-cross";

export type RecallStateDescription =
  | { kind: "known"; state: RecallStateName; label: string; shape: RecallShape }
  | { kind: "unknown" };

/** Label and shape of a known state. No wildcard: a new member breaks compilation here. */
export function describeKnownRecallState(state: RecallStateName): { label: string; shape: RecallShape } {
  switch (state) {
    case "intact":
      return { label: STATE_INTACT, shape: "ring" };
    case "moved":
      return { label: STATE_MOVED, shape: "diamond" };
    case "changed":
      return { label: STATE_CHANGED, shape: "triangle-exclamation" };
    case "missing":
      return { label: STATE_MISSING, shape: "square-cross" };
    default:
      return assertNever(state);
  }
}

function isRecallState(value: string): value is RecallStateName {
  return value === "intact" || value === "moved" || value === "changed" || value === "missing";
}

/** Describe a state string from the sidecar. Never throws; an unknown value is `unknown`. */
export function describeRecallState(value: string): RecallStateDescription {
  if (!isRecallState(value)) {
    return { kind: "unknown" };
  }
  return { kind: "known", state: value, ...describeKnownRecallState(value) };
}

export type RecallFlagDescription =
  | { kind: "known"; flag: RecallFlagName; label: string }
  | { kind: "unknown" };

export function describeKnownRecallFlag(flag: RecallFlagName): string {
  switch (flag) {
    case "document_changed_elsewhere":
      return FLAG_DOCUMENT_CHANGED_ELSEWHERE;
    case "review_overdue":
      return FLAG_REVIEW_OVERDUE;
    case "frontmatter_invalid":
      return FLAG_FRONTMATTER_INVALID;
    default:
      return assertNever(flag);
  }
}

function isRecallFlag(value: string): value is RecallFlagName {
  return value === "document_changed_elsewhere" || value === "review_overdue" || value === "frontmatter_invalid";
}

export function describeRecallFlag(value: string): RecallFlagDescription {
  if (!isRecallFlag(value)) {
    return { kind: "unknown" };
  }
  return { kind: "known", flag: value, label: describeKnownRecallFlag(value) };
}

function assertNever(value: never): never {
  // Reached only if a type and its switch disagree at run time; callers map any value outside the
  // closed set to `unknown` before getting here.
  throw new Error(`unhandled recall word: ${String(value)}`);
}

// ----- the request ----------------------------------------------------------------------------

/** The recall request: `customer_id` is left out when no customer is chosen, never null. */
export function recallRequest(customerId: string | null): Extract<RouteRequest, { route: "ledger_recall" }> {
  return customerId === null
    ? { route: "ledger_recall" }
    : { route: "ledger_recall", customer_id: customerId };
}

/**
 * Whether an item can be acknowledged. The acknowledge request needs the item's own
 * `recall_event_id`; an item without one has nothing to acknowledge and no id is ever guessed.
 */
export function acknowledgeableEventId(item: RecallItem): number | null {
  return item.recall_event_id;
}

/**
 * What step one of an acknowledgement would confirm, or `null` when the item has no
 * `recall_event_id`. The seen id is the item's own, copied as the reply gave it: never computed,
 * never taken from another item, never a default.
 */
export function acknowledgeTarget(item: RecallItem): AcknowledgeTarget | null {
  const seenRecallEventId = acknowledgeableEventId(item);
  if (seenRecallEventId === null) {
    return null;
  }
  return { promiseId: item.promise_id, seenRecallEventId, sourcePath: item.source_path };
}

// ----- the state ------------------------------------------------------------------------------

export type RecallRun =
  | { phase: "idle" }
  | { phase: "running" }
  /** A refusal (no vault folder, too many promises, ...): no list at all. Counts are integers only. */
  | { phase: "failed"; failure: Described; counts: readonly (readonly [string, number])[] }
  | { phase: "done"; report: RecallReport };

export interface RecallState {
  customerId: string | null;
  run: RecallRun;
  ack: WriteState;
}

export const INITIAL_RECALL_STATE: RecallState = {
  customerId: null,
  run: { phase: "idle" },
  ack: { phase: "none" },
};

export type RecallEvent =
  | { type: "customer_chosen"; customerId: string | null }
  | { type: "run_requested" }
  | { type: "run_result"; result: CallResult<"ledger_recall"> }
  | { type: "ack_started" }
  | { type: "ack_result"; result: CallResult<"ledger_acknowledge"> };

export function reduceRecall(state: RecallState, event: RecallEvent): RecallState {
  switch (event.type) {
    case "customer_chosen":
      // A list is never kept beside another customer's name.
      return state.run.phase === "running"
        ? state
        : { ...state, customerId: event.customerId, run: { phase: "idle" }, ack: { phase: "none" } };
    case "run_requested":
      return { ...state, run: { phase: "running" }, ack: { phase: "none" } };
    case "run_result": {
      // A late answer nobody is waiting for changes nothing.
      if (state.run.phase !== "running") {
        return state;
      }
      const result = event.result;
      const failure = failureOf(result);
      if (failure !== null) {
        const counts =
          result.kind === "reply" && result.reply.status !== 200 && result.reply.body.error === "recall_refused"
            ? integerCounts(result.reply.body.counts)
            : [];
        return { ...state, run: { phase: "failed", failure, counts } };
      }
      if (result.kind !== "reply" || result.reply.status !== 200) {
        return state;
      }
      return { ...state, run: { phase: "done", report: result.reply.body } };
    }
    case "ack_started":
      return { ...state, ack: { phase: "sending", kind: "acknowledge" } };
    case "ack_result": {
      const failure = failureOf(event.result);
      return failure !== null
        ? { ...state, ack: { phase: "refused", kind: "acknowledge", failure } }
        : { ...state, ack: { phase: "done", kind: "acknowledge" } };
    }
  }
}

// ----- the store the hook holds (65f tidy-up c) -----------------------------------------------

export interface RecallStore {
  state(): RecallState;
  /** Apply one event through the reducer and return the new state. */
  dispatch(event: RecallEvent): RecallState;
  /** The request for the customer the reducer holds now. There is no second copy of the customer. */
  request(): Extract<RouteRequest, { route: "ledger_recall" }>;
}

/**
 * The state the Recall hook holds. The customer a run uses is read from the reducer's state, so a
 * choice the reducer ignores (a run is in progress) can never change what the next run sends.
 */
export function createRecallStore(): RecallStore {
  let current: RecallState = INITIAL_RECALL_STATE;
  return {
    state: () => current,
    dispatch: (event) => {
      current = reduceRecall(current, event);
      return current;
    },
    request: () => recallRequest(current.customerId),
  };
}
