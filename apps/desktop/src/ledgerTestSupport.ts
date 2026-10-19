// Helpers shared by the 65f tests. Not a test file itself. The base objects are read from the
// sidecar's RECORDED replies (fixtures/contracts/replies/*.json, synthetic data), so a test starts
// from a body the real schemas accept. No hostile text is written in this source except the one
// harmless canary marker below.

import draftFile from "../../../fixtures/contracts/replies/draft_rows.json";
import importFile from "../../../fixtures/contracts/replies/ledger_import.json";
import customersFile from "../../../fixtures/contracts/replies/ledger_customers.json";
import promisesFile from "../../../fixtures/contracts/replies/ledger_promises.json";
import recallFile from "../../../fixtures/contracts/replies/ledger_recall.json";
import type { Customer, PromiseRow } from "./ledger";
import type { ImportReport } from "./ledgerImport";
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

/** A recorded `ledger_import` 200 body (case `preview` or `created`), with fields replaced. */
export function importReport(name: "preview" | "created" | "preview_again", over: Partial<ImportReport> = {}): ImportReport {
  return { ...(recorded(importFile, name).body as ImportReport), ...over };
}

/** The recorded `ledger_import` reply of one case, as status and body. */
export function recordedImport(name: string): { status: number; body: unknown } {
  return recorded(importFile, name);
}

/** The recorded `draft_rows` 200 body (case `ok`): its `run_id` is the full id. */
export function draftBody(): { run_id: string; file_name: string; question_count: number } & Record<string, unknown> {
  return recorded(draftFile, "ok").body as { run_id: string; file_name: string; question_count: number };
}
