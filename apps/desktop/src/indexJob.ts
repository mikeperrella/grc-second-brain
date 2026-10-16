// State machine for "Index the vault" (M6_PLAN C.3, ADR-021 decision 6). Pure: no React, no invoke.
//
// Start is `vault_index_start` (202 `{job_id}`); progress is `vault_index_status` polled about once a
// second. Every refusal becomes fixed words (messages.ts). A `409 run_in_progress` is shown once as
// "another job is running": the state it leaves has no poll target, so nothing retries.

import type { CallResult } from "./ipc/proxy";
import { describeApiError, describeNonReply, safeCode, type Described } from "./messages";

export interface IndexProgress {
  filesDone: number;
  filesTotal: number;
  filesIndexed: number;
  filesUnchanged: number;
  filesRemoved: number;
  filesFailed: number;
  chunksEmbedded: number;
}

export type IndexOutcome = "complete" | "incomplete" | "failed";

export type IndexState =
  | { phase: "idle" }
  | { phase: "starting" }
  | {
      phase: "running";
      jobId: string;
      progress: IndexProgress;
      startedAtMs: number;
      /** The last status check failed; ALIBI keeps asking. A fixed message, never sidecar text. */
      problem: Described | null;
    }
  | {
      phase: "finished";
      outcome: IndexOutcome;
      progress: IndexProgress;
      /** A closed code word from the sidecar, or `null` (also `null` for anything that is not one). */
      failure: string | null;
    }
  /** A typed refusal of the start (busy, models, no vault, ...). Nothing is running. */
  | { phase: "refused"; failure: Described }
  /** `404` for the job id: the sidecar no longer knows it. */
  | { phase: "unknown_job" };

export type IndexEvent =
  | { type: "start_requested" }
  | { type: "start_result"; result: CallResult<"vault_index_start">; nowMs: number }
  | { type: "status_result"; result: CallResult<"vault_index_status"> };

export const EMPTY_PROGRESS: IndexProgress = {
  filesDone: 0,
  filesTotal: 0,
  filesIndexed: 0,
  filesUnchanged: 0,
  filesRemoved: 0,
  filesFailed: 0,
  chunksEmbedded: 0,
};

export const INITIAL_INDEX_STATE: IndexState = { phase: "idle" };

export function reduceIndex(state: IndexState, event: IndexEvent): IndexState {
  switch (event.type) {
    case "start_requested":
      // One start at a time. A running or starting job is never replaced.
      return state.phase === "starting" || state.phase === "running" ? state : { phase: "starting" };
    case "start_result": {
      if (state.phase !== "starting") {
        return state;
      }
      const result = event.result;
      if (result.kind !== "reply") {
        return { phase: "refused", failure: describeNonReply(result) };
      }
      const reply = result.reply;
      if (reply.status === 202) {
        return {
          phase: "running",
          jobId: reply.body.job_id,
          progress: EMPTY_PROGRESS,
          startedAtMs: event.nowMs,
          problem: null,
        };
      }
      return { phase: "refused", failure: describeApiError(reply.body) };
    }
    case "status_result": {
      if (state.phase !== "running") {
        return state;
      }
      const result = event.result;
      if (result.kind !== "reply") {
        return { ...state, problem: describeNonReply(result) };
      }
      const reply = result.reply;
      if (reply.status === 404) {
        // The only 404 this route sends is an unknown job id.
        return { phase: "unknown_job" };
      }
      if (reply.status !== 200) {
        return { ...state, problem: describeApiError(reply.body) };
      }
      const body = reply.body;
      const progress: IndexProgress = {
        filesDone: body.files_done,
        filesTotal: body.files_total,
        filesIndexed: body.files_indexed,
        filesUnchanged: body.files_unchanged,
        filesRemoved: body.files_removed,
        filesFailed: body.files_failed,
        chunksEmbedded: body.chunks_embedded,
      };
      if (body.status === "running") {
        return { ...state, progress, problem: null };
      }
      return {
        phase: "finished",
        outcome: body.status,
        progress,
        failure: body.failure === null ? null : safeCode(body.failure),
      };
    }
  }
}

/** The job id to poll, or `null` when nothing should be polled. */
export function indexPollTarget(state: IndexState): string | null {
  return state.phase === "running" ? state.jobId : null;
}
