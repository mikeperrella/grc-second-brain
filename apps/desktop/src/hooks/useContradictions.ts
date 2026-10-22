// Thin hook: the Contradictions screen (chunk 65i). It starts a run and polls its status about once
// a second (ADR-020 decision 4). Both routes are named only in contradictions.ts (`sendStart`,
// `pollStatus`); every decision (what to poll, when to stop, which answers to drop) is made by the
// pure reducer there.
//
// Polling runs ONLY while health is `healthy` and a run is going. It stops when the run reaches a
// terminal status, when health leaves `healthy` (and starts again if health returns while the run
// is still `running`), and when the component using the hook unmounts (the effect cleanup calls
// the stop function). A start refused with 409 leaves a state with no poll target, so nothing
// retries.
//
// Hooks and click handlers are rendered in no test (threat model F6-1c).

import { useCallback, useEffect, useReducer, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import {
  INITIAL_CONTRADICTIONS_STATE,
  contradictionPollTarget,
  createStartGate,
  pollStatus,
  reduceContradictions,
  sendStart,
  type ContradictionsState,
} from "../contradictions";
import { pollTargetWhen, startPolling } from "../poller";

export interface ContradictionsHandle {
  state: ContradictionsState;
  changeInput: (field: "scope" | "cap", value: string) => void;
  /** A no-op while an input problem stops it or a run is going: nothing is sent then. */
  start: () => void;
}

export function useContradictions(healthy: boolean): ContradictionsHandle {
  const [state, dispatch] = useReducer(reduceContradictions, INITIAL_CONTRADICTIONS_STATE);
  const [gate] = useState(() => createStartGate());
  const target = pollTargetWhen(healthy, contradictionPollTarget(state));

  useEffect(() => {
    if (target === null) {
      return undefined;
    }
    return startPolling(
      () => pollStatus(invoke, target),
      (result) => dispatch({ type: "status_result", runId: target, result }),
    );
  }, [target]);

  const changeInput = useCallback((field: "scope" | "cap", value: string) => {
    dispatch({ type: "input_changed", field, value });
  }, []);

  const start = useCallback(() => {
    // The gate keeps its own flag, so a second click before the render that disables the button
    // cannot send a second start request.
    if (!gate.tryBegin()) {
      return;
    }
    if (contradictionPollTarget(state) !== null || state.run.phase === "starting") {
      gate.end();
      return;
    }
    const sent = sendStart(invoke, state.inputs);
    if (sent === null) {
      gate.end();
      return;
    }
    dispatch({ type: "start_requested" });
    void sent.then((result) => {
      dispatch({ type: "start_result", result });
      gate.end();
    });
  }, [state, gate]);

  return { state, changeInput, start };
}
