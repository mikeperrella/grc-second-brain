// Helpers shared by the 65f tests. Not a test file itself. The base objects are read from the
// sidecar's RECORDED replies (fixtures/contracts/replies/*.json, synthetic data), so a test starts
// from a body the real schemas accept. No hostile text is written in this source except the one
// harmless canary marker below.

import customersFile from "../../../fixtures/contracts/replies/ledger_customers.json";
import promisesFile from "../../../fixtures/contracts/replies/ledger_promises.json";
import recallFile from "../../../fixtures/contracts/replies/ledger_recall.json";
import type { Customer, PromiseRow } from "./ledger";
import type { RecallItem, RecallReport } from "./recall";

interface Recorded {
  replies: readonly { case: string; status: number; body: unknown }[];
}

/** The recorded reply for one case of one route file. Throws if the case is missing. */
export function recorded(file: Recorded, name: string): { status: number; body: unknown } {
  const found = file.replies.find((reply) => reply.case === name);
  if (found === undefined) {
    throw new Error(`no recorded case ${name}`);
  }
  return { status: found.status, body: found.body };
}

/** A string that must show as plain text only, and never reach the console or a link. */
export const CANARY = 'CANARY-LEDGER-<a href="https://canary.invalid/x">link</a>-END';

export function customer(over: Partial<Customer> = {}): Customer {
  const base = (recorded(customersFile, "one_customer").body as Customer[])[0];
  if (base === undefined) {
    throw new Error("no recorded customer");
  }
  return { ...base, ...over };
}

export function promiseRow(over: Partial<PromiseRow> = {}): PromiseRow {
  const body = recorded(promisesFile, "page").body as { promises: PromiseRow[] };
  const base = body.promises[0];
  if (base === undefined) {
    throw new Error("no recorded promise");
  }
  return { ...base, ...over };
}

/** A recorded 200 `ledger_promises` body around the given promises. */
export function promisesBody(promises: PromiseRow[], total: number = promises.length) {
  return { page: 1, page_size: 50, total, promises };
}

export function recallItem(over: Partial<RecallItem> = {}): RecallItem {
  const body = recorded(recallFile, "intact").body as RecallReport;
  const base = body.items[0];
  if (base === undefined) {
    throw new Error("no recorded recall item");
  }
  return { ...base, ...over };
}

export function recallReport(items: RecallItem[], over: Partial<RecallReport> = {}): RecallReport {
  const base = recorded(recallFile, "intact").body as RecallReport;
  return { ...base, checked: items.length, events_recorded: 0, items, ...over };
}
