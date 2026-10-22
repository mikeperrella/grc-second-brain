// The window frame: title, navigation, status bar, content. Presentational: props only.
//
// Navigation is disabled while the sidecar is not healthy ("starting: navigation disabled",
// M6_PLAN C.1). Folders, Index the vault, Questionnaire (6g) and the Review queue (6h) are built,
// chunk 65f adds the Ledger and Recall screens, chunk 65g the Import screen and chunk 65h the Blast
// radius screen (in the Promises group, between Recall and Import, DESIGN.md 11.4), and chunk 65i
// the Contradictions screen (a third group, Checks), and chunk 65j the Reality check screen (after
// Contradictions in Checks, DESIGN.md 11.4). The
// entries are in three groups, and the group names and labels are drafts for the owner's approval
// (ledgerText.ts).

import type { ReactNode } from "react";
import {
  NAV_GROUP_CHECKS,
  NAV_GROUP_PROMISES,
  NAV_GROUP_QUESTIONNAIRES,
  NAV_LABEL_BLAST,
  NAV_LABEL_CONTRADICTIONS,
  NAV_LABEL_FOLDERS,
  NAV_LABEL_IMPORT,
  NAV_LABEL_INDEX,
  NAV_LABEL_LEDGER,
  NAV_LABEL_QUESTIONNAIRE,
  NAV_LABEL_REALITY,
  NAV_LABEL_RECALL,
  NAV_LABEL_REVIEW,
} from "../ledgerText";

export type ScreenId =
  | "folders"
  | "index"
  | "questionnaire"
  | "review"
  | "ledger"
  | "recall"
  | "blast"
  | "import"
  | "contradictions"
  | "reality";

interface NavEntry {
  label: string;
  screen: ScreenId;
}

interface NavGroup {
  /** The visible group heading. Every group name and label here is a DRAFT (ledgerText.ts). */
  heading: string;
  entries: readonly NavEntry[];
}

// Every entry has the same box (a 4px bottom border), so marking one never moves the others. The
// current entry fills that border and underlines its label: shape and line, not colour alone.
const NAV_BOX = "border border-border border-b-4 px-3 py-1";
const NAV_CURRENT = "border-b-border underline underline-offset-4";
const NAV_OTHER = "border-b-transparent";

export const NAV_GROUPS: readonly NavGroup[] = [
  {
    heading: NAV_GROUP_QUESTIONNAIRES,
    entries: [
      { label: NAV_LABEL_FOLDERS, screen: "folders" },
      { label: NAV_LABEL_INDEX, screen: "index" },
      { label: NAV_LABEL_QUESTIONNAIRE, screen: "questionnaire" },
      { label: NAV_LABEL_REVIEW, screen: "review" },
    ],
  },
  {
    heading: NAV_GROUP_PROMISES,
    entries: [
      { label: NAV_LABEL_LEDGER, screen: "ledger" },
      { label: NAV_LABEL_RECALL, screen: "recall" },
      { label: NAV_LABEL_BLAST, screen: "blast" },
      { label: NAV_LABEL_IMPORT, screen: "import" },
    ],
  },
  {
    heading: NAV_GROUP_CHECKS,
    entries: [
      { label: NAV_LABEL_CONTRADICTIONS, screen: "contradictions" },
      { label: NAV_LABEL_REALITY, screen: "reality" },
    ],
  },
];

/** Every entry in page order (the order the Tab key visits them). */
export const NAV_ENTRIES: readonly NavEntry[] = NAV_GROUPS.flatMap((group) => group.entries);

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
        <ul className="flex flex-wrap items-start gap-x-6 gap-y-2">
          {NAV_GROUPS.map((group) => (
            <li key={group.heading} className="grid min-w-0 gap-1">
              <p id={`nav-group-${group.heading}`} className="text-sm font-semibold text-text-muted">
                {group.heading}
              </p>
              <ul aria-labelledby={`nav-group-${group.heading}`} className="flex flex-wrap gap-2">
                {group.entries.map((entry) => {
                  const screen = entry.screen;
                  const isCurrent = screen === current;
                  return (
                    <li key={entry.label}>
                      <button
                        type="button"
                        disabled={!healthy}
                        aria-current={isCurrent ? "page" : undefined}
                        className={`${NAV_BOX} ${isCurrent ? NAV_CURRENT : NAV_OTHER}`}
                        onClick={() => onNavigate?.(screen)}
                      >
                        {entry.label}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </li>
          ))}
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
