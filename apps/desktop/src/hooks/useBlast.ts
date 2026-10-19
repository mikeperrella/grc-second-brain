// Thin hook: the Blast radius screen (chunk 65h). Two read calls: `ledger_cited_paths` and the
// `ledger_blast_radius` preview, which the sidecar answers without writing anything (ADR-018
// decision 7). There is no write and no confirm step. The preview is sent only by blast.ts
// `sendPreview`, which sends nothing while an input problem stops it.
//
// Hooks and click handlers are rendered in no test (threat model F6-1c); the logic is in blast.ts,
// which is tested without a DOM. The proposed text lives in this reducer's state only.

import { useCallback, useReducer } from "react";
import { invoke } from "@tauri-apps/api/core";
import { INITIAL_BLAST_STATE, reduceBlast, sendPreview, type BlastState, type InputChange } from "../blast";
import { callRoute } from "../ipc/proxy";

export interface BlastHandle {
  state: BlastState;
  /** Read the cited files again (on entering the screen). */
  refreshPaths: () => void;
  changeInput: (change: InputChange) => void;
  /** The preview. A no-op while an input problem stops it: nothing is sent then. */
  preview: () => void;
  /** Forget the pasted text, the chosen path and the preview (on leaving the screen). */
  reset: () => void;
}

export function useBlast(): BlastHandle {
  const [state, dispatch] = useReducer(reduceBlast, INITIAL_BLAST_STATE);

  const refreshPaths = useCallback(() => {
    dispatch({ type: "paths_requested" });
    void callRoute<"ledger_cited_paths">(invoke, { route: "ledger_cited_paths" }).then((result) =>
      dispatch({ type: "paths_result", result }),
    );
  }, []);

  const changeInput = useCallback((change: InputChange) => {
    dispatch({ type: "input_changed", change });
  }, []);

  const preview = useCallback(() => {
    const generation = state.generation;
    const sent = sendPreview(invoke, state.inputs);
    if (sent === null) {
      return;
    }
    dispatch({ type: "preview_requested", generation });
    void sent.reply.then((result) => dispatch({ type: "preview_result", generation, fields: sent.fields, result }));
  }, [state]);

  const reset = useCallback(() => {
    dispatch({ type: "reset" });
  }, []);

  return { state, refreshPaths, changeInput, preview, reset };
}
