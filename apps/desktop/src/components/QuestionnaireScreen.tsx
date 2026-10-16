// The questionnaire flow (M6_PLAN C.4, docs/DESIGN.md 4.1, 4.5 and 7). Presentational: props only.
//
// What is never here: an ETA, a percent, a progress bar, the word "stuck", a link, a status label
// that did not come from status.ts. File names, sheet names, the model name and dropped part names
// are untrusted and shown through PlainText. Warnings and reason codes from the sidecar are shown
// only when they are plain code words (`safeCode`), else as a fixed "unrecognised" line.

import {
  NO_LEDGER_BEFORE_START,
  NO_RECORD_AFTER_RUN,
  NO_WORKBOOKS,
  READING_ONLY,
  REVIEW_QUEUE_PLACEHOLDER,
  RUN_SPEED_LINE,
  UNKNOWN_RUN,
  UNREVIEWED_DRAFT_BADGE,
  WRITING_FILE,
} from "../fixedTexts";
import {
  COLUMN_MAX,
  HEADER_ROW_MAX,
  mappingProblemMessage,
  mappingProblems,
  type MappingDraft,
} from "../mapping";
import { safeCode } from "../messages";
import { visibleControls } from "../plainText";
import {
  isWritingFile,
  orderedCounts,
  runIsActive,
  runRecordMissing,
  secondsSinceLastAnswer,
  type InspectInfo,
  type QuestionnaireState,
  type RunSummary,
} from "../questionnaireRun";
import type { ReadinessView } from "../readiness";
import { ModelsBlock, modelsBlocked } from "./ModelsBlock";
import { PlainText } from "./PlainText";
import { Refusal } from "./Refusal";
import { Statement } from "./Statement";
import { StatusChip } from "./StatusChip";

export interface QuestionnaireScreenProps {
  state: QuestionnaireState;
  nowMs: number;
  readiness: ReadinessView;
  /** Whether a ledger folder is in use: `null` until the host has said. */
  ledgerInUse: boolean | null;
  /** False while the sidecar is not healthy. */
  enabled: boolean;
  onSelectFile: (file: string) => void;
  onInspect: () => void;
  onMappingChange: (mapping: MappingDraft) => void;
  onStart: () => void;
  onGoToIndex: () => void;
  onDismissRun: () => void;
}

/** A sidecar code word, or a fixed line for anything else. Never the raw text. */
function CodeWord({ value, kind }: { value: string; kind: string }) {
  const code = safeCode(value);
  return code === null ? (
    <span>unrecognised {kind}</span>
  ) : (
    <code className="font-mono">{code}</code>
  );
}

function numberOrNull(raw: string): number | null {
  const trimmed = raw.trim();
  return trimmed === "" ? null : Number(trimmed);
}

function MappingForm({
  mapping,
  info,
  locked,
  onChange,
}: {
  mapping: MappingDraft;
  info: InspectInfo;
  locked: boolean;
  onChange: (mapping: MappingDraft) => void;
}) {
  const problems = mappingProblems(mapping, info.sheets);
  return (
    <fieldset className="grid gap-2 border border-border p-3" disabled={locked}>
      <legend className="font-semibold">Mapping</legend>
      <p>
        {info.hasSuggestion
          ? "ALIBI suggests this mapping. You can change it."
          : "ALIBI has no suggestion. Choose the sheet and the columns."}
      </p>
      <label className="grid gap-1">
        <span>Sheet</span>
        <select
          className="border border-border px-2 py-1"
          value={mapping.sheet}
          onChange={(event) => onChange({ ...mapping, sheet: event.target.value })}
        >
          {info.sheets.map((sheet, index) => (
            <option key={`${index}:${sheet.name}`} value={sheet.name}>
              {visibleName(sheet.name)}
            </option>
          ))}
        </select>
      </label>
      <label className="grid gap-1">
        <span>Header row (1 to {HEADER_ROW_MAX})</span>
        <input
          type="number"
          className="border border-border px-2 py-1"
          value={mapping.headerRow}
          onChange={(event) =>
            onChange({ ...mapping, headerRow: Number(event.target.value) })
          }
        />
      </label>
      <label className="grid gap-1">
        <span>Question column (1 to {COLUMN_MAX})</span>
        <input
          type="number"
          className="border border-border px-2 py-1"
          value={mapping.questionCol}
          onChange={(event) =>
            onChange({ ...mapping, questionCol: Number(event.target.value) })
          }
        />
      </label>
      <label className="grid gap-1">
        <span>Id column (optional)</span>
        <input
          type="number"
          className="border border-border px-2 py-1"
          value={mapping.idCol ?? ""}
          onChange={(event) => onChange({ ...mapping, idCol: numberOrNull(event.target.value) })}
        />
      </label>
      {problems.length > 0 ? (
        <ul role="alert" className="list-disc pl-6 text-status-error">
          {problems.map((problem) => (
            <li key={problem}>{mappingProblemMessage(problem)}</li>
          ))}
        </ul>
      ) : null}
    </fieldset>
  );
}

/** An <option> holds text only, so the visible-control marks are applied to the string. */
function visibleName(name: string): string {
  return visibleControls(name);
}

function InspectBlock({ info }: { info: InspectInfo }) {
  return (
    <div className="grid gap-2">
      <h3 className="text-lg font-semibold">Sheets</h3>
      <ul className="list-disc pl-6">
        {info.sheets.map((sheet, index) => (
          <li key={`${index}:${sheet.name}`}>
            <PlainText text={sheet.name} /> ({sheet.rows} rows, {sheet.columns} columns)
          </li>
        ))}
      </ul>
      {info.macroEnabled ? <p>This is a macro-enabled workbook.</p> : null}
      <p>
        When the answers file is written, images and charts in the workbook may be dropped. The
        summary after the run lists exactly what was dropped.
      </p>
      {info.warnings.length > 0 ? (
        <ul className="list-disc pl-6">
          {info.warnings.map((warning, index) => (
            <li key={`${index}:${warning}`}>
              Warning: <CodeWord value={warning} kind="warning" />
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function SummaryBlock({ summary }: { summary: RunSummary }) {
  const noRecord = runRecordMissing(summary);
  const otherWarnings = summary.warnings.filter(
    (w) => w !== "run_record_not_kept" && w !== "run_record_failed",
  );
  return (
    <div className="grid gap-2">
      <p>
        <span className="alibi-block border-2 border-border bg-badge-bg px-2 py-1 font-semibold text-badge-text">
          {UNREVIEWED_DRAFT_BADGE}
        </span>
      </p>
      <h4 className="font-semibold">Answers by status</h4>
      <ul className="grid gap-1">
        {orderedCounts(summary.counts).map(({ key, count }) => (
          <li key={key}>
            <StatusChip value={key} />: {count}
          </li>
        ))}
      </ul>
      {Object.keys(summary.reasonCounts).length > 0 ? (
        <>
          <h4 className="font-semibold">Reasons</h4>
          <ul className="list-disc pl-6">
            {Object.entries(summary.reasonCounts).map(([reason, count]) => (
              <li key={reason}>
                <CodeWord value={reason} kind="reason" />: {count}
              </li>
            ))}
          </ul>
        </>
      ) : null}
      <p>
        Answers file:{" "}
        {summary.fileName === null ? "none was written" : <PlainText text={summary.fileName} />}
      </p>
      <p>Rows written: {summary.rowsWritten}</p>
      <p>
        Model: <PlainText text={summary.model} />
      </p>
      <p>Took {summary.seconds.toFixed(1)} s.</p>
      {summary.droppedParts.length > 0 ? (
        <>
          <p className="font-semibold">Parts of the workbook that were dropped:</p>
          <ul className="list-disc pl-6">
            {summary.droppedParts.map(([part, before, after], index) => (
              <li key={`${index}:${part}`}>
                <PlainText text={part} /> ({before} before, {after} after)
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p>No parts of the workbook were dropped.</p>
      )}
      {otherWarnings.length > 0 ? (
        <ul className="list-disc pl-6">
          {otherWarnings.map((warning, index) => (
            <li key={`${index}:${warning}`}>
              Warning: <CodeWord value={warning} kind="warning" />
            </li>
          ))}
        </ul>
      ) : null}
      {noRecord ? <Statement>{NO_RECORD_AFTER_RUN}</Statement> : null}
      <div className="grid gap-1">
        <button type="button" className="border border-border px-3 py-1" disabled>
          Open in review queue
        </button>
        <p className="text-sm">{REVIEW_QUEUE_PLACEHOLDER}</p>
      </div>
    </div>
  );
}

function RunBlock({
  state,
  nowMs,
  onGoToIndex,
  onDismissRun,
}: {
  state: QuestionnaireState;
  nowMs: number;
  onGoToIndex: () => void;
  onDismissRun: () => void;
}) {
  const run = state.run;
  switch (run.phase) {
    case "none":
      return null;
    case "starting":
      return <p role="status">Starting the run.</p>;
    case "refused": {
      const needsIndex =
        run.failure.code === "index_not_built" || run.failure.code === "vault_changed_reindex";
      return (
        <div className="grid gap-2">
          <Refusal failure={run.failure} />
          {needsIndex ? (
            <div>
              <button type="button" className="border border-border px-3 py-1" onClick={onGoToIndex}>
                Go to Index the vault
              </button>
            </div>
          ) : null}
        </div>
      );
    }
    case "running": {
      const seconds = secondsSinceLastAnswer(run, nowMs);
      return (
        <div className="grid gap-2">
          <div role="status" aria-live="polite">
            <p className="font-semibold">
              Answered: {run.completed} / {run.total}
            </p>
            {isWritingFile(run) ? <p>{WRITING_FILE}</p> : null}
          </div>
          <p>Time since the last answer: {seconds ?? 0} s</p>
          <Statement>{RUN_SPEED_LINE}</Statement>
          {run.problem !== null ? <Refusal failure={run.problem} /> : null}
        </div>
      );
    }
    case "finished":
      return (
        <div className="grid gap-2">
          <h3 className="text-lg font-semibold">
            {run.outcome === "complete" ? "Run finished" : null}
            {run.outcome === "stopped" ? "Run stopped early" : null}
            {run.outcome === "failed" ? "Run failed" : null}
          </h3>
          {run.outcome === "stopped" ? (
            <p>
              The model stopped answering, so the rest of the questions were left blank. Answered:{" "}
              {run.completed} / {run.total}.
            </p>
          ) : null}
          {run.outcome === "failed" ? (
            <p>
              The run failed
              {run.failure !== null ? (
                <>
                  {" "}
                  (<code className="font-mono">{run.failure}</code>)
                </>
              ) : null}
              . Answered: {run.completed} / {run.total}.
            </p>
          ) : null}
          {run.summary !== null ? <SummaryBlock summary={run.summary} /> : <p>No summary was returned.</p>}
          <div>
            <button type="button" className="border border-border px-3 py-1" onClick={onDismissRun}>
              Clear this result
            </button>
          </div>
        </div>
      );
    case "unknown_run":
      return (
        <div className="grid gap-2">
          <p role="alert">{UNKNOWN_RUN}</p>
          <div>
            <button type="button" className="border border-border px-3 py-1" onClick={onDismissRun}>
              Clear this result
            </button>
          </div>
        </div>
      );
  }
}

export function QuestionnaireScreen(props: QuestionnaireScreenProps) {
  const { state, nowMs, readiness, ledgerInUse, enabled } = props;
  const active = runIsActive(state.run);
  const info = state.inspect.phase === "ready" ? state.inspect.info : null;
  const mappingIsValid =
    info !== null && state.mapping !== null && mappingProblems(state.mapping, info.sheets).length === 0;
  const showLedgerWarning = ledgerInUse !== true;
  const preStart = info !== null && (state.run.phase === "none" || state.run.phase === "refused");
  const startBlocked = !enabled || active || !mappingIsValid || modelsBlocked(readiness);

  return (
    <section aria-labelledby="questionnaire-title" className="grid gap-4">
      <h2 id="questionnaire-title" className="text-xl font-semibold">
        Questionnaire
      </h2>

      <div className="grid gap-2">
        <h3 className="text-lg font-semibold">1. Choose a workbook</h3>
        {state.files.phase === "idle" || state.files.phase === "loading" ? (
          <p role="status">Reading the file names in the questionnaires folder.</p>
        ) : null}
        {state.files.phase === "failed" ? <Refusal failure={state.files.failure} /> : null}
        {state.files.phase === "listed" && state.files.files.length === 0 ? (
          <p role="status">{NO_WORKBOOKS}</p>
        ) : null}
        {state.files.phase === "listed" && state.files.files.length > 0 ? (
          <>
            <ul className="grid gap-1">
              {state.files.files.map((file, index) => (
                <li key={`${index}:${file}`} className="flex flex-wrap items-center gap-2">
                  <PlainText text={file} />
                  {state.selected === file ? (
                    <span className="font-semibold">Selected</span>
                  ) : (
                    <button
                      type="button"
                      className="border border-border px-3 py-1"
                      disabled={!enabled || active}
                      onClick={() => props.onSelectFile(file)}
                    >
                      Use this file
                    </button>
                  )}
                </li>
              ))}
            </ul>
            {state.files.truncated ? <p>The list was cut short; only the first names are shown.</p> : null}
          </>
        ) : null}
      </div>

      {state.selected !== null ? (
        <div className="grid gap-2">
          <h3 className="text-lg font-semibold">2. Read the sheets</h3>
          <p>
            File: <PlainText text={state.selected} />
          </p>
          <div>
            <button
              type="button"
              className="border border-border px-3 py-1"
              disabled={!enabled || active || state.inspect.phase === "loading"}
              onClick={props.onInspect}
            >
              Read the sheets
            </button>
          </div>
          {state.inspect.phase === "loading" ? <p role="status">{READING_ONLY}</p> : null}
          {state.inspect.phase === "refused" ? <Refusal failure={state.inspect.failure} /> : null}
          {info !== null ? <InspectBlock info={info} /> : null}
        </div>
      ) : null}

      {info !== null && state.mapping !== null ? (
        <div className="grid gap-2">
          <h3 className="text-lg font-semibold">3. Check the mapping</h3>
          <MappingForm mapping={state.mapping} info={info} locked={active} onChange={props.onMappingChange} />
        </div>
      ) : null}

      {info !== null ? (
        <div className="grid gap-2">
          <h3 className="text-lg font-semibold">4. Start</h3>
          {preStart && showLedgerWarning ? <Statement>{NO_LEDGER_BEFORE_START}</Statement> : null}
          <ModelsBlock view={readiness} />
          <div>
            <button
              type="button"
              className="border border-border px-3 py-1"
              disabled={startBlocked}
              onClick={props.onStart}
            >
              {showLedgerWarning ? "Run anyway" : "Start the run"}
            </button>
          </div>
          <RunBlock
            state={state}
            nowMs={nowMs}
            onGoToIndex={props.onGoToIndex}
            onDismissRun={props.onDismissRun}
          />
        </div>
      ) : null}
    </section>
  );
}
