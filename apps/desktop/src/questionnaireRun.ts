// State for the questionnaire flow (M6_PLAN C.4, DESIGN.md 4.5 and 7). Pure: no React, no invoke.
//
// Flow: list the workbook names (`folder_list_files`) → choose one → inspect (read only) → choose
// the mapping → Start → poll `questionnaire_status` about once a second → finished.
//
// What the progress states may and may not say (ADR-020 decision 4): `completed` moves only when a
// question finishes; after the last answer `status` is still `running` while the file is written
// (shown as "writing the answers file"); a run the sidecar no longer knows is `404 run_not_found`.
// There is no ETA, no percent and no "stuck" anywhere in this file or the screen.

import type { FileList, FolderCall } from "./ipc/folders";
import type { CallResult } from "./ipc/proxy";
import {
  describeApiError,
  describeFolderFailure,
  describeNonReply,
  fixedErrorMessage,
  safeCode,
  type Described,
} from "./messages";
import { mappingFromSuggestion, type MappingDraft } from "./mapping";
import { secondsSince } from "./poller";
import { EVIDENCE_STATUSES } from "./status";

// ----- shapes copied from the strict reply schemas, so the screen needs no Zod ----------------

export interface SheetInfo {
  name: string;
  rows: number;
  columns: number;
}

export interface InspectInfo {
  sheets: readonly SheetInfo[];
  hasSuggestion: boolean;
  macroEnabled: boolean;
  warnings: readonly string[];
}

export interface RunSummary {
  model: string;
  complete: boolean;
  counts: Readonly<Record<string, number>>;
  reasonCounts: Readonly<Record<string, number>>;
  fileName: string | null;
  rowsWritten: number;
  droppedParts: ReadonlyArray<readonly [string, number, number]>;
  warnings: readonly string[];
  seconds: number;
}

// ----- the state ------------------------------------------------------------------------------

export type FilesState =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "failed"; failure: Described }
  | { phase: "listed"; files: readonly string[]; truncated: boolean };

export type InspectState =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "refused"; failure: Described }
  | { phase: "ready"; info: InspectInfo };

export type RunState =
  | { phase: "none" }
  | { phase: "starting" }
  | { phase: "refused"; failure: Described }
  | {
      phase: "running";
      runId: string;
      total: number;
      completed: number;
      /** When the run started or `completed` last changed, in ms. Only the local counter uses it. */
      lastChangeMs: number;
      problem: Described | null;
    }
  | {
      phase: "finished";
      outcome: "complete" | "stopped" | "failed";
      total: number;
      completed: number;
      /** A closed code word, or `null` (also `null` for anything that is not one). */
      failure: string | null;
      summary: RunSummary | null;
    }
  | { phase: "unknown_run" };

export interface QuestionnaireState {
  files: FilesState;
  selected: string | null;
  inspect: InspectState;
  mapping: MappingDraft | null;
  run: RunState;
}

export const INITIAL_QUESTIONNAIRE_STATE: QuestionnaireState = {
  files: { phase: "idle" },
  selected: null,
  inspect: { phase: "idle" },
  mapping: null,
  run: { phase: "none" },
};

export type QuestionnaireEvent =
  | { type: "files_requested" }
  | { type: "files_result"; call: FolderCall<FileList> }
  | { type: "file_selected"; file: string }
  | { type: "inspect_requested" }
  | { type: "inspect_result"; result: CallResult<"questionnaire_inspect"> }
  | { type: "mapping_changed"; mapping: MappingDraft }
  | { type: "start_requested" }
  | { type: "start_result"; result: CallResult<"questionnaire_start">; nowMs: number }
  | { type: "status_result"; result: CallResult<"questionnaire_status">; nowMs: number }
  | { type: "run_dismissed" };

/** A run is going (or about to): the file, mapping and Start controls are locked. */
export function runIsActive(run: RunState): boolean {
  return run.phase === "starting" || run.phase === "running";
}

export function reduceQuestionnaire(
  state: QuestionnaireState,
  event: QuestionnaireEvent,
): QuestionnaireState {
  switch (event.type) {
    case "files_requested":
      return { ...state, files: { phase: "loading" } };
    case "files_result": {
      const call = event.call;
      if (call.kind !== "ok") {
        // "No folder of this kind" is the questionnaires folder here, so say so (DESIGN.md 7).
        const failure =
          call.kind === "folder_error" && call.error === "not_configured"
            ? {
                category: "error" as const,
                text: fixedErrorMessage("questionnaire_root_not_configured"),
                code: "questionnaire_root_not_configured",
                models: [],
                showNoDownload: false,
              }
            : describeFolderFailure(call);
        return { ...state, files: { phase: "failed", failure } };
      }
      const listed: FilesState = {
        phase: "listed",
        files: call.value.files,
        truncated: call.value.truncated,
      };
      if (state.selected !== null && call.value.files.includes(state.selected)) {
        return { ...state, files: listed };
      }
      // The chosen file is gone from the folder: forget it and anything read from it.
      return { ...state, files: listed, selected: null, inspect: { phase: "idle" }, mapping: null };
    }
    case "file_selected":
      // Changing the file while a run is going would let the screen show one file's mapping beside
      // another's run, so it is ignored until the run ends.
      if (runIsActive(state.run)) {
        return state;
      }
      return {
        ...state,
        selected: event.file,
        inspect: { phase: "idle" },
        mapping: null,
        run: { phase: "none" },
      };
    case "inspect_requested":
      return state.selected === null || runIsActive(state.run)
        ? state
        : { ...state, inspect: { phase: "loading" }, mapping: null };
    case "inspect_result": {
      if (state.inspect.phase !== "loading") {
        return state;
      }
      const result = event.result;
      if (result.kind !== "reply") {
        return { ...state, inspect: { phase: "refused", failure: describeNonReply(result) } };
      }
      const reply = result.reply;
      if (reply.status !== 200) {
        return { ...state, inspect: { phase: "refused", failure: describeApiError(reply.body) } };
      }
      const body = reply.body;
      const info: InspectInfo = {
        sheets: body.sheets,
        hasSuggestion: body.suggestion !== null,
        macroEnabled: body.macro_enabled,
        warnings: body.warnings,
      };
      return {
        ...state,
        inspect: { phase: "ready", info },
        mapping: mappingFromSuggestion(body.suggestion, body.sheets),
      };
    }
    case "mapping_changed":
      return state.inspect.phase === "ready" && !runIsActive(state.run)
        ? { ...state, mapping: event.mapping }
        : state;
    case "start_requested":
      return state.inspect.phase === "ready" && state.mapping !== null && !runIsActive(state.run)
        ? { ...state, run: { phase: "starting" } }
        : state;
    case "start_result": {
      if (state.run.phase !== "starting") {
        return state;
      }
      const result = event.result;
      if (result.kind !== "reply") {
        return { ...state, run: { phase: "refused", failure: describeNonReply(result) } };
      }
      const reply = result.reply;
      if (reply.status !== 202) {
        return { ...state, run: { phase: "refused", failure: describeApiError(reply.body) } };
      }
      return {
        ...state,
        run: {
          phase: "running",
          runId: reply.body.run_id,
          total: reply.body.total,
          completed: 0,
          lastChangeMs: event.nowMs,
          problem: null,
        },
      };
    }
    case "status_result": {
      const run = state.run;
      if (run.phase !== "running") {
        return state;
      }
      const result = event.result;
      if (result.kind !== "reply") {
        return { ...state, run: { ...run, problem: describeNonReply(result) } };
      }
      const reply = result.reply;
      if (reply.status === 404) {
        return { ...state, run: { phase: "unknown_run" } };
      }
      if (reply.status !== 200) {
        return { ...state, run: { ...run, problem: describeApiError(reply.body) } };
      }
      const body = reply.body;
      if (body.status === "running") {
        return {
          ...state,
          run: {
            ...run,
            total: body.total,
            completed: body.completed,
            lastChangeMs: body.completed === run.completed ? run.lastChangeMs : event.nowMs,
            problem: null,
          },
        };
      }
      return {
        ...state,
        run: {
          phase: "finished",
          outcome: body.status,
          total: body.total,
          completed: body.completed,
          failure: body.failure === null ? null : safeCode(body.failure),
          summary:
            body.summary === null
              ? null
              : {
                  model: body.summary.model,
                  complete: body.summary.complete,
                  counts: body.summary.counts,
                  reasonCounts: body.summary.reason_counts,
                  fileName: body.summary.file_name,
                  rowsWritten: body.summary.rows_written,
                  droppedParts: body.summary.dropped_parts,
                  warnings: body.summary.warnings,
                  seconds: body.summary.seconds,
                },
        },
      };
    }
    case "run_dismissed":
      return runIsActive(state.run) ? state : { ...state, run: { phase: "none" } };
  }
}

// ----- derived facts the screen shows ---------------------------------------------------------

/** The run id to poll, or `null` when nothing should be polled. */
export function runPollTarget(state: QuestionnaireState): string | null {
  return state.run.phase === "running" ? state.run.runId : null;
}

/** After the last answer the status stays `running` while the file is written. */
export function isWritingFile(run: RunState): boolean {
  return run.phase === "running" && run.total > 0 && run.completed >= run.total;
}

/** Whole seconds since the last answer (or the start). A local counter, never an estimate. */
export function secondsSinceLastAnswer(run: RunState, nowMs: number): number | null {
  return run.phase === "running" ? secondsSince(nowMs, run.lastChangeMs) : null;
}

/**
 * The summary's per-status counts in a fixed order: the three known statuses first, then any other
 * key (which the screen shows as an unknown-status block, never as a label).
 */
export function orderedCounts(
  counts: Readonly<Record<string, number>>,
): ReadonlyArray<{ key: string; count: number }> {
  const known = EVIDENCE_STATUSES.filter((status) => status in counts);
  const other = Object.keys(counts)
    .filter((key) => !(EVIDENCE_STATUSES as readonly string[]).includes(key))
    .sort();
  return [...known, ...other].map((key) => ({ key, count: counts[key] ?? 0 }));
}

/** The summary warnings that mean no run record exists (`run_record_not_kept`, `run_record_failed`). */
export const NO_RECORD_WARNINGS = ["run_record_not_kept", "run_record_failed"] as const;

export function runRecordMissing(summary: RunSummary | null): boolean {
  return summary !== null && summary.warnings.some((w) => (NO_RECORD_WARNINGS as readonly string[]).includes(w));
}
