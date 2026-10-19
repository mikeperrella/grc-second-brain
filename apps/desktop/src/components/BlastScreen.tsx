// The Blast radius screen (chunk 65h; ADR-018, ADR-022 decisions 6 and 8, threat model F32-2 and
// F32-4; docs/DESIGN.md 11.1 and 11.3). Presentational: props only.
//
// READ-ONLY: a preview writes nothing, so there is no confirm step and no write control. The file is
// chosen from the cited files the sidecar listed (never typed). Exactly one of "new text" or
// "deleting the file" is chosen with a radio pair. The coverage sentence and the preview line are
// always visible above the results; the recall statement and scope are shown above the list and
// inside every item at the same weight as the state words. Every one of those is the window's own
// constant, never a string from the reply. A proposed flag that is not on the file now is marked
// "New" (text and a thicker bordered box, never colour alone), worked out by blast.ts `newFlags`.
// Customer names, paths and the proposed text are plain text, never a link.

import {
  blastProblemText,
  chosenIndex,
  detailCode,
  inputProblem,
  newFlags,
  textSize,
  type BlastItem,
  type BlastState,
  type InputChange,
} from "../blast";
import {
  BLAST_NEW_FLAG_LINE,
  BLAST_NEW_FLAG_WORD,
  BLAST_NO_MATCH_LINE,
  BLAST_PASTE_NOTE,
  BLAST_PREVIEW_LINE,
  BLAST_SCREEN_COVERAGE,
  RECALL_SCOPE,
  RECALL_STATEMENT,
} from "../fixedTexts";
import { MAX_PROPOSED_TEXT_BYTES } from "../ipc/proxy";
import { customerNameOf, integerCounts, type Customer, type CustomersView } from "../ledger";
import {
  BLAST_BYTES_LABEL,
  BLAST_BYTES_LIMIT,
  BLAST_BY_PROPOSED_STATE,
  BLAST_CHANGE_DELETE,
  BLAST_CHANGE_LEGEND,
  BLAST_CHANGE_TEXT,
  BLAST_CHECKED,
  BLAST_ITEM_FLAGS_NOW,
  BLAST_ITEM_FLAGS_PROPOSED,
  BLAST_ITEM_FOUND_AT,
  BLAST_ITEM_PROPOSED_DETAIL,
  BLAST_ITEM_STATE_NOW,
  BLAST_ITEM_STATE_PROPOSED,
  BLAST_ITEM_WOULD_CHANGE,
  BLAST_MODE_DELETE,
  BLAST_MODE_TEXT,
  BLAST_NO,
  BLAST_NO_CITED_PATHS,
  BLAST_PATH_LABEL,
  BLAST_PATH_PLACEHOLDER,
  BLAST_PATH_PROMISES,
  BLAST_PREVIEWED_CHANGE,
  BLAST_PREVIEWED_FILE,
  BLAST_PREVIEWING,
  BLAST_PREVIEW_BUTTON,
  BLAST_READING_PATHS,
  BLAST_RESULT_HEADING,
  BLAST_TEXT_LABEL,
  BLAST_TITLE,
  BLAST_WOULD_CHANGE_COUNT,
  BLAST_YES,
  COUNTS_LABEL,
  FIELD_CUSTOMER,
  FIELD_SOURCE,
  FILTER_ALL_CUSTOMERS,
  ITEM_LOCATIONS_NOT_LISTED,
  ITEM_NO_FLAGS,
  ITEM_PROMISE,
  ITEM_UNRECOGNISED_DETAIL,
  RECALL_CUSTOMER_LABEL,
  UNKNOWN_FLAG_TEXT,
} from "../ledgerText";
import { visibleControls } from "../plainText";
import { describeRecallFlag } from "../recall";
import { lineRangeText } from "../reviewQueue";
import { PlainText } from "./PlainText";
import { RecallStateChip } from "./RecallStateChip";
import { Refusal } from "./Refusal";
import { Statement } from "./Statement";

export interface BlastScreenProps {
  state: BlastState;
  /** The ledger's customer list (the same read the Ledger, Recall and Import screens use). */
  customers: CustomersView;
  /** False while the sidecar is not healthy. */
  enabled: boolean;
  onInputChange: (change: InputChange) => void;
  /** The preview: writes nothing. */
  onPreview: () => void;
}

const BUTTON = "border border-border px-3 py-1 disabled:border-dashed";
// A native select or textarea takes its own colours unless they are set (Vercel: explicit colours).
const FIELD = "border border-border bg-surface-raised px-2 py-1 text-text";

/** The recall statement and scope, from the window's constants (never from a reply). */
function RecallStatements() {
  return (
    <div className="grid gap-2">
      <Statement>{RECALL_STATEMENT}</Statement>
      <Statement>{RECALL_SCOPE}</Statement>
    </div>
  );
}

function Counts({ label, counts }: { label: string; counts: readonly (readonly [string, number])[] }) {
  if (counts.length === 0) {
    return null;
  }
  return (
    <div>
      <p className="font-semibold">{label}:</p>
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

/** One list of flags. A flag in `marked` (a new one) gets the word "New" in a thicker box. */
function FlagList({ label, flags, marked }: { label: string; flags: readonly string[]; marked: readonly string[] }) {
  if (flags.length === 0) {
    return (
      <p>
        {label}: {ITEM_NO_FLAGS}
      </p>
    );
  }
  return (
    <div className="min-w-0">
      <p className="font-semibold">{label}:</p>
      <ul className="grid gap-1">
        {flags.map((value, index) => {
          const description = describeRecallFlag(value);
          if (description.kind === "unknown") {
            return (
              <li key={`${index}:${value}`}>
                <span
                  role="alert"
                  className="alibi-block inline-block border-2 border-status-error px-2 py-1 font-semibold text-status-error"
                >
                  {UNKNOWN_FLAG_TEXT}
                </span>
              </li>
            );
          }
          return (
            <li key={`${index}:${value}`}>
              {marked.includes(value) ? (
                <span className="alibi-block inline-flex flex-wrap items-center gap-1 border-2 border-border px-2 py-1" data-flag="new">
                  <span className="font-semibold">{BLAST_NEW_FLAG_WORD}</span>
                  <span>{description.label}</span>
                </span>
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

function Item({ item, customers }: { item: BlastItem; customers: readonly Customer[] }) {
  const customerName = customerNameOf(customers, item.customer_id);
  const detail = item.proposed_detail === null ? null : detailCode(item.proposed_detail);
  return (
    <li className="alibi-block grid min-w-0 gap-2 border border-border p-3" data-promise={item.promise_id}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">{BLAST_ITEM_STATE_NOW}:</span>
        <RecallStateChip value={item.state_now} />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">{BLAST_ITEM_STATE_PROPOSED}:</span>
        <RecallStateChip value={item.state_proposed} />
      </div>
      <p className="font-semibold">
        {BLAST_ITEM_WOULD_CHANGE}: {item.would_change ? BLAST_YES : BLAST_NO}
      </p>
      <RecallStatements />
      <Statement>{BLAST_PREVIEW_LINE}</Statement>
      <p className="min-w-0 break-words">
        {ITEM_PROMISE}: <PlainText text={item.promise_id} identifier />
      </p>
      <p className="min-w-0 break-words">
        {FIELD_CUSTOMER}: <PlainText text={customerName ?? item.customer_id} identifier={customerName === null} />
      </p>
      <div className="min-w-0">
        <p className="font-semibold">{FIELD_SOURCE}:</p>
        <p>
          <PlainText text={item.source_path} identifier />
        </p>
        <p className="tabular-nums">{lineRangeText(item.start_line, item.end_line)}</p>
      </div>
      {item.proposed_detail !== null ? (
        <p>
          {BLAST_ITEM_PROPOSED_DETAIL}:{" "}
          {detail === null ? (
            ITEM_UNRECOGNISED_DETAIL
          ) : (
            <code className="font-mono" translate="no">
              {detail}
            </code>
          )}
        </p>
      ) : null}
      {item.locations.length > 0 ? (
        <div>
          <p className="font-semibold">{BLAST_ITEM_FOUND_AT}:</p>
          <ul className="grid gap-1 tabular-nums">
            {item.locations.map((location, index) => (
              <li key={`${index}:${location.start_line}:${location.end_line}`}>
                {lineRangeText(location.start_line, location.end_line)}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {item.locations_not_listed > 0 ? (
        <p className="tabular-nums">
          {ITEM_LOCATIONS_NOT_LISTED}: {item.locations_not_listed}
        </p>
      ) : null}
      <div className="flex flex-wrap items-start gap-6">
        <FlagList label={BLAST_ITEM_FLAGS_NOW} flags={item.flags_now} marked={[]} />
        <FlagList label={BLAST_ITEM_FLAGS_PROPOSED} flags={item.flags} marked={newFlags(item.flags, item.flags_now)} />
      </div>
    </li>
  );
}

function PathBlock({ props, busy }: { props: BlastScreenProps; busy: boolean }) {
  const { state, enabled, onInputChange } = props;
  const paths = state.paths;
  switch (paths.phase) {
    case "idle":
    case "loading":
      return <p role="status">{BLAST_READING_PATHS}</p>;
    case "failed":
      return (
        <div className="grid gap-2">
          <Refusal failure={paths.failure} />
          <Counts label={COUNTS_LABEL} counts={paths.counts} />
        </div>
      );
    case "listed": {
      if (paths.paths.length === 0) {
        return <p role="status">{BLAST_NO_CITED_PATHS}</p>;
      }
      const chosen = chosenIndex(state);
      return (
        <div className="grid min-w-0 gap-1">
          <label htmlFor="blast-path" className="font-semibold">
            {BLAST_PATH_LABEL}
          </label>
          <select
            id="blast-path"
            name="blast-path"
            className={`${FIELD} min-w-0`}
            translate="no"
            disabled={!enabled || busy}
            value={chosen === null ? "" : String(chosen)}
            onChange={(event) =>
              onInputChange({ kind: "path", index: event.target.value === "" ? null : Number(event.target.value) })
            }
          >
            <option value="">{BLAST_PATH_PLACEHOLDER}</option>
            {paths.paths.map((entry, index) => (
              <option key={`${index}:${entry.source_path}`} value={String(index)}>
                {`${visibleControls(entry.source_path)} (${BLAST_PATH_PROMISES}: ${entry.promise_count})`}
              </option>
            ))}
          </select>
        </div>
      );
    }
  }
}

function ChangeBlock({ props, busy }: { props: BlastScreenProps; busy: boolean }) {
  const { state, enabled, onInputChange } = props;
  const inputs = state.inputs;
  const size = textSize(inputs.proposedText);
  const disabled = !enabled || busy;
  return (
    <fieldset className="grid min-w-0 gap-2 border border-border p-3">
      <legend className="px-1 font-semibold">{BLAST_CHANGE_LEGEND}</legend>
      {/* The label wraps its radio, so the control and its words are one hit target (no gap). */}
      <label className="flex w-fit items-center gap-2">
        <input
          id="blast-mode-text"
          name="blast-mode"
          type="radio"
          autoComplete="off"
          disabled={disabled}
          checked={inputs.mode === "text"}
          onChange={() => onInputChange({ kind: "mode", mode: "text" })}
        />
        <span>{BLAST_MODE_TEXT}</span>
      </label>
      <label className="flex w-fit items-center gap-2">
        <input
          id="blast-mode-delete"
          name="blast-mode"
          type="radio"
          autoComplete="off"
          disabled={disabled}
          checked={inputs.mode === "delete"}
          onChange={() => onInputChange({ kind: "mode", mode: "delete" })}
        />
        <span>{BLAST_MODE_DELETE}</span>
      </label>
      <div className="grid min-w-0 gap-1">
        <label htmlFor="blast-text" className="font-semibold">
          {BLAST_TEXT_LABEL}
        </label>
        <Statement>{BLAST_PASTE_NOTE}</Statement>
        <textarea
          id="blast-text"
          name="blast-text"
          rows={10}
          autoComplete="off"
          spellCheck={false}
          translate="no"
          aria-describedby="blast-bytes"
          className={`${FIELD} min-w-0 w-full font-mono`}
          disabled={disabled || inputs.mode !== "text"}
          value={inputs.proposedText}
          onChange={(event) => onInputChange({ kind: "text", text: event.target.value })}
        />
        <p id="blast-bytes" className="tabular-nums">
          {BLAST_BYTES_LABEL}: {size.bytes} ({BLAST_BYTES_LIMIT} {MAX_PROPOSED_TEXT_BYTES})
        </p>
      </div>
    </fieldset>
  );
}

function CustomerBlock({ props, busy }: { props: BlastScreenProps; busy: boolean }) {
  const { state, customers, enabled, onInputChange } = props;
  if (customers.phase !== "listed") {
    return null;
  }
  return (
    <div className="grid gap-1">
      <label htmlFor="blast-customer" className="font-semibold">
        {RECALL_CUSTOMER_LABEL}
      </label>
      <select
        id="blast-customer"
        name="blast-customer"
        className={`${FIELD} w-fit`}
        disabled={!enabled || busy}
        value={state.inputs.customerId ?? ""}
        onChange={(event) =>
          onInputChange({ kind: "customer", customerId: event.target.value === "" ? null : event.target.value })
        }
      >
        <option value="">{FILTER_ALL_CUSTOMERS}</option>
        {customers.customers.map((customer, index) => (
          <option key={`${index}:${customer.customer_id}`} value={customer.customer_id}>
            {visibleControls(customer.display_name)}
          </option>
        ))}
      </select>
    </div>
  );
}

function Results({ props }: { props: BlastScreenProps }) {
  const preview = props.state.preview;
  const customers: readonly Customer[] = props.customers.phase === "listed" ? props.customers.customers : [];
  switch (preview.phase) {
    case "none":
      return null;
    case "loading":
      return <p role="status">{BLAST_PREVIEWING}</p>;
    case "failed":
      return (
        <div className="grid gap-2">
          <Refusal failure={preview.failure} />
          <Counts label={COUNTS_LABEL} counts={preview.counts} />
        </div>
      );
    case "previewed": {
      const report = preview.report;
      return (
        <div className="grid min-w-0 gap-3" data-report="preview">
          <h3 className="text-lg font-semibold">{BLAST_RESULT_HEADING}</h3>
          <Statement>{BLAST_PREVIEW_LINE}</Statement>
          <RecallStatements />
          <Statement>{BLAST_NEW_FLAG_LINE}</Statement>
          <ul className="grid gap-1">
            <li className="min-w-0 break-words">
              {BLAST_PREVIEWED_FILE}: <PlainText text={preview.path} identifier />
            </li>
            <li>
              {BLAST_PREVIEWED_CHANGE}: {preview.mode === "text" ? BLAST_CHANGE_TEXT : BLAST_CHANGE_DELETE}
            </li>
          </ul>
          <p className="tabular-nums">
            {BLAST_CHECKED}: {report.checked} · {BLAST_WOULD_CHANGE_COUNT}: {report.would_change}
          </p>
          <Counts label={BLAST_BY_PROPOSED_STATE} counts={integerCounts(report.counts)} />
          {report.items.length === 0 ? (
            <p role="status">{BLAST_NO_MATCH_LINE}</p>
          ) : (
            <ul className="grid gap-3">
              {report.items.map((item, index) => (
                <Item key={`${index}:${item.promise_id}`} item={item} customers={customers} />
              ))}
            </ul>
          )}
        </div>
      );
    }
  }
}

export function BlastScreen(props: BlastScreenProps) {
  const { state, enabled } = props;
  const busy = state.preview.phase === "loading";
  const problem = inputProblem(state.inputs);
  const pathsListed = state.paths.phase === "listed" && state.paths.paths.length > 0;
  return (
    <section aria-labelledby="blast-title" className="grid gap-4">
      <h2 id="blast-title" className="text-xl font-semibold">
        {BLAST_TITLE}
      </h2>
      <Statement>{BLAST_SCREEN_COVERAGE}</Statement>
      <Statement>{BLAST_PREVIEW_LINE}</Statement>
      <PathBlock props={props} busy={busy} />
      <ChangeBlock props={props} busy={busy} />
      <CustomerBlock props={props} busy={busy} />
      <div className="grid gap-2">
        {pathsListed && problem !== null ? <p role="status">{blastProblemText(problem)}</p> : null}
        <div>
          <button
            type="button"
            className={BUTTON}
            disabled={!enabled || busy || !pathsListed || problem !== null}
            onClick={props.onPreview}
          >
            {BLAST_PREVIEW_BUTTON}
          </button>
        </div>
      </div>
      <Results props={props} />
    </section>
  );
}
