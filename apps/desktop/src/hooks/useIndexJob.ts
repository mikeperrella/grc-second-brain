// Thin hook: start an index job and poll its status about once a second (ADR-020 decision 4).
//
// Polling runs ONLY while health is `healthy` and a job is running. It stops when the job ends,
// when health leaves `healthy`, and when the component using the hook unmounts (the effect cleanup
// calls the stop function). A start refused with 409 leaves a state with no poll target, so
// nothing retries.

import { useCallback, useEffect, useReducer } from "react";
import { invoke } from "@tauri-apps/api/core";
import {
  INITIAL_INDEX_STATE,
  indexPollTarget,
  reduceIndex,
  type IndexState,
} from "../indexJob";
import { callRoute } from "../ipc/proxy";
import { pollTargetWhen, startPolling } from "../poller";

export function useIndexJob(healthy: boolean): { state: IndexState; start: () => void } {
  const [state, dispatch] = useReducer(reduceIndex, INITIAL_INDEX_STATE);
  const target = pollTargetWhen(healthy, indexPollTarget(state));

  useEffect(() => {
    if (target === null) {
      return undefined;
    }
    return startPolling(
      () => callRoute<"vault_index_status">(invoke, { route: "vault_index_status", job_id: target }),
      (result) => dispatch({ type: "status_result", result }),
    );
  }, [target]);

  const start = useCallback(() => {
    dispatch({ type: "start_requested" });
    void callRoute<"vault_index_start">(invoke, { route: "vault_index_start" }).then((result) =>
      dispatch({ type: "start_result", result, nowMs: Date.now() }),
    );
  }, []);

  return { state, start };
}
