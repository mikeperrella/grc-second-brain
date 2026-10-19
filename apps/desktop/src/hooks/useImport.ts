// Thin hook: the Import screen (chunk 65g). Three read calls (`folder_list_files` kind exports,
// `draft_rows`, and the `ledger_import` PREVIEW, which writes nothing) and one write (the real
// import, only through the two-step confirm and ledgerActions.ts `importPromises`). The request
// with `preview: false` is not written here.
//
// Hooks and click handlers are rendered in no test (threat model F6-1c); the logic they call is in
// ledgerImport.ts, confirm.ts and ledgerActions.ts, which are tested without a DOM.

import { useCallback, useReducer } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { ConfirmState } from "../confirm";
import { listFiles } from "../ipc/folders";
import { callRoute } from "../ipc/proxy";
import { failureOf } from "../ledger";
import { importPromises } from "../ledgerActions";
import {
  INITIAL_IMPORT_STATE,
  currentFields,
  fieldsKey,
  importTarget,
  previewRequest,
  reduceImport,
  type ImportState,
  type ImportTarget,
  type InputChange,
} from "../ledgerImport";
import { useConfirm } from "./useConfirm";

export interface ImportHandle {
  state: ImportState;
  confirm: ConfirmState<ImportTarget>;
  refreshFiles: () => void;
  /** Choose a draft by its file name and read it (draft_rows gives the full run id). */
  chooseFile: (file: string) => void;
  changeInput: (change: InputChange) => void;
  /** The preview: `ledger_import` with `preview: true`. Writes nothing. */
  preview: () => void;
  /** Step one: show the confirm area. Sends nothing. A no-op without a matching preview. */
  openImport: () => void;
  /** Step two: the only call that can send the real import. */
  confirmImport: (onImported: () => void) => void;
  cancelImport: () => void;
}

export function useImport(): ImportHandle {
  const [state, dispatch] = useReducer(reduceImport, INITIAL_IMPORT_STATE);
  const confirm = useConfirm<ImportTarget>();
  const confirmDispatch = confirm.dispatch;

  const refreshFiles = useCallback(() => {
    dispatch({ type: "files_requested" });
    void listFiles(invoke, "exports").then((call) => dispatch({ type: "files_result", call }));
  }, []);

  const chooseFile = useCallback(
    (file: string) => {
      confirmDispatch({ type: "cancel" });
      dispatch({ type: "file_selected", file });
      dispatch({ type: "draft_requested" });
      void callRoute<"draft_rows">(invoke, { route: "draft_rows", file }).then((result) =>
        dispatch({ type: "draft_result", file, result }),
      );
    },
    [confirmDispatch],
  );

  const changeInput = useCallback(
    (change: InputChange) => {
      // A changed input also closes an open confirm: what is confirmed is always what was previewed.
      confirmDispatch({ type: "cancel" });
      dispatch({ type: "input_changed", change });
    },
    [confirmDispatch],
  );

  const preview = useCallback(() => {
    const fields = currentFields(state);
    if (fields === null) {
      return;
    }
    const key = fieldsKey(fields);
    dispatch({ type: "preview_requested", key });
    void callRoute<"ledger_import">(invoke, previewRequest(fields)).then((result) =>
      dispatch({ type: "preview_result", key, fields, result }),
    );
  }, [state]);

  const openImport = useCallback(() => {
    const target = importTarget(state);
    if (target === null) {
      return;
    }
    confirmDispatch({ type: "open", target });
  }, [state, confirmDispatch]);

  const cancelImport = useCallback(() => {
    confirmDispatch({ type: "cancel" });
  }, [confirmDispatch]);

  const confirmImport = useCallback(
    (onImported: () => void) => {
      const confirmed = confirmDispatch({ type: "confirm" });
      if (confirmed === null) {
        return;
      }
      dispatch({ type: "import_started" });
      void importPromises(invoke, confirmed).then((result) => {
        dispatch({ type: "import_result", result });
        confirmDispatch({ type: "done" });
        if (failureOf(result) === null) {
          onImported();
        }
      });
    },
    [confirmDispatch],
  );

  return {
    state,
    confirm: confirm.state,
    refreshFiles,
    chooseFile,
    changeInput,
    preview,
    openImport,
    confirmImport,
    cancelImport,
  };
}
