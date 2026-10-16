// Thin hook: the review queue. It lists the draft names in the exports folder and reads one draft.
// Exactly two calls, both read only: `folder_list_files` (kind exports) and `draft_rows`. Nothing
// here records an approval or writes a workbook (ADR-021 decision 7; scripts/reviewQueueSources.test.mjs
// scans these files to prove it).

import { useCallback, useReducer } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listFiles } from "../ipc/folders";
import { callRoute } from "../ipc/proxy";
import {
  INITIAL_REVIEW_QUEUE_STATE,
  reduceReviewQueue,
  type ReviewQueueState,
} from "../reviewQueue";

export interface ReviewQueueHandle {
  state: ReviewQueueState;
  refreshFiles: () => void;
  /** Choose a draft by its file name and read it. */
  openFile: (file: string) => void;
}

export function useReviewQueue(): ReviewQueueHandle {
  const [state, dispatch] = useReducer(reduceReviewQueue, INITIAL_REVIEW_QUEUE_STATE);

  const refreshFiles = useCallback(() => {
    dispatch({ type: "files_requested" });
    void listFiles(invoke, "exports").then((call) => dispatch({ type: "files_result", call }));
  }, []);

  const openFile = useCallback((file: string) => {
    dispatch({ type: "file_selected", file });
    dispatch({ type: "draft_requested" });
    void callRoute<"draft_rows">(invoke, { route: "draft_rows", file }).then((result) =>
      dispatch({ type: "draft_result", file, result }),
    );
  }, []);

  return { state, refreshFiles, openFile };
}
