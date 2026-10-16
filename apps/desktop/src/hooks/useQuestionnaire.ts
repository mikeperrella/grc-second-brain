// Thin hook: the questionnaire flow. It lists the workbook names, inspects a chosen file, starts a
// run and polls `questionnaire_status` about once a second (ADR-020 decision 4).
//
// Polling runs ONLY while health is `healthy` and a run is going. It stops when the run ends, when
// health leaves `healthy`, and on unmount (the effect cleanup calls the stop function). The
// window sends only a file NAME and the mapping numbers; the host checks the name (G9).

import { useCallback, useEffect, useReducer } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listFiles } from "../ipc/folders";
import { callRoute } from "../ipc/proxy";
import { mappingRequest, type MappingDraft } from "../mapping";
import { pollTargetWhen, startPolling } from "../poller";
import {
  INITIAL_QUESTIONNAIRE_STATE,
  reduceQuestionnaire,
  runPollTarget,
  type QuestionnaireState,
} from "../questionnaireRun";

export interface QuestionnaireHandle {
  state: QuestionnaireState;
  refreshFiles: () => void;
  selectFile: (file: string) => void;
  inspect: () => void;
  changeMapping: (mapping: MappingDraft) => void;
  start: () => void;
  dismissRun: () => void;
}

export function useQuestionnaire(healthy: boolean): QuestionnaireHandle {
  const [state, dispatch] = useReducer(reduceQuestionnaire, INITIAL_QUESTIONNAIRE_STATE);
  const target = pollTargetWhen(healthy, runPollTarget(state));

  useEffect(() => {
    if (target === null) {
      return undefined;
    }
    return startPolling(
      () => callRoute<"questionnaire_status">(invoke, { route: "questionnaire_status", run_id: target }),
      (result) => dispatch({ type: "status_result", result, nowMs: Date.now() }),
    );
  }, [target]);

  const refreshFiles = useCallback(() => {
    dispatch({ type: "files_requested" });
    void listFiles(invoke, "questionnaires").then((call) => dispatch({ type: "files_result", call }));
  }, []);

  const selectFile = useCallback((file: string) => dispatch({ type: "file_selected", file }), []);

  const inspect = useCallback(() => {
    const file = state.selected;
    if (file === null) {
      return;
    }
    dispatch({ type: "inspect_requested" });
    void callRoute<"questionnaire_inspect">(invoke, { route: "questionnaire_inspect", file }).then(
      (result) => dispatch({ type: "inspect_result", result }),
    );
  }, [state.selected]);

  const changeMapping = useCallback(
    (mapping: MappingDraft) => dispatch({ type: "mapping_changed", mapping }),
    [],
  );

  const start = useCallback(() => {
    const file = state.selected;
    const sheets = state.inspect.phase === "ready" ? state.inspect.info.sheets : [];
    const mapping = state.mapping === null ? null : mappingRequest(state.mapping, sheets);
    if (file === null || mapping === null) {
      return;
    }
    dispatch({ type: "start_requested" });
    void callRoute<"questionnaire_start">(invoke, { route: "questionnaire_start", file, mapping }).then(
      (result) => dispatch({ type: "start_result", result, nowMs: Date.now() }),
    );
  }, [state.selected, state.inspect, state.mapping]);

  const dismissRun = useCallback(() => dispatch({ type: "run_dismissed" }), []);

  return { state, refreshFiles, selectFile, inspect, changeMapping, start, dismissRun };
}
