// "Folders configured" in one short block (M6_PLAN C.1). Presentational: props only. Not
// configured is said in words and is never "0 results" (DESIGN.md 1 rule 5). The full two-column
// screen is chunk 6g.

import type { FolderKind } from "../ipc/folders";
import type { FoldersSummary as Summary } from "../foldersSummary";

export function folderLabel(kind: FolderKind): string {
  switch (kind) {
    case "vault":
      return "Vault";
    case "questionnaires":
      return "Questionnaires";
    case "exports":
      return "Exports";
    case "imports":
      return "Imports";
    case "ledger":
      return "Ledger";
  }
}

function names(kinds: readonly FolderKind[]): string {
  return kinds.map(folderLabel).join(", ");
}

export function FoldersSummary({ summary }: { summary: Summary }) {
  switch (summary.state) {
    case "unknown":
      return <p>Folders: not read yet</p>;
    case "unavailable":
      return (
        <p role="status" aria-live="polite">
          Folders: could not be read (<code className="font-mono">{summary.code}</code>)
        </p>
      );
    case "known":
      return (
        <div>
          <p>
            Folders: {summary.inUse} of {summary.total} in use
          </p>
          {summary.pendingNextLaunch.length > 0 ? (
            <p>Saved, applies the next time you start ALIBI: {names(summary.pendingNextLaunch)}</p>
          ) : null}
          {summary.notSet.length > 0 ? <p>Not set: {names(summary.notSet)}</p> : null}
          {summary.invalid.length > 0 ? (
            <p className="font-semibold text-status-error">
              Saved folder is not valid: {names(summary.invalid)}
            </p>
          ) : null}
        </div>
      );
  }
}
