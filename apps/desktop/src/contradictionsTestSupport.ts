// Helpers shared by the 65i tests. Not a test file itself. The base objects are read from the
// sidecar's RECORDED replies (fixtures/contracts/replies/*.json, synthetic data), so a test starts
// from a body the real schemas accept. No hostile text is written in this source.

import startFile from "../../../fixtures/contracts/replies/contradictions_start.json";
import statusFile from "../../../fixtures/contracts/replies/contradictions_status.json";
import type { ContradictionReport, ContradictionsEvent, ContradictionsState, Finding } from "./contradictions";
import { INITIAL_CONTRADICTIONS_STATE, reduceContradictions } from "./contradictions";
import type { Invoke } from "./ipc/proxy";
import { recorded } from "./ledgerTestSupport";
import { replyOf } from "./testSupport";

/** The run id of the recorded `accepted` start reply (and of the recorded `complete` status). */
export const RUN_ID = "00000001000000000000000000000000";
/** Another run's id, for the late-answer tests. */
export const OTHER_RUN_ID = "00000002000000000000000000000000";

export function recordedStart(name: string): { status: number; body: unknown } {
  return recorded(startFile, name);
}

export function recordedStatus(name: string): { status: number; body: unknown } {
  return recorded(statusFile, name);
}

/** The recorded complete report (three VALUE_CONFLICT findings), with fields replaced. */
export function report(over: Partial<ContradictionReport> = {}): ContradictionReport {
  return { ...(recordedStatus("complete").body as { report: ContradictionReport }).report, ...over };
}

/** The first recorded finding with fields replaced. */
export function finding(over: Partial<Finding> = {}): Finding {
  const base = report().findings[0];
  if (base === undefined) {
    throw new Error("no recorded finding");
  }
  return { ...base, ...over };
}

/** A 200 status body for `runId` (default: the recorded running body), with fields replaced. */
export function statusBody(over: Record<string, unknown> = {}): Record<string, unknown> {
  return { ...(recordedStatus("running").body as Record<string, unknown>), run_id: RUN_ID, ...over };
}

export function startReply(runId: string = RUN_ID) {
  return replyOf("contradictions_start", 202, { run_id: runId });
}

export function statusReply(over: Record<string, unknown> = {}) {
  return replyOf("contradictions_status", 200, statusBody(over));
}

export function run(events: ContradictionsEvent[], from: ContradictionsState = INITIAL_CONTRADICTIONS_STATE) {
  return events.reduce(reduceContradictions, from);
}

/** A state with the run going (start answered with `runId`). */
export function running(runId: string = RUN_ID): ContradictionsState {
  return run([{ type: "start_requested" }, { type: "start_result", result: startReply(runId) }]);
}

/** A state showing the given report. */
export function complete(rep: ContradictionReport = report()): ContradictionsState {
  return run([{ type: "status_result", runId: RUN_ID, result: statusReply({ status: "complete", report: rep }) }], running());
}

/** A fake invoke that records every call and answers with `answer`. */
export function recording(answer: unknown = { status: 202, body: { run_id: RUN_ID } }) {
  const calls: { command: string; args: Record<string, unknown> }[] = [];
  const invoke: Invoke = (command, args) => {
    calls.push({ command, args });
    return Promise.resolve(answer);
  };
  return { invoke, calls };
}
