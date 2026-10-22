// The Contradictions screen (chunk 65i; ADR-019 and its addendum, ADR-022 decisions 2 and 8,
// threat model F33-9, F33-10, F33-11; docs/DESIGN.md 4.5, 8 and 11.1). Presentational: props only.
//
// READ-ONLY: a run writes nothing, so there is no confirm step, no write control and no cancel
// control (the sidecar has no cancel route). Every fixed statement is the window's own constant,
// never a string from the reply. With results, the numbers-only statement and the three 11.1 lines
// are always visible. The two quotes of a finding are two blocks with one shared class string, in
// the sidecar's order; nothing says which is right. The model's explanation is the model's words,
// behind "show more" when long. `model_confidence` is not shown at all. Quotes, paths, subjects,
// numbers, explanations and the model name are plain text, never a link.

import {
  contradictionProblem,
  contradictionProblemText,
  describeFlag,
  describeModelStatus,
  describeReason,
  describeRelation,
  progressText,
  reportCountRows,
  runIsActive,
  type ContradictionReport,
  type ContradictionsState,
  type Finding,
} from "../contradictions";
import {
  CONTRADICTIONS_BOTH_QUOTES,
  CONTRADICTIONS_MODEL_VARIES,
  CONTRADICTIONS_NOT_KEPT,
  CONTRADICTIONS_NUMERIC_ONLY_STATEMENT,
} from "../fixedTexts";
import {
  CONTRA_CANCELLED_TEXT,
  CONTRA_CAP_LABEL,
  CONTRA_COMPLETE_NONE,
  CONTRA_EXPLANATION_LABEL,
  CONTRA_FINDINGS_HEADING,
  CONTRA_FLAGS_LABEL,
  CONTRA_MODEL_LABEL,
  CONTRA_MODEL_STATUS_LABEL,
  CONTRA_NOT_STARTED,
  CONTRA_NUMBERS_LABEL,
  CONTRA_QUOTE_A_LABEL,
  CONTRA_QUOTE_B_LABEL,
  CONTRA_REASONS_LABEL,
  CONTRA_RELATION_LABEL,
  CONTRA_REPORT_MISSING_TEXT,
  CONTRA_RESULT_HEADING,
  CONTRA_SCOPE_LABEL,
  CONTRA_SOURCE_LABEL,
  CONTRA_START_BUTTON,
  CONTRA_STARTING,
  CONTRA_SUBJECT_LABEL,
  CONTRA_TITLE,
  CONTRA_UNKNOWN_FLAG_TEXT,
  CONTRA_UNKNOWN_MODEL_STATUS_TEXT,
  CONTRA_UNKNOWN_REASON_TEXT,
  CONTRA_UNKNOWN_RELATION_TEXT,
  CONTRA_VERDICT_LABEL,
} from "../ledgerText";
import { describeContradictionError } from "../messages";
import { FindingStatusChip, UnknownBlock } from "./FindingStatusChip";
import { LongText } from "./LongText";
import { PlainText } from "./PlainText";
import { Refusal } from "./Refusal";
import { Statement } from "./Statement";

export interface ContradictionsScreenProps {
  state: ContradictionsState;
  /** False while the sidecar is not healthy. */
  enabled: boolean;
  onInputChange: (field: "scope" | "cap", value: string) => void;
  /** Starts a run (nothing is written). */
  onStart: () => void;
}

const BUTTON = "border border-border px-3 py-1 disabled:border-dashed";
// A native input takes its own colours unless they are set (Vercel: explicit colours).
const FIELD = "border border-border bg-surface-raised px-2 py-1 text-text";

/** The ONE class string both quote blocks use, so neither is first-among-equals. */
export const QUOTE_BLOCK_CLASS = "alibi-block grid min-w-0 content-start gap-1 border border-border p-2";

function QuoteBlock({ label, source, quote }: { label: string; source: string; quote: string }) {
  return (
    <div className={QUOTE_BLOCK_CLASS} data-quote="block">
      <p className="font-semibold">{label}</p>
      <p className="min-w-0 break-words">
        {CONTRA_SOURCE_LABEL}: <PlainText text={source} identifier />
      </p>
      <p className="min-w-0 break-words">
        <LongText text={quote} />
      </p>
    </div>
  );
}

function LabelList<T extends string>({
  label,
  values,
  describe,
  unknownText,
}: {
  label: string;
  values: readonly string[];
  describe: (value: string) => { kind: "known"; value: T; label: string } | { kind: "unknown" };
  unknownText: string;
}) {
  if (values.length === 0) {
    return null;
  }
  return (
    <div className="min-w-0">
      <p className="font-semibold">{label}:</p>
      <ul className="grid gap-1">
        {values.map((value, index) => {
          const description = describe(value);
          return (
            <li key={`${index}:${value}`}>
              {description.kind === "unknown" ? (
                <UnknownBlock text={unknownText} />
              ) : (
                <span className="alibi-block inline-block border border-border px-2 py-1">{description.label}</span>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Verdict({ verdict }: { verdict: NonNullable<Finding["verdict"]> }) {
  const relation = describeRelation(verdict.relation);
  return (
    <div className="grid min-w-0 gap-1">
      <p className="font-semibold">{CONTRA_VERDICT_LABEL}:</p>
      <p className="min-w-0">
        {CONTRA_RELATION_LABEL}:{" "}
        {relation.kind === "unknown" ? <UnknownBlock text={CONTRA_UNKNOWN_RELATION_TEXT} /> : relation.label}
      </p>
      <div className="alibi-block grid min-w-0 gap-1 border border-border p-2" data-model-words="block">
        <p className="font-semibold">{CONTRA_EXPLANATION_LABEL}:</p>
        <p className="min-w-0 break-words">
          <LongText text={verdict.explanation} />
        </p>
      </div>
    </div>
  );
}

function FindingView({ finding }: { finding: Finding }) {
  return (
    <li className="alibi-block grid min-w-0 gap-2 border border-border p-3" data-finding={finding.finding_id}>
      <div>
        <FindingStatusChip value={finding.status} />
      </div>
      <p className="min-w-0 break-words">
        {CONTRA_SUBJECT_LABEL}: <PlainText text={finding.subject} />
      </p>
      <div className="min-w-0">
        <p className="font-semibold">{CONTRA_NUMBERS_LABEL}:</p>
        <ul className="grid gap-1">
          {finding.numbers.map((value, index) => (
            <li key={`${index}:${value}`} className="min-w-0 break-words">
              <PlainText text={value} />
            </li>
          ))}
        </ul>
      </div>
      <div className="grid min-w-0 gap-3 md:grid-cols-2">
        <QuoteBlock label={CONTRA_QUOTE_A_LABEL} source={finding.source_a} quote={finding.quote_a} />
        <QuoteBlock label={CONTRA_QUOTE_B_LABEL} source={finding.source_b} quote={finding.quote_b} />
      </div>
      <LabelList
        label={CONTRA_REASONS_LABEL}
        values={finding.reasons}
        describe={describeReason}
        unknownText={CONTRA_UNKNOWN_REASON_TEXT}
      />
      <LabelList
        label={CONTRA_FLAGS_LABEL}
        values={finding.flags}
        describe={describeFlag}
        unknownText={CONTRA_UNKNOWN_FLAG_TEXT}
      />
      {finding.verdict === null ? null : <Verdict verdict={finding.verdict} />}
    </li>
  );
}

function Report({ report }: { report: ContradictionReport }) {
  const modelStatus = describeModelStatus(report.model_status);
  return (
    <div className="grid min-w-0 gap-3" data-report="contradictions">
      <h3 className="text-lg font-semibold">{CONTRA_RESULT_HEADING}</h3>
      <Statement>{CONTRADICTIONS_NUMERIC_ONLY_STATEMENT}</Statement>
      <Statement>{CONTRADICTIONS_MODEL_VARIES}</Statement>
      <Statement>{CONTRADICTIONS_NOT_KEPT}</Statement>
      <Statement>{CONTRADICTIONS_BOTH_QUOTES}</Statement>
      <ul className="grid gap-1">
        <li className="min-w-0 break-words">
          {CONTRA_MODEL_LABEL}: <PlainText text={report.model} identifier />
        </li>
        <li>
          {CONTRA_MODEL_STATUS_LABEL}:{" "}
          {modelStatus.kind === "unknown" ? <UnknownBlock text={CONTRA_UNKNOWN_MODEL_STATUS_TEXT} /> : modelStatus.label}
        </li>
      </ul>
      <ul className="grid gap-1 tabular-nums">
        {reportCountRows(report).map(([label, value]) => (
          <li key={label}>
            {label}: {value}
          </li>
        ))}
      </ul>
      <h3 className="text-lg font-semibold">{CONTRA_FINDINGS_HEADING}</h3>
      {report.findings.length === 0 ? (
        <p role="status">{CONTRA_COMPLETE_NONE}</p>
      ) : (
        <ul className="grid gap-3">
          {report.findings.map((finding, index) => (
            <FindingView key={`${index}:${finding.finding_id}`} finding={finding} />
          ))}
        </ul>
      )}
    </div>
  );
}

function RunBlock({ state }: { state: ContradictionsState }) {
  const run = state.run;
  switch (run.phase) {
    case "none":
      return <p role="status">{CONTRA_NOT_STARTED}</p>;
    case "starting":
      return <p role="status">{CONTRA_STARTING}</p>;
    case "refused":
      return <Refusal failure={run.failure} />;
    case "running":
      return (
        <div className="grid gap-2">
          <p role="status" className="tabular-nums">
            {progressText(run.pairsDone, run.pairsTotal)}
          </p>
          {run.problem === null ? null : <Refusal failure={run.problem} />}
        </div>
      );
    case "complete":
      return <Report report={run.report} />;
    case "report_missing":
      return (
        <p
          role="alert"
          className="alibi-block border-2 border-status-error px-2 py-1 font-semibold text-status-error"
        >
          {CONTRA_REPORT_MISSING_TEXT}
        </p>
      );
    case "failed":
      return <Refusal failure={run.failure} />;
    case "cancelled":
      return <p role="status">{CONTRA_CANCELLED_TEXT}</p>;
    case "unknown_run":
      return <Refusal failure={describeContradictionError({ error: "run_not_found" })} />;
  }
}

export function ContradictionsScreen({ state, enabled, onInputChange, onStart }: ContradictionsScreenProps) {
  const busy = runIsActive(state.run);
  const problem = contradictionProblem(state.inputs);
  return (
    <section aria-labelledby="contradictions-title" className="grid gap-4">
      <h2 id="contradictions-title" className="text-xl font-semibold">
        {CONTRA_TITLE}
      </h2>
      <div className="grid min-w-0 gap-1">
        <label htmlFor="contradictions-scope" className="font-semibold">
          {CONTRA_SCOPE_LABEL}
        </label>
        <input
          id="contradictions-scope"
          name="contradictions-scope"
          type="text"
          autoComplete="off"
          spellCheck={false}
          translate="no"
          className={`${FIELD} min-w-0 font-mono`}
          disabled={!enabled || busy}
          value={state.inputs.scope}
          onChange={(event) => onInputChange("scope", event.target.value)}
        />
      </div>
      <div className="grid gap-1">
        <label htmlFor="contradictions-cap" className="font-semibold">
          {CONTRA_CAP_LABEL}
        </label>
        <input
          id="contradictions-cap"
          name="contradictions-cap"
          type="text"
          inputMode="numeric"
          autoComplete="off"
          spellCheck={false}
          className={`${FIELD} w-24 tabular-nums`}
          disabled={!enabled || busy}
          value={state.inputs.cap}
          onChange={(event) => onInputChange("cap", event.target.value)}
        />
      </div>
      <div className="grid gap-2">
        {problem === null ? null : <p role="status">{contradictionProblemText(problem)}</p>}
        <div>
          <button type="button" className={BUTTON} disabled={!enabled || busy || problem !== null} onClick={onStart}>
            {CONTRA_START_BUTTON}
          </button>
        </div>
      </div>
      <RunBlock state={state} />
    </section>
  );
}
