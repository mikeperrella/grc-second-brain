// The Import screen (chunk 65g; ADR-022 decision 4, Option A+; docs/DESIGN.md 7 and 11).
// Presentational: props only.
//
// Choose a draft from the exports folder (names only: the window never sends a path), choose a
// customer and the sent date, PREVIEW, then import through the in-page two-step confirm
// (ConfirmStep). The Import button stays off until a preview of exactly the current inputs has
// succeeded. A result is headed "Preview result" or "Import result" from the reply's own `preview`
// flag. File names, customer names and other Review values are untrusted text: they go through
// PlainText and LongText (React text nodes, no link). Numbers and dates are shown exactly as the
// sidecar sent them; a missing `already_changed_since_draft` is "not available", never 0.

import type { ConfirmState } from "../confirm";
import {
  BLAST_PREVIEW_LINE,
  IMPORT_APPROVAL_LINE,
  IMPORT_DATE_STATEMENT,
  IMPORT_READS_ONLY,
  IMPORT_STEP_STATEMENT,
  NO_WORKBOOKS,
  READING_ONLY,
} from "../fixedTexts";
import { customerNameOf, integerCounts, type Customer, type CustomersView } from "../ledger";
import {
  alreadyChanged,
  canImport,
  inputProblem,
  inputProblemText,
  reportKind,
  runIdOf,
  type ImportFailure,
  type ImportFields,
  type ImportReport,
  type ImportState,
  type ImportTarget,
  type InputChange,
} from "../ledgerImport";
import {
  COUNTS_LABEL,
  IMPORT_ALREADY_CHANGED,
  IMPORT_BUTTON,
  IMPORT_CHOOSE_HEADING,
  IMPORT_CONFIRM_BUTTON,
  IMPORT_CONFIRM_NEW,
  IMPORT_CREATED,
  IMPORT_CREATED_EDITED,
  IMPORT_CUSTOMER_CREATED,
  IMPORT_CUSTOMER_HEADING,
  IMPORT_CUSTOMER_ID,
  IMPORT_WOULD_CREATE,
  IMPORT_WOULD_CREATE_EDITED,
  IMPORT_DRAFT_FILE,
  IMPORT_EXISTING_CUSTOMER,
  IMPORT_EXISTING_CUSTOMER_ID,
  IMPORT_FILES_CUT_SHORT,
  IMPORT_NEEDS_PREVIEW,
  IMPORT_NEW_CUSTOMER_NAME,
  IMPORT_NEW_LABEL,
  IMPORT_NO,
  IMPORT_NONE,
  IMPORT_NOT_AVAILABLE,
  IMPORT_NO_EXISTING_CUSTOMER,
  IMPORT_OTHER_VALUES,
  IMPORT_OTHER_VALUE_COUNT,
  IMPORT_OTHER_VALUE_CUT,
  IMPORT_PREVIEWING,
  IMPORT_PREVIEW_BUTTON,
  IMPORT_PREVIEW_RESULT_HEADING,
  IMPORT_QUESTIONS_LABEL,
  IMPORT_READING_DRAFT,
  IMPORT_READING_FILES,
  IMPORT_RESULT_HEADING,
  IMPORT_REVIEW_COUNTS,
  IMPORT_RUN_LABEL,
  IMPORT_SELECTED,
  IMPORT_SENDING,
  IMPORT_SENT_ON_LABEL,
  IMPORT_SKIPPED,
  IMPORT_TITLE,
  IMPORT_TO_CREATE,
  IMPORT_USE_DRAFT,
  IMPORT_YES,
  READING_CUSTOMERS,
} from "../ledgerText";
import { visibleControls } from "../plainText";
import { ConfirmStep } from "./ConfirmStep";
import { LongText } from "./LongText";
import { PlainText } from "./PlainText";
import { Refusal } from "./Refusal";
import { Statement } from "./Statement";

export interface ImportScreenProps {
  state: ImportState;
  confirm: ConfirmState<ImportTarget>;
  /** The ledger's customer list (the same read the Ledger and Recall screens use). */
  customers: CustomersView;
  /** False while the sidecar is not healthy. */
  enabled: boolean;
  onChooseFile: (file: string) => void;
  onInputChange: (change: InputChange) => void;
  onPreview: () => void;
  /** Step one of the import: shows the confirm area and sends nothing. */
  onOpenImport: () => void;
  /** Step two of the import: the only control that sends it. */
  onConfirmImport: () => void;
  onCancelImport: () => void;
}

const BUTTON = "border border-border px-3 py-1 disabled:border-dashed";
// A native select or input takes its own colours unless they are set (Vercel: explicit colours).
const FIELD = "border border-border bg-surface-raised px-2 py-1 text-text";

function Counts({ counts }: { counts: readonly (readonly [string, number])[] }) {
  if (counts.length === 0) {
    return null;
  }
  return (
    <div>
      <p className="font-semibold">{COUNTS_LABEL}:</p>
      <ul className="list-disc pl-6 tabular-nums">
        {counts.map(([name, count]) => (
          <li key={name}>
            <code className="font-mono" translate="no">
              {name}
            </code>
            : {count}
          </li>
        ))}
      </ul>
    </div>
  );
}

function FailureBlock({ failure }: { failure: ImportFailure }) {
  return (
    <div className="grid gap-2">
      <Refusal failure={failure.failure} />
      {failure.existingCustomerId !== null ? (
        <p>
          {IMPORT_EXISTING_CUSTOMER_ID}: <PlainText text={failure.existingCustomerId} identifier />
        </p>
      ) : null}
      <Counts counts={failure.counts} />
    </div>
  );
}

function CodeCounts({ label, counts }: { label: string; counts: Readonly<Record<string, number>> }) {
  const entries = integerCounts(counts);
  return (
    <div>
      <p className="font-semibold">{label}:</p>
      {entries.length === 0 ? (
        <p>{IMPORT_NONE}</p>
      ) : (
        <ul className="list-disc pl-6 tabular-nums">
          {entries.map(([name, count]) => (
            <li key={name}>
              <code className="font-mono" translate="no">
                {name}
              </code>
              : {count}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** One preview or import reply. The heading is chosen from the reply's own `preview` flag. */
export function ReportBlock({ report }: { report: ImportReport }) {
  const kind = reportKind(report);
  const changed = alreadyChanged(report);
  return (
    <div className="alibi-block grid min-w-0 gap-2 border border-border p-3" data-report={kind}>
      <h3 className="text-lg font-semibold">
        {kind === "preview" ? IMPORT_PREVIEW_RESULT_HEADING : IMPORT_RESULT_HEADING}
      </h3>
      {kind === "preview" ? <Statement>{BLAST_PREVIEW_LINE}</Statement> : null}
      <ul className="grid gap-1 tabular-nums">
        <li>
          {kind === "preview" ? IMPORT_WOULD_CREATE : IMPORT_CREATED}: {report.created}
        </li>
        <li>
          {kind === "preview" ? IMPORT_WOULD_CREATE_EDITED : IMPORT_CREATED_EDITED}:{" "}
          {report.created_edited}
        </li>
        {kind === "preview" ? null : (
          <li>
            {IMPORT_CUSTOMER_CREATED}: {report.customer_created ? IMPORT_YES : IMPORT_NO}
          </li>
        )}
        {report.customer_id !== null ? (
          <li>
            {IMPORT_CUSTOMER_ID}: <PlainText text={report.customer_id} identifier />
          </li>
        ) : null}
        <li>
          {IMPORT_ALREADY_CHANGED}: {changed === null ? IMPORT_NOT_AVAILABLE : changed}
        </li>
      </ul>
      <CodeCounts label={IMPORT_SKIPPED} counts={report.skipped} />
      <CodeCounts label={IMPORT_REVIEW_COUNTS} counts={report.review} />
      <div className="min-w-0">
        <p className="font-semibold">{IMPORT_OTHER_VALUES}:</p>
        {report.other_values.length === 0 ? (
          <p>{IMPORT_NONE}</p>
        ) : (
          <ul className="grid gap-1">
            {report.other_values.map((value, index) => (
              <li key={index} className="min-w-0 break-words">
                <LongText text={value.text} />{" "}
                <span className="tabular-nums">
                  ({IMPORT_OTHER_VALUE_COUNT}: {value.count})
                </span>
                {value.truncated ? <span> ({IMPORT_OTHER_VALUE_CUT})</span> : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function FilesBlock({ props, locked }: { props: ImportScreenProps; locked: boolean }) {
  const { state } = props;
  const files = state.files;
  switch (files.phase) {
    case "idle":
    case "loading":
      return <p role="status">{IMPORT_READING_FILES}</p>;
    case "failed":
      return <Refusal failure={files.failure} />;
    case "listed":
      if (files.files.length === 0) {
        return <p role="status">{NO_WORKBOOKS}</p>;
      }
      return (
        <>
          <ul className="grid gap-1">
            {files.files.map((file, index) => (
              <li key={`${index}:${file}`} className="flex min-w-0 flex-wrap items-center gap-2">
                <PlainText text={file} identifier />
                {state.inputs.file === file ? <span className="font-semibold">{IMPORT_SELECTED}</span> : null}
                <button
                  type="button"
                  className={BUTTON}
                  disabled={!props.enabled || locked || state.draft.phase === "loading"}
                  onClick={() => props.onChooseFile(file)}
                >
                  {IMPORT_USE_DRAFT}
                </button>
              </li>
            ))}
          </ul>
          {files.truncated ? <p>{IMPORT_FILES_CUT_SHORT}</p> : null}
        </>
      );
  }
}

function DraftBlock({ state }: { state: ImportState }) {
  const draft = state.draft;
  switch (draft.phase) {
    case "none":
      return null;
    case "loading":
      return (
        <p role="status">
          {IMPORT_READING_DRAFT} ({READING_ONLY}).
        </p>
      );
    case "failed":
      return (
        <div className="grid gap-2">
          <Refusal failure={draft.failure} />
          <Counts counts={draft.counts} />
        </div>
      );
    case "loaded":
      return (
        <ul className="grid gap-1">
          <li className="min-w-0 break-words">
            {IMPORT_DRAFT_FILE}: <PlainText text={draft.fileName} identifier />
          </li>
          <li className="min-w-0 break-words">
            {IMPORT_RUN_LABEL}: <PlainText text={draft.runId} identifier />
          </li>
          <li className="tabular-nums">
            {IMPORT_QUESTIONS_LABEL}: {draft.questionCount}
          </li>
        </ul>
      );
  }
}

function CustomerFields({ props, locked }: { props: ImportScreenProps; locked: boolean }) {
  const { state, customers, enabled, onInputChange } = props;
  const inputs = state.inputs;
  const disabled = !enabled || locked;
  return (
    <div className="grid gap-3">
      {customers.phase === "listed" ? (
        <div className="grid gap-1">
          <label htmlFor="import-existing-customer" className="font-semibold">
            {IMPORT_EXISTING_CUSTOMER}
          </label>
          <select
            id="import-existing-customer"
            name="import-existing-customer"
            className={FIELD}
            disabled={disabled}
            value={inputs.existingCustomerId ?? ""}
            onChange={(event) =>
              onInputChange({
                kind: "existing_customer",
                customerId: event.target.value === "" ? null : event.target.value,
              })
            }
          >
            <option value="">{IMPORT_NO_EXISTING_CUSTOMER}</option>
            {customers.customers.map((customer, index) => (
              <option key={`${index}:${customer.customer_id}`} value={customer.customer_id}>
                {visibleControls(customer.display_name)}
              </option>
            ))}
          </select>
        </div>
      ) : customers.phase === "failed" ? (
        <Refusal failure={customers.failure} />
      ) : (
        <p role="status">{READING_CUSTOMERS}</p>
      )}
      <div className="grid gap-1">
        <label htmlFor="import-new-customer" className="font-semibold">
          {IMPORT_NEW_CUSTOMER_NAME}
        </label>
        <input
          id="import-new-customer"
          name="import-new-customer"
          type="text"
          autoComplete="off"
          spellCheck={false}
          className={FIELD}
          disabled={disabled}
          value={inputs.newCustomerName}
          onChange={(event) => onInputChange({ kind: "new_customer_name", name: event.target.value })}
        />
      </div>
      <div className="flex items-center gap-2">
        <input
          id="import-confirm-new"
          name="import-confirm-new"
          type="checkbox"
          autoComplete="off"
          disabled={disabled}
          checked={inputs.confirmNew}
          onChange={(event) => onInputChange({ kind: "confirm_new", confirmNew: event.target.checked })}
        />
        <label htmlFor="import-confirm-new">{IMPORT_CONFIRM_NEW}</label>
      </div>
      <div className="grid gap-1">
        <label htmlFor="import-sent-on" className="font-semibold">
          {IMPORT_SENT_ON_LABEL}
        </label>
        {/* A text field, not type="date": a native date field shows the date in the computer's own
            format, so the person would not see the YYYY-MM-DD text that is checked and sent. */}
        <input
          id="import-sent-on"
          name="import-sent-on"
          type="text"
          autoComplete="off"
          spellCheck={false}
          className={`${FIELD} w-fit tabular-nums`}
          disabled={disabled}
          value={inputs.sentOn}
          onChange={(event) => onInputChange({ kind: "sent_on", sentOn: event.target.value })}
        />
        <Statement>{IMPORT_DATE_STATEMENT}</Statement>
      </div>
    </div>
  );
}

function ConfirmTarget({ fields, created, customers }: { fields: ImportFields; created: number; customers: CustomersView }) {
  const list: readonly Customer[] = customers.phase === "listed" ? customers.customers : [];
  const existingId = fields.existing_customer_id;
  const existingName = existingId === undefined ? null : customerNameOf(list, existingId);
  return (
    <ul className="grid gap-1">
      <li className="min-w-0 break-words">
        {IMPORT_DRAFT_FILE}: <PlainText text={fields.file} identifier />
      </li>
      <li className="min-w-0 break-words">
        {IMPORT_RUN_LABEL}: <PlainText text={fields.run_id} identifier />
      </li>
      <li className="min-w-0 break-words">
        {IMPORT_CUSTOMER_HEADING}:{" "}
        {existingId !== undefined ? (
          <PlainText text={existingName ?? existingId} />
        ) : (
          <>
            <PlainText text={fields.new_customer_name ?? ""} /> ({IMPORT_NEW_LABEL})
          </>
        )}
      </li>
      <li className="tabular-nums">
        {IMPORT_SENT_ON_LABEL}: {fields.sent_on}
      </li>
      <li className="tabular-nums">
        {IMPORT_TO_CREATE}: {created}
      </li>
    </ul>
  );
}

export function ImportScreen(props: ImportScreenProps) {
  const { state, confirm, enabled } = props;
  const sending = state.outcome.phase === "sending";
  const confirmOpen = confirm.phase === "open";
  // While the confirm is open or an import is being sent, no input can change: what is shown is
  // what would be sent.
  const locked = confirmOpen || sending;
  const problem = inputProblem(state.inputs, runIdOf(state));
  const previewing = state.preview.phase === "loading";
  const importable = canImport(state);
  const preview = state.preview;
  const outcome = state.outcome;
  return (
    <section aria-labelledby="import-title" className="grid gap-4">
      <h2 id="import-title" className="text-xl font-semibold">
        {IMPORT_TITLE}
      </h2>
      <Statement>{IMPORT_APPROVAL_LINE}</Statement>
      <Statement>{IMPORT_READS_ONLY}</Statement>

      <div className="grid gap-2">
        <h3 className="text-lg font-semibold">{IMPORT_CHOOSE_HEADING}</h3>
        <FilesBlock props={props} locked={locked} />
        <DraftBlock state={state} />
      </div>

      <div className="grid gap-2">
        <h3 className="text-lg font-semibold">{IMPORT_CUSTOMER_HEADING}</h3>
        <CustomerFields props={props} locked={locked} />
      </div>

      <div className="grid gap-2">
        {problem !== null ? <p role="status">{inputProblemText(problem)}</p> : null}
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className={BUTTON}
            disabled={!enabled || locked || previewing || problem !== null}
            onClick={props.onPreview}
          >
            {IMPORT_PREVIEW_BUTTON}
          </button>
          <button
            type="button"
            className={BUTTON}
            disabled={!enabled || locked || !importable}
            onClick={props.onOpenImport}
          >
            {IMPORT_BUTTON}
          </button>
        </div>
        {problem === null && !importable && !previewing && !sending ? <p>{IMPORT_NEEDS_PREVIEW}</p> : null}
      </div>

      {confirm.phase === "open" ? (
        <ConfirmStep
          statement={IMPORT_STEP_STATEMENT}
          confirmLabel={IMPORT_CONFIRM_BUTTON}
          enabled={enabled && !sending}
          onConfirm={props.onConfirmImport}
          onCancel={props.onCancelImport}
          target={
            <ConfirmTarget
              fields={confirm.target.fields}
              created={confirm.target.previewCreated}
              customers={props.customers}
            />
          }
        />
      ) : null}

      {previewing ? <p role="status">{IMPORT_PREVIEWING}</p> : null}
      {preview.phase === "failed" ? <FailureBlock failure={preview} /> : null}
      {preview.phase === "previewed" ? <ReportBlock report={preview.report} /> : null}

      {outcome.phase === "sending" ? <p role="status">{IMPORT_SENDING}</p> : null}
      {outcome.phase === "failed" ? <FailureBlock failure={outcome} /> : null}
      {outcome.phase === "imported" ? <ReportBlock report={outcome.report} /> : null}
    </section>
  );
}
