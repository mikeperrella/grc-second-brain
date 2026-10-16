// The status bar: health, then readiness, then folders (M6_PLAN C.1). Presentational: props only.

import type { HealthView } from "../ipc/health";
import type { ReadinessView } from "../readiness";
import type { FoldersSummary as Summary } from "../foldersSummary";
import { FoldersSummary } from "./FoldersSummary";
import { HealthIndicator } from "./HealthIndicator";
import { ReadinessIndicator } from "./ReadinessIndicator";

export interface StatusBarProps {
  health: HealthView;
  readiness: ReadinessView;
  folders: Summary;
}

export function StatusBar({ health, readiness, folders }: StatusBarProps) {
  return (
    <section
      aria-label="ALIBI status"
      className="alibi-block grid gap-2 border border-border bg-surface-raised p-3 text-sm"
    >
      <HealthIndicator view={health} />
      <ReadinessIndicator view={readiness} />
      <FoldersSummary summary={folders} />
    </section>
  );
}
