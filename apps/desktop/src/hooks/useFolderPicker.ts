// Thin hook: choose a folder of a named KIND. The window sends only the kind; the host opens its
// dialog, checks the folder and saves it (ADR-020 decisions 5 to 8). After a saved choice the
// folder columns are read again.

import { useCallback, useReducer } from "react";
import { invoke } from "@tauri-apps/api/core";
import { reducePick, type PickState } from "../foldersScreen";
import { pickFolder, type FolderKind } from "../ipc/folders";

export function useFolderPicker(refresh: () => void): {
  pick: PickState;
  choose: (kind: FolderKind) => void;
} {
  const [pick, dispatch] = useReducer(reducePick, { phase: "idle" } as PickState);

  const choose = useCallback(
    (kind: FolderKind) => {
      dispatch({ type: "start", kind });
      void pickFolder(invoke, kind).then((call) => {
        dispatch({ type: "result", call });
        if (call.kind === "ok" && call.value.outcome === "saved") {
          refresh();
        }
      });
    },
    [refresh],
  );

  return { pick, choose };
}
