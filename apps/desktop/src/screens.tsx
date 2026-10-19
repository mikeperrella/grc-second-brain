// Wires the built screens (the Ledger and Recall screens included) to their thin hooks. The state of the index job and of the
// questionnaire run lives here (not inside a screen), so leaving a screen does not forget a job
// that is still running; the hooks' polling stops when health leaves `healthy` or this component
// unmounts.

import { useEffect } from "react";
import { FoldersScreen } from "./components/FoldersScreen";
import { IndexScreen } from "./components/IndexScreen";
import { LedgerScreen } from "./components/LedgerScreen";
import { QuestionnaireScreen } from "./components/QuestionnaireScreen";
import { RecallScreen } from "./components/RecallScreen";
import { ReviewQueueScreen } from "./components/ReviewQueueScreen";
import type { ScreenId } from "./components/Shell";
import { folderInUse, foldersScreenView, ledgerInUse } from "./foldersScreen";
import { useFolderPicker } from "./hooks/useFolderPicker";
import type { FoldersHandle } from "./hooks/useFolders";
import { useIndexJob } from "./hooks/useIndexJob";
import { useLedger } from "./hooks/useLedger";
import { useNow } from "./hooks/useNow";
import { useQuestionnaire } from "./hooks/useQuestionnaire";
import { useRecall } from "./hooks/useRecall";
import { useReviewQueue } from "./hooks/useReviewQueue";
import { runIsActive } from "./questionnaireRun";
import type { ReadinessView } from "./readiness";

export interface ScreensProps {
  screen: ScreenId;
  healthy: boolean;
  readiness: ReadinessView;
  folders: FoldersHandle;
  onNavigate: (screen: ScreenId) => void;
}

export function Screens({ screen, healthy, readiness, folders, onNavigate }: ScreensProps) {
  const { pick, choose } = useFolderPicker(folders.refresh);
  const index = useIndexJob(healthy);
  const questionnaire = useQuestionnaire(healthy);
  const counting = index.state.phase === "running" || runIsActive(questionnaire.state.run);
  const nowMs = useNow(counting);
  const queue = useReviewQueue();
  const ledger = useLedger();
  const recall = useRecall();

  // Entering the Ledger reads the customers and a page of promises; entering Recall reads the
  // customers only (its select). Both are read calls; nothing is written by entering a screen.
  const { refreshCustomers, refreshPromises } = ledger;
  useEffect(() => {
    if (healthy && screen === "ledger") {
      refreshCustomers();
      refreshPromises();
    } else if (healthy && screen === "recall") {
      refreshCustomers();
    }
  }, [healthy, screen, refreshCustomers, refreshPromises]);

  const { refreshFiles } = questionnaire;
  useEffect(() => {
    if (healthy && screen === "questionnaire") {
      refreshFiles();
    }
  }, [healthy, screen, refreshFiles]);

  const { refreshFiles: refreshQueueFiles, openFile: openQueueFile } = queue;
  useEffect(() => {
    if (healthy && screen === "review") {
      refreshQueueFiles();
    }
  }, [healthy, screen, refreshQueueFiles]);

  const openInQueue = (file: string): void => {
    openQueueFile(file);
    onNavigate("review");
  };

  switch (screen) {
    case "folders":
      return (
        <FoldersScreen
          view={foldersScreenView(folders.call)}
          pick={pick}
          enabled={healthy}
          onChoose={choose}
        />
      );
    case "index":
      return (
        <IndexScreen
          state={index.state}
          nowMs={nowMs}
          readiness={readiness}
          vaultInUse={folderInUse(folders.call, "vault")}
          enabled={healthy}
          onStart={index.start}
        />
      );
    case "questionnaire":
      return (
        <QuestionnaireScreen
          state={questionnaire.state}
          nowMs={nowMs}
          readiness={readiness}
          ledgerInUse={ledgerInUse(folders.call)}
          enabled={healthy}
          onSelectFile={questionnaire.selectFile}
          onInspect={questionnaire.inspect}
          onMappingChange={questionnaire.changeMapping}
          onStart={questionnaire.start}
          onGoToIndex={() => onNavigate("index")}
          onDismissRun={questionnaire.dismissRun}
          onOpenInQueue={openInQueue}
        />
      );
    case "review":
      return (
        <ReviewQueueScreen state={queue.state} enabled={healthy} onOpenFile={queue.openFile} />
      );
    case "ledger":
      return (
        <LedgerScreen
          state={ledger.state}
          confirm={ledger.confirm}
          enabled={healthy}
          onFilterChange={ledger.changeFilter}
          onChooseReason={ledger.chooseReason}
          onOpenVoid={ledger.openVoid}
          onConfirmVoid={ledger.confirmVoid}
          onCancelVoid={ledger.cancelVoid}
        />
      );
    case "recall":
      return (
        <RecallScreen
          state={recall.state}
          confirm={recall.confirm}
          customers={ledger.state.customers}
          enabled={healthy}
          onChooseCustomer={recall.chooseCustomer}
          onRun={recall.run}
          onOpenAcknowledge={recall.openAcknowledge}
          onConfirmAcknowledge={recall.confirmAcknowledge}
          onCancelAcknowledge={recall.cancelAcknowledge}
        />
      );
  }
}
