// Thin hook: read the folder columns from the host once the sidecar is healthy (the host decides
// the folders as the sidecar starts, so `ready` is false until then) and re-read on each change of
// health. `refresh` re-reads on demand (after a folder was chosen).

import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { getFolders, type FolderCall, type FoldersReply } from "../ipc/folders";

export interface FoldersHandle {
  call: FolderCall<FoldersReply> | undefined;
  refresh: () => void;
}

export function useFolders(healthy: boolean): FoldersHandle {
  const [call, setCall] = useState<FolderCall<FoldersReply> | undefined>(undefined);
  const [tick, setTick] = useState(0);

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
  }, [healthy, tick]);

  const refresh = useCallback(() => setTick((n) => n + 1), []);

  return { call, refresh };
}
