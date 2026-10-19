// Helpers shared by the 65h tests. Not a test file itself. The base objects are read from the
// sidecar's RECORDED replies (fixtures/contracts/replies/*.json, synthetic data), so a test starts
// from a body the real schemas accept. No hostile text is written in this source.

import blastFile from "../../../fixtures/contracts/replies/ledger_blast_radius.json";
import citedFile from "../../../fixtures/contracts/replies/ledger_cited_paths.json";
import type { BlastItem, BlastReport, CitedPath } from "./blast";
import { recorded } from "./ledgerTestSupport";

/** A recorded blast item (case `proposed_text`) with fields replaced. */
export function blastItem(over: Partial<BlastItem> = {}): BlastItem {
  const base = (recorded(blastFile, "proposed_text").body as BlastReport).items[0];
  if (base === undefined) {
    throw new Error("no recorded blast item");
  }
  return { ...base, ...over };
}

/** A recorded 200 blast report (case `proposed_text`) around the given items. */
export function blastReport(items: BlastItem[], over: Partial<BlastReport> = {}): BlastReport {
  const base = recorded(blastFile, "proposed_text").body as BlastReport;
  return { ...base, checked: items.length, items, ...over };
}

/** The recorded blast reply of one case, as status and body. */
export function recordedBlast(name: string): { status: number; body: unknown } {
  return recorded(blastFile, name);
}

/** The recorded `ledger_cited_paths` paths of one 200 case. */
export function citedPaths(name: "empty" | "one_path" | "two_paths"): CitedPath[] {
  return (recorded(citedFile, name).body as { paths: CitedPath[] }).paths;
}

/** The recorded `ledger_cited_paths` reply of one case, as status and body. */
export function recordedCited(name: string): { status: number; body: unknown } {
  return recorded(citedFile, name);
}
