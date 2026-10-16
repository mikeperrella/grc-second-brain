// The "Index the vault" screen (M6_PLAN C.3, ADR-021 decision 6). Presentational: props only.
//
// Progress is a count and a local elapsed counter: no ETA, no percent. A `409 run_in_progress` is
// shown once and nothing retries (the state it leaves has no poll target). Every refusal is fixed
// words from messages.ts.

import { UNKNOWN_INDEX_JOB } from "../fixedTexts";
import type { IndexProgress, IndexState } from "../indexJob";
import { fixedErrorMessage } from "../messages";
import { secondsSince } from "../poller";
import type { ReadinessView } from "../readiness";
import { ModelsBlock, modelsBlocked } from "./ModelsBlock";
import { Refusal } from "./Refusal";

export interface IndexScreenProps {
  state: IndexState;
  nowMs: number;
  readiness: ReadinessView;
  /** Whether a vault folder is in use: `null` until the host has said. */
  vaultInUse: boolean | null;
  /** False while the sidecar is not healthy. */
  enabled: boolean;
  onStart: () => void;
}

function Counts({ progress }: { progress: IndexProgress }) {
  return (
    <ul className="list-disc pl-6">
      <li>Files indexed: {progress.filesIndexed}</li>
      <li>Files unchanged: {progress.filesUnchanged}</li>
      <li>Files removed from the index: {progress.filesRemoved}</li>
      <li>Files that failed: {progress.filesFailed}</li>
      <li>Chunks embedded: {progress.chunksEmbedded}</li>
    </ul>
  );
}

export function IndexScreen({ state, nowMs, readiness, vaultInUse, enabled, onStart }: IndexScreenProps) {
  const working = state.phase === "starting" || state.phase === "running";
  const noVault = vaultInUse === false;
  const blocked = !enabled || working || noVault || modelsBlocked(readiness);
  return (
    <section aria-labelledby="index-title" className="grid gap-3">
      <h2 id="index-title" className="text-xl font-semibold">
        Index the vault
      </h2>
      <p>
        This reads the Markdown files in the vault and builds the search index. The vault files are
        not changed.
      </p>
      {noVault ? (
        <p role="alert" className="text-status-error">
          {fixedErrorMessage("vault_root_not_configured")} (
          <code className="font-mono">vault_root_not_configured</code>)
        </p>
      ) : null}
      {vaultInUse === null ? <p>The vault folder has not been read yet.</p> : null}
      <ModelsBlock view={readiness} />
      <div>
        <button type="button" className="border border-border px-3 py-1" disabled={blocked} onClick={onStart}>
          Index the vault
        </button>
      </div>
      {state.phase === "starting" ? <p role="status">Starting the index job.</p> : null}
      {state.phase === "running" ? (
        <div className="grid gap-1">
          <p role="status" aria-live="polite">
            Files done: {state.progress.filesDone} / {state.progress.filesTotal}
          </p>
          <p>Running for {secondsSince(nowMs, state.startedAtMs)} s.</p>
          <Counts progress={state.progress} />
          {state.problem !== null ? <Refusal failure={state.problem} /> : null}
        </div>
      ) : null}
      {state.phase === "finished" ? (
        <div className="grid gap-1">
          <p role="status" className="font-semibold">
            {state.outcome === "complete" ? "Index finished." : null}
            {state.outcome === "incomplete"
              ? "Index finished with problems. Some files were not indexed; see the counts."
              : null}
            {state.outcome === "failed" ? "Index failed." : null}
          </p>
          {state.failure !== null ? (
            <p>
              Code: <code className="font-mono">{state.failure}</code>
            </p>
          ) : null}
          <p>
            Files done: {state.progress.filesDone} / {state.progress.filesTotal}
          </p>
          <Counts progress={state.progress} />
        </div>
      ) : null}
      {state.phase === "refused" ? <Refusal failure={state.failure} /> : null}
      {state.phase === "unknown_job" ? <p role="alert">{UNKNOWN_INDEX_JOB}</p> : null}
    </section>
  );
}
