// State for the Ledger screen (ADR-022, docs/DESIGN.md 7 and 11). Pure: no React, no invoke.
//
// Flow: list the customers (`ledger_customers`) and one page of promises (`ledger_promises`) → the
// person may filter, page, or void a promise through the two-step confirm (confirm.ts,
// ledgerActions.ts). Every refusal is fixed words from messages.ts. A ledger that is not
// configured is a refusal, never "0" and never an empty list.
//
// `ledger_promises` is ALWAYS sent with include_voided, page and page_size (the host needs all
// four); `customer_id` is left out when no customer is chosen and is never null.

import {
  MAX_PAGE_SIZE,
  type CallResult,
  type RouteReply,
  type RouteRequest,
  type VOID_REASONS,
} from "./ipc/proxy";
import {
  VOID_REASON_IMPORTED_IN_ERROR,
  VOID_REASON_NOT_ACTUALLY_SENT,
  VOID_REASON_OTHER,
  VOID_REASON_WRONG_CUSTOMER,
} from "./ledgerText";
import {
  describeLedgerError,
  describeNonReply,
  safeCode,
  type Described,
} from "./messages";

type VoidReason = (typeof VOID_REASONS)[number];

type CustomersBody = Extract<RouteReply<"ledger_customers">, { status: 200 }>["body"];
type PromisesBody = Extract<RouteReply<"ledger_promises">, { status: 200 }>["body"];
export type Customer = CustomersBody[number];
export type PromiseRow = PromisesBody["promises"][number];

// ----- paging ---------------------------------------------------------------------------------

/**
 * The closed page sizes the screen offers. A starting value, not a measurement: the host accepts 1
 * to MAX_PAGE_SIZE and refuses a promises answer over its cap with `too_large`.
 */
export const PAGE_SIZES = [5, 10, 25, 50, 100] as const;
export const DEFAULT_PAGE_SIZE = 50;

export interface PromiseFilter {
  /** `null` means every customer: the request then has no `customer_id` at all. */
  customerId: string | null;
  includeVoided: boolean;
  page: number;
  pageSize: number;
}

export const INITIAL_FILTER: PromiseFilter = {
  customerId: null,
  includeVoided: false,
  page: 1,
  pageSize: DEFAULT_PAGE_SIZE,
};

export type FilterChange =
  | { kind: "customer"; customerId: string | null }
  | { kind: "include_voided"; includeVoided: boolean }
  | { kind: "page_size"; pageSize: number }
  | { kind: "page"; page: number };

/** A new filter. Any change except a page change goes back to page 1. Never changes the input. */
export function applyFilterChange(filter: PromiseFilter, change: FilterChange): PromiseFilter {
  switch (change.kind) {
    case "customer":
      return { ...filter, customerId: change.customerId, page: 1 };
    case "include_voided":
      return { ...filter, includeVoided: change.includeVoided, page: 1 };
    case "page_size":
      return { ...filter, pageSize: change.pageSize, page: 1 };
    case "page":
      return { ...filter, page: Math.max(1, change.page) };
  }
}

export function filterKey(filter: PromiseFilter): string {
  return `${filter.customerId ?? "-"}|${filter.includeVoided ? "v" : "-"}|${filter.page}|${filter.pageSize}`;
}

/** The request for a filter: all four query fields, and `customer_id` only when one is chosen. */
export function promisesRequest(filter: PromiseFilter): Extract<RouteRequest, { route: "ledger_promises" }> {
  return {
    route: "ledger_promises",
    include_voided: filter.includeVoided,
    page: filter.page,
    page_size: filter.pageSize,
    ...(filter.customerId === null ? {} : { customer_id: filter.customerId }),
  };
}

/** Page sizes smaller than the current one, for the "page too large" state. */
export function smallerPageSizes(current: number): readonly number[] {
  return PAGE_SIZES.filter((size) => size < current && size <= MAX_PAGE_SIZE);
}

/** Whether there is a page after this one, from the sidecar's total. */
export function hasNextPage(filter: PromiseFilter, total: number): boolean {
  return filter.page * filter.pageSize < total;
}

// ----- small pure helpers the screens share ---------------------------------------------------

/** Integer counts under plain code-word keys only; anything else is dropped. */
export function integerCounts(
  counts: Readonly<Record<string, number>> | undefined,
): readonly (readonly [string, number])[] {
  if (counts === undefined) {
    return [];
  }
  const out: (readonly [string, number])[] = [];
  for (const [key, value] of Object.entries(counts)) {
    if (safeCode(key) !== null && Number.isInteger(value)) {
      out.push([key, value]);
    }
  }
  return out;
}

/** The label of a closed void reason. No wildcard: a new reason breaks compilation here. */
export function voidReasonLabel(reason: VoidReason): string {
  switch (reason) {
    case "wrong_customer":
      return VOID_REASON_WRONG_CUSTOMER;
    case "imported_in_error":
      return VOID_REASON_IMPORTED_IN_ERROR;
    case "not_actually_sent":
      return VOID_REASON_NOT_ACTUALLY_SENT;
    case "other":
      return VOID_REASON_OTHER;
  }
}

/** The display name of a customer id, or `null` when the customer list has no such id. */
export function customerNameOf(customers: readonly Customer[], customerId: string): string | null {
  return customers.find((customer) => customer.customer_id === customerId)?.display_name ?? null;
}

/**
 * What a call that did not give a success body shows, as fixed words. `null` means the call gave
 * the 200 success body.
 */
export function failureOf<R extends "ledger_customers" | "ledger_promises" | "ledger_void" | "ledger_acknowledge" | "ledger_recall">(
  result: CallResult<R>,
): Described | null {
  if (result.kind !== "reply") {
    return describeNonReply(result);
  }
  const reply = result.reply as { status: number; body: unknown };
  if (reply.status === 200) {
    return null;
  }
  return describeLedgerError(reply.body as Parameters<typeof describeLedgerError>[0]);
}

// ----- the state ------------------------------------------------------------------------------

export type CustomersView =
  | { phase: "idle" }
  | { phase: "loading" }
  /** Includes "no ledger folder is in use": never shown as an empty list. */
  | { phase: "failed"; failure: Described }
  | { phase: "listed"; customers: readonly Customer[] };

export type PromisesView =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "failed"; failure: Described }
  /** The host refused an answer over its size cap (`too_large`): offer a smaller page size. */
  | { phase: "too_large" }
  | { phase: "listed"; total: number; promises: readonly PromiseRow[] };

export type WriteKind = "void" | "acknowledge";

export type WriteState =
  | { phase: "none" }
  | { phase: "sending"; kind: WriteKind }
  | { phase: "done"; kind: WriteKind }
  | { phase: "refused"; kind: WriteKind; failure: Described };

export interface LedgerState {
  customers: CustomersView;
  promises: PromisesView;
  filter: PromiseFilter;
  /** The void reason chosen per promise id (the picker, before step one). */
  reasons: Readonly<Record<string, VoidReason>>;
  write: WriteState;
}

export const INITIAL_LEDGER_STATE: LedgerState = {
  customers: { phase: "idle" },
  promises: { phase: "idle" },
  filter: INITIAL_FILTER,
  reasons: {},
  write: { phase: "none" },
};

export type LedgerEvent =
  | { type: "customers_requested" }
  | { type: "customers_result"; result: CallResult<"ledger_customers"> }
  | { type: "filter_changed"; filter: PromiseFilter }
  | { type: "promises_requested" }
  /** `filter` is the filter the call was made for, so a late answer can be recognised. */
  | { type: "promises_result"; filter: PromiseFilter; result: CallResult<"ledger_promises"> }
  | { type: "reason_chosen"; promiseId: string; reason: VoidReason | null }
  | { type: "write_started"; kind: WriteKind }
  | { type: "write_result"; kind: WriteKind; result: CallResult<"ledger_void" | "ledger_acknowledge"> };

export function reduceLedger(state: LedgerState, event: LedgerEvent): LedgerState {
  switch (event.type) {
    case "customers_requested":
      return { ...state, customers: { phase: "loading" } };
    case "customers_result": {
      const failure = failureOf(event.result);
      if (failure !== null) {
        return { ...state, customers: { phase: "failed", failure } };
      }
      if (event.result.kind !== "reply" || event.result.reply.status !== 200) {
        return state;
      }
      return { ...state, customers: { phase: "listed", customers: event.result.reply.body } };
    }
    case "filter_changed":
      return { ...state, filter: event.filter };
    case "promises_requested":
      return { ...state, promises: { phase: "loading" } };
    case "promises_result": {
      // A late answer for a filter that is no longer the current one changes nothing.
      if (filterKey(event.filter) !== filterKey(state.filter)) {
        return state;
      }
      const result = event.result;
      if (result.kind === "proxy_error" && result.error === "too_large") {
        return { ...state, promises: { phase: "too_large" } };
      }
      const failure = failureOf(result);
      if (failure !== null) {
        return { ...state, promises: { phase: "failed", failure } };
      }
      if (result.kind !== "reply" || result.reply.status !== 200) {
        return state;
      }
      const body = result.reply.body;
      return { ...state, promises: { phase: "listed", total: body.total, promises: body.promises } };
    }
    case "reason_chosen": {
      const reasons = { ...state.reasons };
      if (event.reason === null) {
        delete reasons[event.promiseId];
      } else {
        reasons[event.promiseId] = event.reason;
      }
      return { ...state, reasons };
    }
    case "write_started":
      return { ...state, write: { phase: "sending", kind: event.kind } };
    case "write_result": {
      const failure = failureOf(event.result);
      if (failure !== null) {
        return { ...state, write: { phase: "refused", kind: event.kind, failure } };
      }
      return { ...state, write: { phase: "done", kind: event.kind }, reasons: {} };
    }
  }
}
