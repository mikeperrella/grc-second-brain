// State for the review queue (ADR-021 decisions 3 to 7, docs/DESIGN.md 4 and 5). Pure: no React,
// no invoke.
//
// Flow: list the draft names in the exports folder (`folder_list_files`, kind exports) → choose one
// → read it (`draft_rows`, read only) → show the rows. The queue only reads: nothing here records
// an approval or writes a workbook (ADR-021 decision 7).
//
// Every verdict shown is the run record's: `status`, `reasons` and `citation` come from the
// sidecar's record and are never taken from a cell. The edited-cell flags only say that a cell
// differs from what ALIBI wrote.

import type { FileList, FolderCall } from "./ipc/folders";
import type { CallResult, DraftRow } from "./ipc/proxy";
import {
  describeApiError,
  describeFolderFailure,
  describeNonReply,
  fixedErrorMessage,
  safeCode,
  type Described,
} from "./messages";
import { describeStatus } from "./status";

export type { DraftRow };

// ----- the state ------------------------------------------------------------------------------

export type QueueFilesState =
  | { phase: "idle" }
  | { phase: "loading" }
  /** Includes "no exports folder is in use": never shown as an empty list. */
  | { phase: "failed"; failure: Described }
  | { phase: "listed"; files: readonly string[]; truncated: boolean };

export interface DraftHeader {
  model: string;
  fileName: string;
  complete: boolean;
  questionCount: number;
}

export type DraftState =
  | { phase: "none" }
  | { phase: "loading" }
  /** A whole-queue refusal: no rows at all. `counts` holds integers only (question_mismatch). */
  | { phase: "refused"; failure: Described; counts: readonly (readonly [string, number])[] }
  | { phase: "loaded"; header: DraftHeader; rows: readonly DraftRow[] };

export interface ReviewQueueState {
  files: QueueFilesState;
  selected: string | null;
  draft: DraftState;
}

export const INITIAL_REVIEW_QUEUE_STATE: ReviewQueueState = {
  files: { phase: "idle" },
  selected: null,
  draft: { phase: "none" },
};

export type ReviewQueueEvent =
  | { type: "files_requested" }
  | { type: "files_result"; call: FolderCall<FileList> }
  | { type: "file_selected"; file: string }
  /** Ask for the selected file's draft. The hook sends the call only when a file is selected. */
  | { type: "draft_requested" }
  /** `file` is the file the call was made for, so a late answer can be recognised. */
  | { type: "draft_result"; file: string; result: CallResult<"draft_rows"> };

// ----- ordering and per-row rules -------------------------------------------------------------

/**
 * 0: edited-status rows, NEEDS_REVIEW and any unknown status (an error block, so it is read first);
 * 1: PARTIAL; 2: SUPPORTED (ADR-021 decision 7).
 */
export function riskRank(row: DraftRow): 0 | 1 | 2 {
  if (row.status_edited) {
    return 0;
  }
  const description = describeStatus(row.status);
  if (description.kind === "unknown") {
    return 0;
  }
  switch (description.status) {
    case "NEEDS_REVIEW":
      return 0;
    case "PARTIAL":
      return 1;
    case "SUPPORTED":
      return 2;
  }
}

/** Rows ordered by risk, then by row number. A new array: the input is not changed. */
export function orderRows(rows: readonly DraftRow[]): DraftRow[] {
  return [...rows].sort((a, b) => riskRank(a) - riskRank(b) || a.row - b.row);
}

/**
 * The fixed review sentence goes on every SUPPORTED and PARTIAL row (ADR-021 decision 7), and on
 * any other row that carries a quote, because a quote is what the sentence is about.
 */
export function showsReviewSentence(row: DraftRow): boolean {
  const description = describeStatus(row.status);
  if (description.kind === "known" && description.status !== "NEEDS_REVIEW") {
    return true;
  }
  return row.citation !== null;
}

/** A reason code as a plain code word, or `null` (the screen then says "unrecognised reason"). */
export function reasonCode(value: string): string | null {
  return safeCode(value);
}

// ----- the reducer ----------------------------------------------------------------------------

function exportsNotConfigured(): Described {
  return {
    category: "error",
    text: fixedErrorMessage("export_root_not_configured"),
    code: "export_root_not_configured",
    models: [],
    showNoDownload: false,
  };
}

/** Integer counts under plain code-word keys only; anything else is dropped. */
function safeCounts(
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

export function reduceReviewQueue(
  state: ReviewQueueState,
  event: ReviewQueueEvent,
): ReviewQueueState {
  switch (event.type) {
    case "files_requested":
      return { ...state, files: { phase: "loading" } };
    case "files_result": {
      const call = event.call;
      if (call.kind !== "ok") {
        const failure =
          call.kind === "folder_error" && call.error === "not_configured"
            ? exportsNotConfigured()
            : describeFolderFailure(call);
        return { ...state, files: { phase: "failed", failure } };
      }
      const listed: QueueFilesState = {
        phase: "listed",
        files: call.value.files,
        truncated: call.value.truncated,
      };
      if (state.selected !== null && call.value.files.includes(state.selected)) {
        return { ...state, files: listed };
      }
      // The chosen file is gone from the folder: forget it and anything read from it.
      return { ...state, files: listed, selected: null, draft: { phase: "none" } };
    }
    case "file_selected":
      if (state.selected === event.file) {
        return state;
      }
      // Rows of one file are never kept beside another file's name.
      return { ...state, selected: event.file, draft: { phase: "none" } };
    case "draft_requested":
      if (state.selected === null) {
        return state;
      }
      return { ...state, draft: { phase: "loading" } };
    case "draft_result": {
      // A late answer for a file that is no longer selected, or one nobody is waiting for, changes
      // nothing.
      if (state.selected !== event.file || state.draft.phase !== "loading") {
        return state;
      }
      const result = event.result;
      if (result.kind !== "reply") {
        return {
          ...state,
          draft: { phase: "refused", failure: describeNonReply(result), counts: [] },
        };
      }
      const reply = result.reply;
      if (reply.status !== 200) {
        const counts = reply.body.error === "draft_refused" ? safeCounts(reply.body.counts) : [];
        return {
          ...state,
          draft: { phase: "refused", failure: describeApiError(reply.body), counts },
        };
      }
      const body = reply.body;
      return {
        ...state,
        draft: {
          phase: "loaded",
          header: {
            model: body.model,
            fileName: body.file_name,
            complete: body.complete,
            questionCount: body.question_count,
          },
          rows: orderRows(body.rows),
        },
      };
    }
  }
}
