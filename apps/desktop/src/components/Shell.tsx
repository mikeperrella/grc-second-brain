// The window frame: title, navigation, status bar, content. Presentational: props only.
//
// Navigation is disabled while the sidecar is not healthy ("starting: navigation disabled",
// M6_PLAN C.1). The screens themselves are built in later chunks (6g, 6h), so the entries are
// disabled with a visible reason in every state; nothing here pretends a screen exists.

import type { ReactNode } from "react";

export const NAV_ENTRIES = ["Folders", "Index the vault", "Questionnaire", "Review queue"] as const;

export interface ShellProps {
  healthy: boolean;
  statusBar: ReactNode;
  children?: ReactNode;
}

export function Shell({ healthy, statusBar, children }: ShellProps) {
  return (
    <div className="mx-auto grid max-w-5xl gap-4 p-4">
      <header>
        <h1 className="text-xl font-semibold">ALIBI</h1>
        <p className="text-text-muted">Every compliance answer needs an alibi.</p>
      </header>
      <nav aria-label="Screens">
        <ul className="flex flex-wrap gap-2">
          {NAV_ENTRIES.map((entry) => (
            <li key={entry}>
              <button
                type="button"
                disabled
                className="border border-border px-3 py-1 text-text-muted"
              >
                {entry}
              </button>
            </li>
          ))}
        </ul>
        <p className="text-sm text-text-muted">
          {healthy
            ? "These screens are not built yet."
            : "Navigation is off until the sidecar is healthy."}
        </p>
      </nav>
      {statusBar}
      <main>{children}</main>
    </div>
  );
}
