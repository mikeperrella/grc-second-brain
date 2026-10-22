// Helpers shared by the 65j tests. Not a test file itself. The base objects are read from the
// sidecar's RECORDED replies (fixtures/contracts/replies/*.json, synthetic data), so a test starts
// from a body the real schemas accept. No hostile text is written in this source except the one
// harmless canary marker of ledgerTestSupport.ts.

import checkFile from "../../../fixtures/contracts/replies/reality_check.json";
import claimsFile from "../../../fixtures/contracts/replies/reality_claims.json";
import inspectFile from "../../../fixtures/contracts/replies/reality_inspect.json";
import { recorded } from "./ledgerTestSupport";
import {
  INITIAL_REALITY_STATE,
  reduceReality,
  type CheckReport,
  type ClaimCandidate,
  type ClaimsReport,
  type ExportInspection,
  type RealityEvent,
  type RealityState,
} from "./reality";
import { replyOf } from "./testSupport";

export const FILE = "okta-users-2026-10-01.csv";
export const OTHER_FILE = "okta-users-2026-09-01.csv";

/** The recorded inspect body (case `ok`) with fields replaced. */
export function inspectReport(over: Partial<ExportInspection> = {}): ExportInspection {
  return { ...(recorded(inspectFile, "ok").body as ExportInspection), ...over };
}

/** The recorded claims body (case `ok`) with fields replaced. */
export function claimsReport(over: Partial<ClaimsReport> = {}): ClaimsReport {
  return { ...(recorded(claimsFile, "ok").body as ClaimsReport), ...over };
}

/** The recorded claim candidate, with fields replaced. */
export function candidate(over: Partial<ClaimCandidate> = {}): ClaimCandidate {
  const base = claimsReport().candidates[0];
  if (base === undefined) {
    throw new Error("no recorded claim");
  }
  return { ...base, ...over };
}

/** The recorded check body (case `ok`) with fields replaced. */
export function checkReport(over: Partial<CheckReport> = {}): CheckReport {
  return { ...(recorded(checkFile, "ok").body as CheckReport), ...over };
}

/** The recorded reply of one case, as status and body. */
export function recordedCheck(name: string): { status: number; body: unknown } {
  return recorded(checkFile, name);
}
export function recordedInspect(name: string): { status: number; body: unknown } {
  return recorded(inspectFile, name);
}
export function recordedClaims(name: string): { status: number; body: unknown } {
  return recorded(claimsFile, name);
}

export function run(events: RealityEvent[], from: RealityState = INITIAL_REALITY_STATE): RealityState {
  return events.reduce(reduceReality, from);
}

/** The positions in the recorded inspect reply. */
export const TYPE_AT = { password: 0, oktaVerify: 1, email: 2, phone: 3, forgotten: 4 } as const;
export const STATUS_AT = { active: 0, deprovisioned: 1, staged: 2 } as const;

export function filesListed(files: string[] = [FILE, OTHER_FILE], truncated = false): RealityEvent[] {
  return [
    { type: "files_requested" },
    { type: "files_result", call: { kind: "ok", value: { kind: "imports", files, truncated } } },
  ];
}

export function claimsListed(report: ClaimsReport = claimsReport()): RealityEvent[] {
  return [
    { type: "claims_requested" },
    { type: "claims_result", result: replyOf("reality_claims", 200, report) },
  ];
}

export function inspected(file = FILE, report: ExportInspection = inspectReport()): RealityEvent[] {
  return [
    { type: "file_chosen", index: file === FILE ? 0 : 1 },
    { type: "inspect_result", file, result: replyOf("reality_inspect", 200, report) },
  ];
}

/** The export read and the claims listed, nothing chosen yet. */
export function readState(report: ExportInspection = inspectReport()): RealityState {
  return run([...filesListed(), ...claimsListed(), ...inspected(FILE, report)]);
}

/** Every choice made and the types NOT yet confirmed. */
export function chosenState(report: ExportInspection = inspectReport()): RealityState {
  return run(
    [
      { type: "input_changed", change: { kind: "mfa", index: TYPE_AT.oktaVerify } },
      { type: "input_changed", change: { kind: "status", index: STATUS_AT.active } },
      { type: "input_changed", change: { kind: "claim", index: 0 } },
      { type: "input_changed", change: { kind: "date", text: "2026-10-01" } },
    ],
    readState(report),
  );
}

/** Every choice made and the MFA types confirmed: a check can be sent. */
export function readyState(report: ExportInspection = inspectReport()): RealityState {
  return run([{ type: "input_changed", change: { kind: "confirm", confirmed: true } }], chosenState(report));
}

/** A state with the check sent and answered with `report` (the inspect hash is the recorded one). */
export function checkedState(report: CheckReport = checkReport(), from: RealityState = readyState()): RealityState {
  return run(
    [
      { type: "check_requested", generation: from.generation },
      { type: "check_result", generation: from.generation, result: replyOf("reality_check", 200, report) },
    ],
    from,
  );
}
