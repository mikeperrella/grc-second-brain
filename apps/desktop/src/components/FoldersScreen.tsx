// The Folders screen (M6_PLAN C.2, docs/DESIGN.md 4.1 and 4.5). Presentational: props only.
//
// Paths are display strings from the host and are shown as plain text nodes. The Choose button
// passes the folder KIND (an enum) to `onChoose`; no path is held, built or sent from here.
// "In use now" and "Saved for next launch" are never merged: a saved folder is not shown as if it
// were in use (F35-6).

import { LEDGER_FOLDER_WARNING, SAVED_STATEMENT } from "../fixedTexts";
import type { FoldersScreenView, FolderRowView, PickState } from "../foldersScreen";
import type { FolderKind } from "../ipc/folders";
import { folderProblemMessage } from "../messages";
import { PlainText } from "./PlainText";
import { Refusal } from "./Refusal";
import { Statement } from "./Statement";

export interface FoldersScreenProps {
  view: FoldersScreenView;
  pick: PickState;
  /** False while the sidecar is not healthy. */
  enabled: boolean;
  onChoose: (kind: FolderKind) => void;
}

function Value({ value }: { value: string | null }) {
  return value === null ? <span>not set</span> : <PlainText text={value} />;
}

function Row({
  row,
  busy,
  enabled,
  onChoose,
}: {
  row: FolderRowView;
  busy: boolean;
  enabled: boolean;
  onChoose: (kind: FolderKind) => void;
}) {
  const savedNotInUse = row.saved !== null && row.inUse === null;
  return (
    <li className="alibi-block grid gap-1 border border-border bg-surface-raised p-3">
      <h3 className="text-lg font-semibold">
        {row.label}
        {row.neededForQueue ? " (needed for the review queue)" : ""}
      </h3>
      <dl className="grid gap-1">
        <div>
          <dt className="font-semibold">In use now</dt>
          <dd>
            <Value value={row.inUse} />
          </dd>
        </div>
        <div>
          <dt className="font-semibold">Saved for next launch</dt>
          <dd>{row.differs ? <Value value={row.saved} /> : <span>{row.saved === null ? "not set" : "the same folder"}</span>}</dd>
        </div>
      </dl>
      {savedNotInUse ? <p>This saved folder applies the next time you start ALIBI.</p> : null}
      {row.problem !== null ? (
        <p role="alert" className="text-status-error">
          The saved folder cannot be used: {folderProblemMessage(row.problem)} (
          <code className="font-mono">{row.problem}</code>).
        </p>
      ) : null}
      {row.startProblem !== null ? (
        <p role="alert" className="text-status-error">
          It was not used at this start: {folderProblemMessage(row.startProblem)} (
          <code className="font-mono">{row.startProblem}</code>).
        </p>
      ) : null}
      {row.neededForQueue ? (
        <>
          <Statement>{LEDGER_FOLDER_WARNING}</Statement>
          <p className="text-sm">
            Back up the ledger folder, and do not put it in a synced folder.
          </p>
        </>
      ) : null}
      <div>
        <button
          type="button"
          className="border border-border px-3 py-1"
          disabled={!enabled || busy}
          aria-label={`Choose the ${row.label} folder`}
          onClick={() => onChoose(row.kind)}
        >
          Choose
        </button>
      </div>
    </li>
  );
}

function PickFeedback({ pick }: { pick: PickState }) {
  switch (pick.phase) {
    case "idle":
      return null;
    case "picking":
      return <p role="status">The folder dialog is open.</p>;
    case "cancelled":
      return <p role="status">No folder was chosen. Nothing changed.</p>;
    case "failed":
      return <Refusal failure={pick.failure} />;
    case "saved":
      return (
        <div role="status" className="grid gap-1">
          <Statement>{SAVED_STATEMENT}</Statement>
          <p>
            Saved folder: <PlainText text={pick.display} />
          </p>
        </div>
      );
  }
}

export function FoldersScreen({ view, pick, enabled, onChoose }: FoldersScreenProps) {
  const busy = pick.phase === "picking";
  return (
    <section aria-labelledby="folders-title" className="grid gap-3">
      <h2 id="folders-title" className="text-xl font-semibold">
        Folders
      </h2>
      <PickFeedback pick={pick} />
      {view.state === "loading" ? <p role="status">Reading the folder settings.</p> : null}
      {view.state === "not_ready" ? (
        <p role="status">
          ALIBI has not decided the folders yet because the sidecar is still starting. Nothing is
          shown as set or not set until it has.
        </p>
      ) : null}
      {view.state === "unavailable" ? <Refusal failure={view.failure} /> : null}
      {view.state === "loaded" ? (
        <>
          {view.savedFileInvalid ? (
            <p role="alert" className="text-status-error">
              The saved folders file cannot be read now, so every saved folder will be ignored at the
              next start.
            </p>
          ) : null}
          {view.ignoredAtStart ? (
            <p role="alert" className="text-status-error">
              The saved folders file could not be read at this start, so no saved folder is in use.
            </p>
          ) : null}
          <ul className="grid gap-3">
            {view.rows.map((row) => (
              <Row key={row.kind} row={row} busy={busy} enabled={enabled} onChoose={onChoose} />
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}
