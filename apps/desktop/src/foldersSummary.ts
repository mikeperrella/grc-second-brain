// The one-line "folders configured" summary for the status bar (M6_PLAN C.1). The full two-column
// screen is chunk 6g. Words, never "0 results": a folder that is not set says so, and a folder that
// is saved but not yet in use says it applies at the next launch (ADR-020 decision 5).

import { FOLDER_KINDS, type FolderCall, type FoldersReply, type FolderKind } from "./ipc/folders";

export type FoldersSummary =
  /** The host has not decided the folders yet, or the call has not returned. */
  | { state: "unknown" }
  /** The call failed; `code` is a fixed word. */
  | { state: "unavailable"; code: string }
  | {
      state: "known";
      inUse: number;
      total: number;
      /** Saved for the next launch but not in use now. */
      pendingNextLaunch: readonly FolderKind[];
      /** Not in use and nothing saved. */
      notSet: readonly FolderKind[];
      /** The saved folder is invalid (a typed host reason exists). */
      invalid: readonly FolderKind[];
    };

export function summarizeFolders(call: FolderCall<FoldersReply> | undefined): FoldersSummary {
  if (call === undefined) {
    return { state: "unknown" };
  }
  switch (call.kind) {
    case "folder_error":
      return { state: "unavailable", code: call.error };
    case "invalid_response":
      return { state: "unavailable", code: "invalid_response" };
    case "rejected":
      return { state: "unavailable", code: "rejected" };
    case "ok": {
      if (!call.value.ready) {
        return { state: "unknown" };
      }
      const pendingNextLaunch: FolderKind[] = [];
      const notSet: FolderKind[] = [];
      const invalid: FolderKind[] = [];
      let inUse = 0;
      for (const kind of FOLDER_KINDS) {
        const view = call.value.folders.find((folder) => folder.kind === kind);
        if (view === undefined) {
          notSet.push(kind);
          continue;
        }
        if (view.problem !== null || view.start_problem !== null) {
          invalid.push(kind);
        }
        if (view.in_use !== null) {
          inUse += 1;
        } else if (view.saved !== null) {
          pendingNextLaunch.push(kind);
        } else {
          notSet.push(kind);
        }
      }
      return {
        state: "known",
        inUse,
        total: FOLDER_KINDS.length,
        pendingNextLaunch,
        notSet,
        invalid,
      };
    }
  }
}
