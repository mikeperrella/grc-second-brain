// State for the Folders screen (M6_PLAN C.2, DESIGN.md 4.5 "Folder row"). Pure: no React, no invoke.
//
// Two layers:
//   * `foldersScreenView` turns what the host said (`folders_get`) into rows, in the recommended
//     order: vault, ledger, questionnaires, exports, imports.
//   * `reducePick` is the small state machine for choosing a folder. The window only ever names a
//     KIND; it never holds or sends a path (ADR-020 decision 5). Paths here are display strings
//     that the screen shows as plain text.

import {
  type FolderCall,
  type FolderKind,
  type FolderProblem,
  type FoldersReply,
  type PickReply,
} from "./ipc/folders";
import { describeFolderFailure, type Described } from "./messages";

/** The order of the rows (M6_PLAN B.2 item 8). The same five kinds as FOLDER_KINDS. */
export const FOLDER_ROW_ORDER = [
  "vault",
  "ledger",
  "questionnaires",
  "exports",
  "imports",
] as const satisfies readonly FolderKind[];

export function folderLabel(kind: FolderKind): string {
  switch (kind) {
    case "vault":
      return "Vault";
    case "ledger":
      return "Ledger";
    case "questionnaires":
      return "Questionnaires";
    case "exports":
      return "Exports";
    case "imports":
      return "Imports";
  }
}

export interface FolderRowView {
  kind: FolderKind;
  label: string;
  inUse: string | null;
  saved: string | null;
  /** True when what is saved is not what is in use, so both columns are shown separately. */
  differs: boolean;
  problem: FolderProblem | null;
  startProblem: FolderProblem | null;
  /** The ledger row is marked "needed for the review queue". */
  neededForQueue: boolean;
}

export type FoldersScreenView =
  /** The first read has not returned. */
  | { state: "loading" }
  /** The host has not decided the folders yet (the sidecar is still starting). */
  | { state: "not_ready" }
  | { state: "unavailable"; failure: Described }
  | {
      state: "loaded";
      rows: readonly FolderRowView[];
      /** The saved file cannot be read now: every saved folder is ignored. */
      savedFileInvalid: boolean;
      /** The saved file was unreadable at this start: nothing saved was used. */
      ignoredAtStart: boolean;
    };

export function foldersScreenView(call: FolderCall<FoldersReply> | undefined): FoldersScreenView {
  if (call === undefined) {
    return { state: "loading" };
  }
  if (call.kind !== "ok") {
    return { state: "unavailable", failure: describeFolderFailure(call) };
  }
  if (!call.value.ready) {
    return { state: "not_ready" };
  }
  const rows: FolderRowView[] = FOLDER_ROW_ORDER.map((kind) => {
    const view = call.value.folders.find((folder) => folder.kind === kind);
    const inUse = view?.in_use ?? null;
    const saved = view?.saved ?? null;
    return {
      kind,
      label: folderLabel(kind),
      inUse,
      saved,
      differs: inUse !== saved,
      problem: view?.problem ?? null,
      startProblem: view?.start_problem ?? null,
      neededForQueue: kind === "ledger",
    };
  });
  return {
    state: "loaded",
    rows,
    savedFileInvalid: call.value.saved_file === "invalid",
    ignoredAtStart: call.value.saved_file_at_start === "invalid",
  };
}

/** Whether a ledger folder is in use right now: `null` while the host has not said. */
export function ledgerInUse(call: FolderCall<FoldersReply> | undefined): boolean | null {
  if (call === undefined || call.kind !== "ok" || !call.value.ready) {
    return null;
  }
  return folderInUse(call, "ledger");
}

/** Whether a folder of this kind is in use right now: `null` while the host has not said. */
export function folderInUse(
  call: FolderCall<FoldersReply> | undefined,
  kind: FolderKind,
): boolean | null {
  if (call === undefined || call.kind !== "ok" || !call.value.ready) {
    return null;
  }
  const inUse = call.value.folders.find((folder) => folder.kind === kind)?.in_use ?? null;
  return inUse !== null;
}

// ----- choosing a folder ----------------------------------------------------------------------

export type PickState =
  | { phase: "idle" }
  | { phase: "picking"; kind: FolderKind }
  | { phase: "saved"; kind: FolderKind; display: string }
  | { phase: "cancelled" }
  | { phase: "failed"; failure: Described };

export type PickEvent =
  | { type: "start"; kind: FolderKind }
  | { type: "result"; call: FolderCall<PickReply> };

export function reducePick(state: PickState, event: PickEvent): PickState {
  switch (event.type) {
    case "start":
      // One dialog at a time: a second start while one is open changes nothing.
      return state.phase === "picking" ? state : { phase: "picking", kind: event.kind };
    case "result": {
      if (state.phase !== "picking") {
        return state;
      }
      const call = event.call;
      if (call.kind !== "ok") {
        return { phase: "failed", failure: describeFolderFailure(call) };
      }
      return call.value.outcome === "saved"
        ? { phase: "saved", kind: call.value.kind, display: call.value.display }
        : { phase: "cancelled" };
    }
  }
}
