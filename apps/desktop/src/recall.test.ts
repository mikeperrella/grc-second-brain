// The Recall screen's state and words (ADR-017 decisions 17 and 20, threat model F31-4b). Pure.

import { describe, expect, it } from "vitest";
import { RECALL_FLAGS, RECALL_STATES, routeRequestSchema } from "./ipc/proxy";
import {
  ACKNOWLEDGE_STALE_STATEMENT,
} from "./fixedTexts";
import { STATE_CHANGED, STATE_INTACT, STATE_MISSING, STATE_MOVED } from "./ledgerText";
import { recallItem, recallReport } from "./ledgerTestSupport";
import {
  INITIAL_RECALL_STATE,
  createRecallStore,
  describeKnownRecallFlag,
  describeKnownRecallState,
  describeRecallFlag,
  describeRecallState,
  recallRequest,
  reduceRecall,
  type RecallState,
} from "./recall";
import { errorOf, replyOf } from "./testSupport";

const CUSTOMER_ID = "00000001000000000000000000000000";

function running(): RecallState {
  return reduceRecall(INITIAL_RECALL_STATE, { type: "run_requested" });
}

describe("recall state words", () => {
  it("each known state has its own word and its own shape", () => {
    const words = RECALL_STATES.map((state) => describeKnownRecallState(state));
    expect(new Set(words.map((entry) => entry.label)).size).toBe(RECALL_STATES.length);
    expect(new Set(words.map((entry) => entry.shape)).size).toBe(RECALL_STATES.length);
    expect(describeKnownRecallState("intact").label).toBe(STATE_INTACT);
    expect(describeKnownRecallState("moved").label).toBe(STATE_MOVED);
    expect(describeKnownRecallState("changed").label).toBe(STATE_CHANGED);
    expect(describeKnownRecallState("missing").label).toBe(STATE_MISSING);
  });

  it("a value outside the closed set is unknown, never a label", () => {
    for (const value of ["", "INTACT", "intact ", "settled", "still_true", "constructor", "__proto__"]) {
      expect(describeRecallState(value), value).toEqual({ kind: "unknown" });
    }
    expect(describeRecallState("intact")).toMatchObject({ kind: "known", state: "intact" });
  });

  it("each flag has its own word, and an unknown flag is unknown", () => {
    const labels = RECALL_FLAGS.map(describeKnownRecallFlag);
    expect(new Set(labels).size).toBe(RECALL_FLAGS.length);
    expect(describeRecallFlag("review_overdue")).toMatchObject({ kind: "known", flag: "review_overdue" });
    expect(describeRecallFlag("all_clear")).toEqual({ kind: "unknown" });
    expect(describeRecallFlag("")).toEqual({ kind: "unknown" });
  });
});

describe("the recall request", () => {
  it("has no customer_id when none is chosen, and never a null one", () => {
    expect(recallRequest(null)).toEqual({ route: "ledger_recall" });
    expect(recallRequest(CUSTOMER_ID)).toEqual({ route: "ledger_recall", customer_id: CUSTOMER_ID });
    expect(routeRequestSchema.safeParse(recallRequest(null)).success).toBe(true);
    expect(routeRequestSchema.safeParse(recallRequest(CUSTOMER_ID)).success).toBe(true);
    expect(JSON.stringify(recallRequest(null))).not.toContain("null");
  });
});

describe("the recall run", () => {
  it("a report becomes the done state", () => {
    const report = recallReport([recallItem()]);
    const after = reduceRecall(running(), { type: "run_result", result: replyOf("ledger_recall", 200, report) });
    expect(after.run).toEqual({ phase: "done", report });
  });

  it("no vault folder, an unusable ledger and a busy slot are refusals with their own codes", () => {
    for (const [status, word] of [
      [503, "vault_root_not_configured"],
      [503, "ledger_root_not_configured"],
      [503, "ledger_unusable"],
      [409, "run_in_progress"],
    ] as const) {
      const after = reduceRecall(running(), { type: "run_result", result: errorOf("ledger_recall", status, word) });
      expect(after.run, word).toMatchObject({ phase: "failed", failure: { code: word } });
    }
  });

  it("too_many_promises shows integer counts only", () => {
    const after = reduceRecall(running(), {
      type: "run_result",
      result: errorOf("ledger_recall", 422, "recall_refused", {
        code: "too_many_promises",
        counts: { promises: 2, cap: 1 },
      }),
    });
    expect(after.run).toMatchObject({
      phase: "failed",
      failure: { code: "recall_refused:too_many_promises" },
      counts: [
        ["promises", 2],
        ["cap", 1],
      ],
    });
  });

  it("an invalid response and a proxy error are refusals", () => {
    expect(
      reduceRecall(running(), { type: "run_result", result: { kind: "invalid_response" } }).run,
    ).toMatchObject({ phase: "failed", failure: { code: "invalid_response" } });
    expect(
      reduceRecall(running(), { type: "run_result", result: { kind: "proxy_error", error: "timeout" } }).run,
    ).toMatchObject({ phase: "failed", failure: { code: "timeout" } });
  });

  it("a late answer nobody is waiting for changes nothing", () => {
    const result = replyOf("ledger_recall", 200, recallReport([]));
    expect(reduceRecall(INITIAL_RECALL_STATE, { type: "run_result", result })).toBe(INITIAL_RECALL_STATE);
  });

  it("changing the customer drops the list, and is ignored while a check is running", () => {
    const done = reduceRecall(running(), {
      type: "run_result",
      result: replyOf("ledger_recall", 200, recallReport([recallItem()])),
    });
    expect(reduceRecall(done, { type: "customer_chosen", customerId: CUSTOMER_ID })).toMatchObject({
      customerId: CUSTOMER_ID,
      run: { phase: "idle" },
    });
    const busy = running();
    expect(reduceRecall(busy, { type: "customer_chosen", customerId: CUSTOMER_ID })).toBe(busy);
  });
});

describe("the recall store the hook holds (65f tidy-up c)", () => {
  const OTHER_ID = "00000002000000000000000000000000";

  it("a customer chosen during a run does not change the stored customer or the next request", () => {
    const store = createRecallStore();
    store.dispatch({ type: "customer_chosen", customerId: CUSTOMER_ID });
    expect(store.request()).toEqual({ route: "ledger_recall", customer_id: CUSTOMER_ID });
    store.dispatch({ type: "run_requested" });
    // The reducer ignores this choice, so the store's customer, and what a run sends, stay the same.
    const after = store.dispatch({ type: "customer_chosen", customerId: OTHER_ID });
    expect(after.customerId).toBe(CUSTOMER_ID);
    expect(store.state().customerId).toBe(CUSTOMER_ID);
    expect(store.request()).toEqual({ route: "ledger_recall", customer_id: CUSTOMER_ID });
    // Clearing the customer during the run is ignored the same way: no request for all customers.
    store.dispatch({ type: "customer_chosen", customerId: null });
    expect(store.request()).toEqual({ route: "ledger_recall", customer_id: CUSTOMER_ID });
  });

  it("after the run ends, a new choice is kept and the next request uses it", () => {
    const store = createRecallStore();
    store.dispatch({ type: "run_requested" });
    store.dispatch({ type: "customer_chosen", customerId: OTHER_ID });
    expect(store.request()).toEqual({ route: "ledger_recall" });
    store.dispatch({ type: "run_result", result: replyOf("ledger_recall", 200, recallReport([])) });
    store.dispatch({ type: "customer_chosen", customerId: OTHER_ID });
    expect(store.state().customerId).toBe(OTHER_ID);
    expect(store.request()).toEqual({ route: "ledger_recall", customer_id: OTHER_ID });
  });

  it("the store's state is exactly the reducer's for every event", () => {
    const store = createRecallStore();
    let expected = INITIAL_RECALL_STATE;
    for (const event of [
      { type: "customer_chosen", customerId: CUSTOMER_ID },
      { type: "run_requested" },
      { type: "customer_chosen", customerId: OTHER_ID },
      { type: "run_result", result: replyOf("ledger_recall", 200, recallReport([recallItem()])) },
    ] as const) {
      expected = reduceRecall(expected, event);
      expect(store.dispatch(event)).toEqual(expected);
    }
  });
});

describe("the acknowledge write", () => {
  it("a stale acknowledgement says it is stale, with the approved sentence", () => {
    const after = reduceRecall(INITIAL_RECALL_STATE, {
      type: "ack_result",
      result: errorOf("ledger_acknowledge", 422, "recall_refused", { code: "acknowledge_stale" }),
    });
    expect(after.ack).toMatchObject({ phase: "refused", failure: { code: "recall_refused:acknowledge_stale" } });
    if (after.ack.phase === "refused") {
      expect(after.ack.failure.text).toContain(ACKNOWLEDGE_STALE_STATEMENT);
    }
  });

  it("promise_voided and promise_unknown are refusals of their own", () => {
    const voided = reduceRecall(INITIAL_RECALL_STATE, {
      type: "ack_result",
      result: errorOf("ledger_acknowledge", 422, "recall_refused", { code: "promise_voided" }),
    });
    const unknown = reduceRecall(INITIAL_RECALL_STATE, {
      type: "ack_result",
      result: errorOf("ledger_acknowledge", 422, "ledger_refused", { code: "promise_unknown" }),
    });
    expect(voided.ack).toMatchObject({ phase: "refused", failure: { code: "recall_refused:promise_voided" } });
    expect(unknown.ack).toMatchObject({ phase: "refused", failure: { code: "ledger_refused:promise_unknown" } });
  });

  it("a success is done", () => {
    const after = reduceRecall(INITIAL_RECALL_STATE, {
      type: "ack_result",
      result: replyOf("ledger_acknowledge", 200, {
        promise_id: "00000000-0000-4000-8000-000000000001",
        event_id: 6,
        kind: "acknowledged",
      }),
    });
    expect(after.ack).toEqual({ phase: "done", kind: "acknowledge" });
  });
});
