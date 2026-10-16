// Thin hook: read the folder columns from the host once the sidecar is healthy (the host decides
// the folders as the sidecar starts, so `ready` is false until then) and re-read on each change of
// health. The screen that edits folders is chunk 6g.

import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { getFolders, type FolderCall, type FoldersReply } from "../ipc/folders";

export function useFolders(healthy: boolean): FolderCall<FoldersReply> | undefined {
  const [call, setCall] = useState<FolderCall<FoldersReply> | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    void getFolders(invoke).then((result) => {
      if (!cancelled) {
        setCall(result);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [healthy]);

  return call;
}
