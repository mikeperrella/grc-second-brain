// The only three ledger writes the window can make: void a promise, acknowledge a recall state and
// import a reviewed draft (ADR-022 decisions 1 and 4; docs/DESIGN.md 11.2). Pure: they take
// `invoke` as an argument.
//
// Each function needs a `Confirmed` value, which only the confirm reducer (confirm.ts) produces,
// and it takes every field of the request from that value, so the request that is sent is always
// the target the person confirmed. These are the only places the route names `ledger_void` and
// `ledger_acknowledge`, and `ledger_import` with `preview: false`, appear in the window
// (scripts/ledgerSources.test.mjs scans for it). The import preview (`preview: true`, which writes
// nothing) is built in ledgerImport.ts.

import type { Confirmed } from "./confirm";
import { callRoute, type CallResult, type Invoke, type VOID_REASONS } from "./ipc/proxy";
import type { ImportTarget } from "./ledgerImport";

export type VoidReason = (typeof VOID_REASONS)[number];

/** What the second step of a void confirms. The customer name and path are shown, never sent. */
export interface VoidTarget {
  promiseId: string;
  reason: VoidReason;
  customerName: string | null;
  sourcePath: string;
}

/** What the second step of an acknowledgement confirms. */
export interface AcknowledgeTarget {
  promiseId: string;
  /** The `recall_event_id` of the item the person saw, as the reply gave it. Never guessed. */
  seenRecallEventId: number;
  sourcePath: string;
}

/** Void one promise. The body carries only the promise id and the closed reason. */
export function voidPromise(
  invoke: Invoke,
  confirmed: Confirmed<VoidTarget>,
): Promise<CallResult<"ledger_void">> {
  const { promiseId, reason } = confirmed.target;
  return callRoute<"ledger_void">(invoke, { route: "ledger_void", promise_id: promiseId, reason });
}

/** Acknowledge the recall state the person saw (`seen_recall_event_id`). */
export function acknowledgePromise(
  invoke: Invoke,
  confirmed: Confirmed<AcknowledgeTarget>,
): Promise<CallResult<"ledger_acknowledge">> {
  const { promiseId, seenRecallEventId } = confirmed.target;
  return callRoute<"ledger_acknowledge">(invoke, {
    route: "ledger_acknowledge",
    promise_id: promiseId,
    seen_recall_event_id: seenRecallEventId,
  });
}

/**
 * The real import: exactly the fields the successful preview sent (ImportTarget.fields), with
 * `preview: false`. It can create promises and a customer, and neither can be removed.
 */
export function importPromises(
  invoke: Invoke,
  confirmed: Confirmed<ImportTarget>,
): Promise<CallResult<"ledger_import">> {
  return callRoute<"ledger_import">(invoke, { route: "ledger_import", ...confirmed.target.fields, preview: false });
}
