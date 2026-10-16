// Wires the three built screens to their thin hooks. The state of the index job and of the
// questionnaire run lives here (not inside a screen), so leaving a screen does not forget a job
// that is still running; the hooks' polling stops when health leaves `healthy` or this component
// unmounts.

import { useEffect } from "react";
import { FoldersScreen } from "./components/FoldersScreen";
import { IndexScreen } from "./components/IndexScreen";
import { QuestionnaireScreen } from "./components/QuestionnaireScreen";
import { ReviewQueueScreen } from "./components/ReviewQueueScreen";
import type { ScreenId } from "./components/Shell";
import { folderInUse, foldersScreenView, ledgerInUse } from "./foldersScreen";
import { useFolderPicker } from "./hooks/useFolderPicker";
import type { FoldersHandle } from "./hooks/useFolders";
import { useIndexJob } from "./hooks/useIndexJob";
import { useNow } from "./hooks/useNow";
import { useQuestionnaire } from "./hooks/useQuestionnaire";
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
  }
}
