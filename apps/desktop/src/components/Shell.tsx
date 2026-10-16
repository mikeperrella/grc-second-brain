// The window frame: title, navigation, status bar, content. Presentational: props only.
//
// Navigation is disabled while the sidecar is not healthy ("starting: navigation disabled",
// M6_PLAN C.1). Folders, Index the vault and Questionnaire are built (6g); the Review queue is
// chunk 6h, so its entry stays disabled with a visible reason in every state and nothing here
// pretends it exists.

import type { ReactNode } from "react";
import { REVIEW_QUEUE_PLACEHOLDER } from "../fixedTexts";

export type ScreenId = "folders" | "index" | "questionnaire";

interface NavEntry {
  label: string;
  /** `null` means the screen is not built yet. */
  screen: ScreenId | null;
}

export const NAV_ENTRIES: readonly NavEntry[] = [
  { label: "Folders", screen: "folders" },
  { label: "Index the vault", screen: "index" },
  { label: "Questionnaire", screen: "questionnaire" },
  { label: "Review queue", screen: null },
];


export interface ShellProps {
  healthy: boolean;
  statusBar: ReactNode;
  /** The screen shown in `children`; marks its entry as current. */
  current?: ScreenId;
  onNavigate?: (screen: ScreenId) => void;
  children?: ReactNode;
}

export function Shell({ healthy, statusBar, current, onNavigate, children }: ShellProps) {
  return (
    <div className="mx-auto grid max-w-5xl gap-4 p-4">
      <header>
        <h1 className="text-xl font-semibold">ALIBI</h1>
        <p className="text-text-muted">Every compliance answer needs an alibi.</p>
      </header>
      <nav aria-label="Screens">
        <ul className="flex flex-wrap gap-2">
          {NAV_ENTRIES.map((entry) => {
            const screen = entry.screen;
            const enabled = healthy && screen !== null;
            return (
              <li key={entry.label}>
                <button
                  type="button"
                  disabled={!enabled}
                  aria-current={screen !== null && screen === current ? "page" : undefined}
                  className="border border-border px-3 py-1"
                  onClick={screen !== null ? () => onNavigate?.(screen) : undefined}
                >
                  {entry.label}
                </button>
              </li>
            );
          })}
        </ul>
        {healthy ? (
          <p className="text-sm text-text-muted">{REVIEW_QUEUE_PLACEHOLDER}</p>
        ) : (
          <p className="text-sm text-text-muted">Navigation is off until the sidecar is healthy.</p>
        )}
      </nav>
      {statusBar}
      <main>{children}</main>
    </div>
  );
}
