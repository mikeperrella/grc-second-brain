// The review queue (ADR-021, docs/DESIGN.md 5 and 7). Presentational: props only.
//
// The queue only reads. It has no approve control and no control that writes a workbook; the
// approval line says so in fixed words and is always shown. File names, the model name, questions,
// answers and quotes are untrusted and shown as plain text. There is no progress bar, no ETA and
// no link.

import {
  APPROVAL_LINE,
  NO_WORKBOOKS,
  READING_ONLY,
  UNREVIEWED_DRAFT_BADGE,
} from "../fixedTexts";
import type { ReviewQueueState } from "../reviewQueue";
import { DraftRowView } from "./DraftRowView";
import { PlainText } from "./PlainText";
import { Refusal } from "./Refusal";
import { Statement } from "./Statement";

export interface ReviewQueueScreenProps {
  state: ReviewQueueState;
  /** False while the sidecar is not healthy. */
  enabled: boolean;
  onOpenFile: (file: string) => void;
}

function DraftBlock({ state }: { state: ReviewQueueState }) {
  const draft = state.draft;
  switch (draft.phase) {
    case "none":
      return null;
    case "loading":
      return <p role="status">Reading the draft ({READING_ONLY}).</p>;
    case "refused":
      return (
        <div className="grid gap-2">
          <Refusal failure={draft.failure} />
          {draft.counts.length > 0 ? (
            <div>
              <p className="font-semibold">Counts:</p>
              <ul className="list-disc pl-6">
                {draft.counts.map(([name, count]) => (
                  <li key={name}>
                    <code className="font-mono">{name}</code>: {count}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      );
    case "loaded":
      return (
        <div className="grid gap-3">
          <h3 className="text-lg font-semibold">Draft</h3>
          <p>
            <span className="alibi-block border-2 border-border bg-badge-bg px-2 py-1 font-semibold text-badge-text">
              {UNREVIEWED_DRAFT_BADGE}
            </span>
          </p>
          <p>
            File: <PlainText text={draft.header.fileName} />
          </p>
          <p>
            Model: <PlainText text={draft.header.model} />
          </p>
          <p>Questions: {draft.header.questionCount}</p>
          <p>
            Run: {draft.header.complete ? "complete" : "incomplete (the run did not finish)"}
          </p>
          <ul className="grid gap-3">
            {draft.rows.map((row, index) => (
              <DraftRowView key={`${index}:${row.row}`} row={row} />
            ))}
          </ul>
        </div>
      );
  }
}

export function ReviewQueueScreen({ state, enabled, onOpenFile }: ReviewQueueScreenProps) {
  const files = state.files;
  const busy = state.draft.phase === "loading";
  return (
    <section aria-labelledby="queue-title" className="grid gap-4">
      <h2 id="queue-title" className="text-xl font-semibold">
        Review queue
      </h2>
      <Statement>{APPROVAL_LINE}</Statement>

      <div className="grid gap-2">
        <h3 className="text-lg font-semibold">Choose a draft</h3>
        {files.phase === "idle" || files.phase === "loading" ? (
          <p role="status">Reading the file names in the exports folder.</p>
        ) : null}
        {files.phase === "failed" ? <Refusal failure={files.failure} /> : null}
        {files.phase === "listed" && files.files.length === 0 ? (
          <p role="status">{NO_WORKBOOKS}</p>
        ) : null}
        {files.phase === "listed" && files.files.length > 0 ? (
          <>
            <ul className="grid gap-1">
              {files.files.map((file, index) => (
                <li key={`${index}:${file}`} className="flex flex-wrap items-center gap-2">
                  <PlainText text={file} />
                  {state.selected === file ? <span className="font-semibold">Selected</span> : null}
                  <button
                    type="button"
                    className="border border-border px-3 py-1"
                    disabled={!enabled || busy}
                    onClick={() => onOpenFile(file)}
                  >
                    Open this draft
                  </button>
                </li>
              ))}
            </ul>
            {files.truncated ? <p>The list was cut short; only the first names are shown.</p> : null}
          </>
        ) : null}
      </div>

      <DraftBlock state={state} />
    </section>
  );
}
