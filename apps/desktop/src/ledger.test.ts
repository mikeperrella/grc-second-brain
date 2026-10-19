// The Ledger screen's state (ADR-022, docs/DESIGN.md 7 and 11). Pure, no DOM.

import { describe, expect, it } from "vitest";
import { MAX_PAGE_SIZE, VOID_REASONS, callRoute, routeRequestSchema, type Invoke } from "./ipc/proxy";
import {
  DEFAULT_PAGE_SIZE,
  INITIAL_FILTER,
  INITIAL_LEDGER_STATE,
  PAGE_SIZES,
  applyFilterChange,
  customerNameOf,
  filterKey,
  hasNextPage,
  integerCounts,
  promisesRequest,
  reduceLedger,
  smallerPageSizes,
  voidReasonLabel,
  type LedgerState,
  type PromiseFilter,
} from "./ledger";
import { customer, promiseRow, promisesBody } from "./ledgerTestSupport";
import { errorOf, replyOf } from "./testSupport";

const CUSTOMER_ID = "00000001000000000000000000000000";

function listed(state: LedgerState = INITIAL_LEDGER_STATE, total = 1): LedgerState {
  return reduceLedger(state, {
    type: "promises_result",
    filter: state.filter,
    result: replyOf("ledger_promises", 200, promisesBody([promiseRow()], total)),
  });
}

describe("the ledger_promises request", () => {
  it("always carries include_voided, page and page_size, and no customer_id when none is chosen", () => {
    const request = promisesRequest(INITIAL_FILTER);
    expect(Object.keys(request).sort()).toEqual(["include_voided", "page", "page_size", "route"]);
    expect("customer_id" in request).toBe(false);
    expect(routeRequestSchema.safeParse(request).success).toBe(true);
  });

  it("sends the chosen customer id, and never a null id for any filter", () => {
    const withCustomer = promisesRequest({ ...INITIAL_FILTER, customerId: CUSTOMER_ID });
    expect(withCustomer.customer_id).toBe(CUSTOMER_ID);
    for (const filter of [INITIAL_FILTER, { ...INITIAL_FILTER, includeVoided: true, page: 3 }]) {
      expect(JSON.stringify(promisesRequest(filter))).not.toContain("null");
    }
  });

  it("goes through callRoute with exactly those fields (a null id would be refused unsent)", async () => {
    const sent: Record<string, unknown>[] = [];
    const invoke: Invoke = (_command, args) => {
      sent.push(args);
      return Promise.resolve({ status: 200, body: promisesBody([], 0) });
    };
    const result = await callRoute<"ledger_promises">(invoke, promisesRequest(INITIAL_FILTER));
    expect(result.kind).toBe("reply");
    expect(sent).toEqual([
      { request: { route: "ledger_promises", include_voided: false, page: 1, page_size: DEFAULT_PAGE_SIZE } },
    ]);
    // The host and the window schema refuse a null customer id, so nothing is sent.
    const nullId = { ...promisesRequest(INITIAL_FILTER), customer_id: null } as unknown as Parameters<
      typeof callRoute<"ledger_promises">
    >[1];
    const refused = await callRoute<"ledger_promises">(invoke, nullId);
    expect(refused.kind).toBe("invalid_request");
    expect(sent).toHaveLength(1);
  });

  it("the default page size and every offered size are within the host's range 1 to 100", () => {
    expect(DEFAULT_PAGE_SIZE).toBeGreaterThanOrEqual(1);
    expect(DEFAULT_PAGE_SIZE).toBeLessThanOrEqual(MAX_PAGE_SIZE);
    expect(PAGE_SIZES).toContain(DEFAULT_PAGE_SIZE);
    for (const size of PAGE_SIZES) {
      expect(size).toBeLessThanOrEqual(MAX_PAGE_SIZE);
      expect(routeRequestSchema.safeParse(promisesRequest({ ...INITIAL_FILTER, pageSize: size })).success).toBe(true);
    }
    // The default request itself is valid, so the first load is never refused unsent.
    expect(routeRequestSchema.safeParse(promisesRequest(INITIAL_FILTER)).success).toBe(true);
    expect(routeRequestSchema.safeParse(promisesRequest({ ...INITIAL_FILTER, pageSize: 101 })).success).toBe(false);
  });
});

describe("paging and filtering", () => {
  it("any change but a page change goes back to page 1, and a page never goes below 1", () => {
    const third: PromiseFilter = { ...INITIAL_FILTER, page: 3 };
    expect(applyFilterChange(third, { kind: "customer", customerId: CUSTOMER_ID }).page).toBe(1);
    expect(applyFilterChange(third, { kind: "include_voided", includeVoided: true }).page).toBe(1);
    expect(applyFilterChange(third, { kind: "page_size", pageSize: 10 }).page).toBe(1);
    expect(applyFilterChange(third, { kind: "page", page: 4 }).page).toBe(4);
    expect(applyFilterChange(third, { kind: "page", page: 0 }).page).toBe(1);
    expect(third.page).toBe(3);
  });

  it("knows whether a next page exists from the sidecar's total", () => {
    expect(hasNextPage({ ...INITIAL_FILTER, pageSize: 10 }, 10)).toBe(false);
    expect(hasNextPage({ ...INITIAL_FILTER, pageSize: 10 }, 11)).toBe(true);
    expect(hasNextPage({ ...INITIAL_FILTER, pageSize: 10, page: 2 }, 20)).toBe(false);
  });

  it("offers only sizes smaller than the current one", () => {
    expect(smallerPageSizes(50)).toEqual([5, 10, 25]);
    expect(smallerPageSizes(5)).toEqual([]);
  });

  it("has a different key for each filter", () => {
    expect(filterKey(INITIAL_FILTER)).not.toBe(filterKey({ ...INITIAL_FILTER, page: 2 }));
    expect(filterKey(INITIAL_FILTER)).not.toBe(filterKey({ ...INITIAL_FILTER, customerId: CUSTOMER_ID }));
  });
});

describe("the promises view", () => {
  it("shows a page with the sidecar's total, as sent", () => {
    const state = listed(INITIAL_LEDGER_STATE, 1234567);
    expect(state.promises).toMatchObject({ phase: "listed", total: 1234567 });
  });

  it("an empty list is a listed state with total 0, a different state from not configured", () => {
    const state = reduceLedger(INITIAL_LEDGER_STATE, {
      type: "promises_result",
      filter: INITIAL_FILTER,
      result: replyOf("ledger_promises", 200, promisesBody([], 0)),
    });
    expect(state.promises).toEqual({ phase: "listed", total: 0, promises: [] });
  });

  it("not configured is a refusal with its code, never a list and never a 0", () => {
    const state = reduceLedger(INITIAL_LEDGER_STATE, {
      type: "promises_result",
      filter: INITIAL_FILTER,
      result: errorOf("ledger_promises", 503, "ledger_root_not_configured"),
    });
    expect(state.promises.phase).toBe("failed");
    if (state.promises.phase === "failed") {
      expect(state.promises.failure.code).toBe("ledger_root_not_configured");
      expect(state.promises.failure.text).toContain("No ledger folder is in use.");
    }
  });

  it("the proxy's too_large is the page-too-large state, any other proxy error is a refusal", () => {
    const tooLarge = reduceLedger(INITIAL_LEDGER_STATE, {
      type: "promises_result",
      filter: INITIAL_FILTER,
      result: { kind: "proxy_error", error: "too_large" },
    });
    expect(tooLarge.promises).toEqual({ phase: "too_large" });
    const timeout = reduceLedger(INITIAL_LEDGER_STATE, {
      type: "promises_result",
      filter: INITIAL_FILTER,
      result: { kind: "proxy_error", error: "timeout" },
    });
    expect(timeout.promises.phase).toBe("failed");
  });

  it("an invalid response and an unusable ledger are refusals with their own codes", () => {
    const invalid = reduceLedger(INITIAL_LEDGER_STATE, {
      type: "promises_result",
      filter: INITIAL_FILTER,
      result: { kind: "invalid_response" },
    });
    expect(invalid.promises).toMatchObject({ phase: "failed", failure: { code: "invalid_response" } });
    const unusable = reduceLedger(INITIAL_LEDGER_STATE, {
      type: "promises_result",
      filter: INITIAL_FILTER,
      result: errorOf("ledger_promises", 503, "ledger_unusable"),
    });
    expect(unusable.promises).toMatchObject({ phase: "failed", failure: { code: "ledger_unusable" } });
  });

  it("a 409 busy is a refusal in the busy category", () => {
    const state = reduceLedger(INITIAL_LEDGER_STATE, {
      type: "promises_result",
      filter: INITIAL_FILTER,
      result: errorOf("ledger_promises", 409, "run_in_progress"),
    });
    expect(state.promises).toMatchObject({ phase: "failed", failure: { category: "busy" } });
  });

  it("a late answer for an older filter changes nothing", () => {
    const current = reduceLedger(listed(), { type: "filter_changed", filter: { ...INITIAL_FILTER, page: 2 } });
    const after = reduceLedger(current, {
      type: "promises_result",
      filter: INITIAL_FILTER,
      result: errorOf("ledger_promises", 503, "ledger_root_not_configured"),
    });
    expect(after).toBe(current);
  });
});

describe("the customers view", () => {
  it("lists customers, and shows not configured as a refusal rather than an empty list", () => {
    const ok = reduceLedger(INITIAL_LEDGER_STATE, {
      type: "customers_result",
      result: replyOf("ledger_customers", 200, [customer()]),
    });
    expect(ok.customers).toMatchObject({ phase: "listed" });
    const refused = reduceLedger(INITIAL_LEDGER_STATE, {
      type: "customers_result",
      result: errorOf("ledger_customers", 503, "ledger_root_not_configured"),
    });
    expect(refused.customers).toMatchObject({ phase: "failed", failure: { code: "ledger_root_not_configured" } });
  });

  it("finds a customer's name by id, or says there is none", () => {
    expect(customerNameOf([customer()], CUSTOMER_ID)).toBe("Acme Test Co");
    expect(customerNameOf([customer()], "ffffffff000000000000000000000000")).toBeNull();
  });
});

describe("the void write", () => {
  it("promise_voided and promise_unknown each have their own words", () => {
    const voided = reduceLedger(INITIAL_LEDGER_STATE, {
      type: "write_result",
      kind: "void",
      result: errorOf("ledger_void", 422, "recall_refused", { code: "promise_voided" }),
    });
    const unknown = reduceLedger(INITIAL_LEDGER_STATE, {
      type: "write_result",
      kind: "void",
      result: errorOf("ledger_void", 422, "ledger_refused", { code: "promise_unknown" }),
    });
    expect(voided.write).toMatchObject({ phase: "refused", failure: { code: "recall_refused:promise_voided" } });
    expect(unknown.write).toMatchObject({ phase: "refused", failure: { code: "ledger_refused:promise_unknown" } });
    if (voided.write.phase === "refused" && unknown.write.phase === "refused") {
      expect(voided.write.failure.text).not.toBe(unknown.write.failure.text);
    }
  });

  it("a success is done and clears the chosen reasons", () => {
    const chosen = reduceLedger(INITIAL_LEDGER_STATE, {
      type: "reason_chosen",
      promiseId: "00000000-0000-4000-8000-000000000001",
      reason: "other",
    });
    expect(Object.keys(chosen.reasons)).toHaveLength(1);
    const after = reduceLedger(chosen, {
      type: "write_result",
      kind: "void",
      result: replyOf("ledger_void", 200, {
        promise_id: "00000000-0000-4000-8000-000000000001",
        event_id: 4,
        kind: "voided",
      }),
    });
    expect(after.write).toEqual({ phase: "done", kind: "void" });
    expect(after.reasons).toEqual({});
  });

  it("choosing no reason removes the choice", () => {
    const id = "00000000-0000-4000-8000-000000000001";
    const chosen = reduceLedger(INITIAL_LEDGER_STATE, { type: "reason_chosen", promiseId: id, reason: "other" });
    expect(reduceLedger(chosen, { type: "reason_chosen", promiseId: id, reason: null }).reasons).toEqual({});
  });

  it("every closed reason has its own non-empty label", () => {
    const labels = VOID_REASONS.map(voidReasonLabel);
    expect(new Set(labels).size).toBe(VOID_REASONS.length);
    for (const label of labels) {
      expect(label.length).toBeGreaterThan(0);
    }
  });
});

describe("integerCounts", () => {
  it("keeps integer counts under plain code words and drops everything else", () => {
    expect(integerCounts({ promises: 2, cap: 1 })).toEqual([
      ["promises", 2],
      ["cap", 1],
    ]);
    expect(integerCounts({ "bad key!": 2, ok: 1.5, fine: 3 })).toEqual([["fine", 3]]);
    expect(integerCounts(undefined)).toEqual([]);
  });
});
