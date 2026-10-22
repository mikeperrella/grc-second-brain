// Wires the built screens (the Ledger, Recall, Blast radius, Import and Contradictions screens included) to their thin hooks. The state of the index job, of the
// questionnaire run and of the contradictions run lives here (not inside a screen), so leaving a screen does not forget a job
// that is still running; the hooks' polling stops when health leaves `healthy` or this component
// unmounts.

import { useEffect } from "react";
import { blastEventOnScreen } from "./blast";
import { BlastScreen } from "./components/BlastScreen";
import { ContradictionsScreen } from "./components/ContradictionsScreen";
import { FoldersScreen } from "./components/FoldersScreen";
import { ImportScreen } from "./components/ImportScreen";
import { IndexScreen } from "./components/IndexScreen";
import { LedgerScreen } from "./components/LedgerScreen";
import { QuestionnaireScreen } from "./components/QuestionnaireScreen";
import { RealityScreen } from "./components/RealityScreen";
import { RecallScreen } from "./components/RecallScreen";
import { ReviewQueueScreen } from "./components/ReviewQueueScreen";
import type { ScreenId } from "./components/Shell";
import { confirmEventOnScreen } from "./confirm";
import { folderInUse, foldersScreenView, ledgerInUse } from "./foldersScreen";
import { useFolderPicker } from "./hooks/useFolderPicker";
import { useBlast } from "./hooks/useBlast";
import { useContradictions } from "./hooks/useContradictions";
import type { FoldersHandle } from "./hooks/useFolders";
import { useImport } from "./hooks/useImport";
import { useIndexJob } from "./hooks/useIndexJob";
import { useLedger } from "./hooks/useLedger";
import { useNow } from "./hooks/useNow";
import { useQuestionnaire } from "./hooks/useQuestionnaire";
import { useReality } from "./hooks/useReality";
import { useRecall } from "./hooks/useRecall";
import { useReviewQueue } from "./hooks/useReviewQueue";
import { runIsActive } from "./questionnaireRun";
import type { ReadinessView } from "./readiness";
import { realityEventOnScreen } from "./reality";

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
  const imports = useImport();
  const blast = useBlast();
  // Like the index job and the questionnaire run, a contradictions run lives here, so leaving the
  // screen does not forget a run that is going (nothing resets it on a screen change).
  const contradictions = useContradictions(healthy);
  // The Reality check state (the chosen export, the types, the result and the logins) is dropped when
  // the person leaves its screen (reality.ts realityEventOnScreen, below).
  const reality = useReality();

  // Entering the Ledger reads the customers and a page of promises; entering Recall reads the
  // customers only (its select); entering Blast radius reads the customers and the cited files;
  // entering Import reads the customers and the exports folder's file names. All are read calls;
  // nothing is written by entering a screen.
  const { refreshCustomers, refreshPromises } = ledger;
  const { refreshFiles: refreshImportFiles } = imports;
  const { refreshPaths: refreshBlastPaths } = blast;
  const { refresh: refreshReality } = reality;
  useEffect(() => {
    if (healthy && screen === "ledger") {
      refreshCustomers();
      refreshPromises();
    } else if (healthy && screen === "recall") {
      refreshCustomers();
    } else if (healthy && screen === "blast") {
      refreshCustomers();
      refreshBlastPaths();
    } else if (healthy && screen === "import") {
      refreshCustomers();
      refreshImportFiles();
    } else if (healthy && screen === "reality") {
      // The imports folder's file names and the policy claims: two read calls.
      refreshReality();
    }
  }, [healthy, screen, refreshCustomers, refreshPromises, refreshBlastPaths, refreshImportFiles, refreshReality]);

  // Leaving a screen (or entering another) cancels its open confirm area: nothing is sent and the
  // area is gone when the person returns (confirm.ts confirmEventOnScreen).
  const { cancelVoid } = ledger;
  const { cancelAcknowledge } = recall;
  const { cancelImport } = imports;
  const { reset: resetBlast } = blast;
  const { reset: resetReality } = reality;
  useEffect(() => {
    // The pasted text of the Blast radius screen stays for that preview only (DESIGN.md 11.1).
    if (blastEventOnScreen(screen) !== null) {
      resetBlast();
    }
    // The chosen export, the confirmed types, the result and the logins are dropped on leaving.
    if (realityEventOnScreen(screen) !== null) {
      resetReality();
    }
    if (confirmEventOnScreen<ScreenId>("ledger", screen) !== null) {
      cancelVoid();
    }
    if (confirmEventOnScreen<ScreenId>("recall", screen) !== null) {
      cancelAcknowledge();
    }
    if (confirmEventOnScreen<ScreenId>("import", screen) !== null) {
      cancelImport();
    }
  }, [screen, cancelVoid, cancelAcknowledge, cancelImport, resetBlast, resetReality]);

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
    case "blast":
      return (
        <BlastScreen
          state={blast.state}
          customers={ledger.state.customers}
          enabled={healthy}
          onInputChange={blast.changeInput}
          onPreview={blast.preview}
        />
      );
    case "import":
      return (
        <ImportScreen
          state={imports.state}
          confirm={imports.confirm}
          customers={ledger.state.customers}
          enabled={healthy}
          onChooseFile={imports.chooseFile}
          onInputChange={imports.changeInput}
          onPreview={imports.preview}
          onOpenImport={imports.openImport}
          onConfirmImport={() => imports.confirmImport(refreshCustomers)}
          onCancelImport={imports.cancelImport}
        />
      );
    case "contradictions":
      return (
        <ContradictionsScreen
          state={contradictions.state}
          enabled={healthy}
          onInputChange={contradictions.changeInput}
          onStart={contradictions.start}
        />
      );
    case "reality":
      return (
        <RealityScreen
          state={reality.state}
          enabled={healthy}
          onChooseFile={reality.chooseFile}
          onInputChange={reality.changeInput}
          onCheck={reality.check}
        />
      );
  }
}
