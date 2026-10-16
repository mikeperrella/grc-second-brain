// The window frame: title, navigation, status bar, content. Presentational: props only.
//
// Navigation is disabled while the sidecar is not healthy ("starting: navigation disabled",
// M6_PLAN C.1). Folders, Index the vault, Questionnaire (6g) and the Review queue (6h) are built.

import type { ReactNode } from "react";

export type ScreenId = "folders" | "index" | "questionnaire" | "review";

interface NavEntry {
  label: string;
  screen: ScreenId;
}

export const NAV_ENTRIES: readonly NavEntry[] = [
  { label: "Folders", screen: "folders" },
  { label: "Index the vault", screen: "index" },
  { label: "Questionnaire", screen: "questionnaire" },
  { label: "Review queue", screen: "review" },
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
            return (
              <li key={entry.label}>
                <button
                  type="button"
                  disabled={!healthy}
                  aria-current={screen === current ? "page" : undefined}
                  className="border border-border px-3 py-1"
                  onClick={() => onNavigate?.(screen)}
                >
                  {entry.label}
                </button>
              </li>
            );
          })}
        </ul>
        {healthy ? null : (
          <p className="text-sm text-text-muted">Navigation is off until the sidecar is healthy.</p>
        )}
      </nav>
      {statusBar}
      <main>{children}</main>
    </div>
  );
}
