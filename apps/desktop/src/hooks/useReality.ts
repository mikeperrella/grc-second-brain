// Thin hook: the Reality check screen (chunk 65j). Reading the Imports listing, the claims, the
// export (inspect) and the check are all sent by reality.ts (`listImports`, `sendClaims`,
// `sendInspect`, `sendCheck`); every decision (what is sent, which answers are dropped, what is
// cleared) is made by the pure reducer there. The hook names no route.
//
// A check is sent only after `gate.tryBegin()`, which keeps its own flag, so two quick clicks cannot
// both send. Every call below gives a result for a rejection too (the clients never throw), and the
// rejection handlers turn an unexpected rejection into a `rejected` result, so no control is left
// waiting for an answer that will not come. The gate ends after the answer either way.
//
// Leaving the screen resets the state (screens.tsx sends `reset` through `realityEventOnScreen`),
// which drops the chosen file, the types, the claim, the result and the logins. Nothing here is
// written to storage or logged.
//
// Hooks and click handlers are rendered in no test (threat model F6-1c).

import { useCallback, useReducer, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { createStartGate } from "../contradictions";
import {
  INITIAL_REALITY_STATE,
  fileAt,
  listImports,
  reduceReality,
  sendCheck,
  sendClaims,
  sendInspect,
  type InputChange,
  type RealityState,
} from "../reality";

export interface RealityHandle {
  state: RealityState;
  /** Read the Imports listing and the policy claims again (on entering the screen). */
  refresh: () => void;
  /** Choose the export at this position of the listing and read it. */
  chooseFile: (index: number) => void;
  changeInput: (change: InputChange) => void;
  /** A no-op while a problem stops it or a check is going: nothing is sent then. */
  check: () => void;
  /** Forget the file, the choices, the claim, the result and the logins (on leaving the screen). */
  reset: () => void;
}

export function useReality(): RealityHandle {
  const [state, dispatch] = useReducer(reduceReality, INITIAL_REALITY_STATE);
  const [gate] = useState(() => createStartGate());

  const refresh = useCallback(() => {
    dispatch({ type: "files_requested" });
    void listImports(invoke).then(
      (call) => dispatch({ type: "files_result", call }),
      () => dispatch({ type: "files_result", call: { kind: "rejected" } }),
    );
    dispatch({ type: "claims_requested" });
    void sendClaims(invoke).then(
      (result) => dispatch({ type: "claims_result", result }),
      () => dispatch({ type: "claims_result", result: { kind: "rejected" } }),
    );
  }, []);

  const chooseFile = useCallback(
    (index: number) => {
      const file = fileAt(state.files, index);
      if (file === null) {
        return;
      }
      dispatch({ type: "file_chosen", index });
      void sendInspect(invoke, file).then(
        (result) => dispatch({ type: "inspect_result", file, result }),
        () => dispatch({ type: "inspect_result", file, result: { kind: "rejected" } }),
      );
    },
    [state.files],
  );

  const changeInput = useCallback((change: InputChange) => {
    dispatch({ type: "input_changed", change });
  }, []);

  const check = useCallback(() => {
    // The gate keeps its own flag, so a second click before the render that disables the button
    // cannot send a second check.
    if (!gate.tryBegin()) {
      return;
    }
    if (state.check.phase === "loading") {
      gate.end();
      return;
    }
    const generation = state.generation;
    const sent = sendCheck(invoke, state);
    if (sent === null) {
      gate.end();
      return;
    }
    dispatch({ type: "check_requested", generation });
    void sent
      .then(
        (result) => dispatch({ type: "check_result", generation, result }),
        () => dispatch({ type: "check_result", generation, result: { kind: "rejected" } }),
      )
      .finally(() => gate.end());
  }, [state, gate]);

  const reset = useCallback(() => {
    dispatch({ type: "reset" });
  }, []);

  return { state, refresh, chooseFile, changeInput, check, reset };
}
