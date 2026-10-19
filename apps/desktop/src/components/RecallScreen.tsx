// The Recall screen (ADR-017 decisions 17 and 20, threat model F31-4b, docs/DESIGN.md 11).
// Presentational: props only.
//
// The sentence that checking records a change of state is always visible BEFORE the run action.
// The statement ("intact does not mean the promise is still true") and the scope are the window's
// own constants, shown above the results AND inside every item at the same weight as the state word;
// the strings in the reply are never shown. State and flag words come from exhaustive switches.
// Customer names, source paths and quotes are plain text, never a link. The acknowledge control
// exists only for an item that has a `recall_event_id`: the id sent is that item's own.

import type { ConfirmState } from "../confirm";
import {
  ACKNOWLEDGE_STATEMENT,
  RECALL_BEFORE_RUN,
  RECALL_SCOPE,
  RECALL_STATEMENT,
} from "../fixedTexts";
import { safeCode } from "../messages";
import {
  ACKNOWLEDGE_BUTTON,
  ACKNOWLEDGE_CONFIRM_BUTTON,
  ACKNOWLEDGE_DONE,
  ACKNOWLEDGE_SENDING,
  COUNTS_LABEL,
  FIELD_CUSTOMER,
  FIELD_SOURCE,
  FILTER_ALL_CUSTOMERS,
  ITEM_ACKNOWLEDGED_AS,
  ITEM_ACKNOWLEDGED_BEFORE_STATE,
  ITEM_ACKNOWLEDGEMENT,
  ITEM_ACK_CURRENT,
  ITEM_ACK_OUT_OF_DATE,
  ITEM_DETAIL,
  ITEM_FLAGS,
  ITEM_LOCATIONS_NOT_LISTED,
  ITEM_MOVED_TO,
  ITEM_NOT_ACKNOWLEDGED,
  ITEM_NOT_RECORDED,
  ITEM_NO_FLAGS,
  ITEM_PREVIOUS_STATE,
  ITEM_PROMISE,
  ITEM_RECORDED,
  ITEM_STATE,
  ITEM_UNRECOGNISED_DETAIL,
  RECALL_CHECKED,
  RECALL_CUSTOMER_LABEL,
  RECALL_EVENTS_RECORDED,
  RECALL_NO_ITEMS,
  RECALL_RESULTS_HEADING,
  RECALL_RUNNING,
  RECALL_RUN_BUTTON,
  RECALL_TITLE,
  UNKNOWN_FLAG_TEXT,
} from "../ledgerText";
import { customerNameOf, integerCounts, type Customer, type CustomersView } from "../ledger";
import type { AcknowledgeTarget } from "../ledgerActions";
import { acknowledgeableEventId, describeRecallFlag, type RecallItem, type RecallState } from "../recall";
import { visibleControls } from "../plainText";
import { lineRangeText } from "../reviewQueue";
import { ConfirmStep } from "./ConfirmStep";
import { PlainText } from "./PlainText";
import { RecallStateChip } from "./RecallStateChip";
import { Refusal } from "./Refusal";
import { Statement } from "./Statement";

export interface RecallScreenProps {
  state: RecallState;
  confirm: ConfirmState<AcknowledgeTarget>;
  /** The customers the Ledger screen's call read; the select is shown only once they are listed. */
  customers: CustomersView;
  /** False while the sidecar is not healthy. */
  enabled: boolean;
  onChooseCustomer: (customerId: string | null) => void;
  onRun: () => void;
  /** Step one of an acknowledgement: shows the confirm area and sends nothing. */
  onOpenAcknowledge: (item: RecallItem) => void;
  /** Step two: the only control that sends it. */
  onConfirmAcknowledge: () => void;
  onCancelAcknowledge: () => void;
}

const BUTTON = "border border-border px-3 py-1 disabled:border-dashed";
// A native select takes its own colours unless they are set (Vercel: explicit background and text).
const FIELD = "border border-border bg-surface-raised px-2 py-1 text-text";

/** The fixed statement and scope, from the window's constants (never from a reply). */
function RecallStatements() {
  return (
    <div className="grid gap-2">
      <Statement>{RECALL_STATEMENT}</Statement>
      <Statement>{RECALL_SCOPE}</Statement>
    </div>
  );
}

function Flags({ flags }: { flags: readonly string[] }) {
  if (flags.length === 0) {
    return <p>{ITEM_FLAGS}: {ITEM_NO_FLAGS}</p>;
  }
  return (
    <div>
      <p className="font-semibold">{ITEM_FLAGS}:</p>
      <ul className="grid gap-1">
        {flags.map((value, index) => {
          const description = describeRecallFlag(value);
          return (
            <li key={`${index}:${value}`}>
              {description.kind === "known" ? (
                <span className="alibi-block border border-border px-2 py-1">{description.label}</span>
              ) : (
                <span
                  role="alert"
                  className="alibi-block inline-block border-2 border-status-error px-2 py-1 font-semibold text-status-error"
                >
                  {UNKNOWN_FLAG_TEXT}
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Acknowledgement({ item }: { item: RecallItem }) {
  if (item.acknowledged_event_id === null) {
    return <p>{ITEM_ACKNOWLEDGEMENT}: {ITEM_NOT_ACKNOWLEDGED}</p>;
  }
  return (
    <div className="grid gap-1">
      <p className="font-semibold">{ITEM_ACKNOWLEDGEMENT}:</p>
      {item.acknowledged_state === null ? (
        <p>{ITEM_ACKNOWLEDGED_BEFORE_STATE}</p>
      ) : (
        <p className="flex flex-wrap items-center gap-2">
          {ITEM_ACKNOWLEDGED_AS}: <RecallStateChip value={item.acknowledged_state} />
        </p>
      )}
      <p className="font-semibold">{item.acknowledgement_current ? ITEM_ACK_CURRENT : ITEM_ACK_OUT_OF_DATE}</p>
    </div>
  );
}

function Item({
  item,
  customers,
  props,
}: {
  item: RecallItem;
  customers: readonly Customer[];
  props: RecallScreenProps;
}) {
  const customerName = customerNameOf(customers, item.customer_id);
  const eventId = acknowledgeableEventId(item);
  const sending = props.state.ack.phase === "sending";
  const confirming =
    props.confirm.phase === "open" &&
    props.confirm.target.promiseId === item.promise_id &&
    props.confirm.target.seenRecallEventId === eventId;
  const detail = item.detail === null ? null : safeCode(item.detail);
  return (
    <li className="alibi-block grid min-w-0 gap-2 border border-border p-3" data-promise={item.promise_id}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">{ITEM_STATE}:</span>
        <RecallStateChip value={item.state} />
      </div>
      <RecallStatements />
      <p className="min-w-0 break-words">
        {ITEM_PROMISE}: <PlainText text={item.promise_id} identifier />
      </p>
      <p className="min-w-0 break-words">
        {FIELD_CUSTOMER}: <PlainText text={customerName ?? item.customer_id} />
      </p>
      <div className="min-w-0">
        <p className="font-semibold">{FIELD_SOURCE}:</p>
        <p>
          <PlainText text={item.source_path} identifier />
        </p>
        <p className="tabular-nums">{lineRangeText(item.start_line, item.end_line)}</p>
      </div>
      {item.previous_state !== null ? (
        <p className="flex flex-wrap items-center gap-2">
          {ITEM_PREVIOUS_STATE}: <RecallStateChip value={item.previous_state} />
        </p>
      ) : null}
      {item.detail !== null ? (
        <p>
          {ITEM_DETAIL}: {detail === null ? ITEM_UNRECOGNISED_DETAIL : <code className="font-mono">{detail}</code>}
        </p>
      ) : null}
      {item.locations.length > 0 ? (
        <div>
          <p className="font-semibold">{ITEM_MOVED_TO}:</p>
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
      <Flags flags={item.flags} />
      <p>{item.event_recorded ? ITEM_RECORDED : ITEM_NOT_RECORDED}</p>
      <Acknowledgement item={item} />
      {eventId !== null ? (
        <div>
          <button
            type="button"
            className={BUTTON}
            disabled={!props.enabled || sending}
            onClick={() => props.onOpenAcknowledge(item)}
          >
            {ACKNOWLEDGE_BUTTON}
          </button>
        </div>
      ) : null}
      {confirming && props.confirm.phase === "open" ? (
        <ConfirmStep
          statement={ACKNOWLEDGE_STATEMENT}
          confirmLabel={ACKNOWLEDGE_CONFIRM_BUTTON}
          enabled={props.enabled && !sending}
          onConfirm={props.onConfirmAcknowledge}
          onCancel={props.onCancelAcknowledge}
          target={
            <ul className="grid gap-1">
              <li>
                {ITEM_PROMISE}: <PlainText text={props.confirm.target.promiseId} identifier />
              </li>
              <li>
                {FIELD_SOURCE}: <PlainText text={props.confirm.target.sourcePath} identifier />
              </li>
              <li className="flex flex-wrap items-center gap-2">
                {ITEM_STATE}: <RecallStateChip value={item.state} />
              </li>
            </ul>
          }
        />
      ) : null}
    </li>
  );
}

function Results({ props }: { props: RecallScreenProps }) {
  const run = props.state.run;
  const customers: readonly Customer[] = props.customers.phase === "listed" ? props.customers.customers : [];
  switch (run.phase) {
    case "idle":
      return null;
    case "running":
      return <p role="status">{RECALL_RUNNING}</p>;
    case "failed":
      return (
        <div className="grid gap-2">
          <Refusal failure={run.failure} />
          {run.counts.length > 0 ? (
            <div>
              <p className="font-semibold">{COUNTS_LABEL}:</p>
              <ul className="list-disc pl-6 tabular-nums">
                {run.counts.map(([name, count]) => (
                  <li key={name}>
                    <code className="font-mono">{name}</code>: {count}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      );
    case "done": {
      const report = run.report;
      return (
        <div className="grid gap-3">
          <h3 className="text-lg font-semibold">{RECALL_RESULTS_HEADING}</h3>
          <RecallStatements />
          <p className="tabular-nums">
            {RECALL_CHECKED}: {report.checked} · {RECALL_EVENTS_RECORDED}: {report.events_recorded}
          </p>
          <ul className="flex flex-wrap gap-3 tabular-nums">
            {integerCounts(report.counts).map(([name, count]) => (
              <li key={name}>
                <code className="font-mono">{name}</code>: {count}
              </li>
            ))}
          </ul>
          {report.items.length === 0 ? (
            <p role="status">{RECALL_NO_ITEMS}</p>
          ) : (
            <ul className="grid gap-3">
              {report.items.map((item, index) => (
                <Item key={`${index}:${item.promise_id}`} item={item} customers={customers} props={props} />
              ))}
            </ul>
          )}
        </div>
      );
    }
  }
}

export function RecallScreen(props: RecallScreenProps) {
  const { state, customers, enabled } = props;
  const running = state.run.phase === "running";
  const ack = state.ack;
  return (
    <section aria-labelledby="recall-title" className="grid gap-4">
      <h2 id="recall-title" className="text-xl font-semibold">
        {RECALL_TITLE}
      </h2>
      <Statement>{RECALL_BEFORE_RUN}</Statement>
      <div className="flex flex-wrap items-end gap-3">
        {customers.phase === "listed" ? (
          <div className="grid gap-1">
            <label htmlFor="recall-customer" className="font-semibold">
              {RECALL_CUSTOMER_LABEL}
            </label>
            <select
              id="recall-customer"
              name="recall-customer"
              className={FIELD}
              disabled={!enabled || running}
              value={state.customerId ?? ""}
              onChange={(event) => props.onChooseCustomer(event.target.value === "" ? null : event.target.value)}
            >
              <option value="">{FILTER_ALL_CUSTOMERS}</option>
              {customers.customers.map((customer, index) => (
                <option key={`${index}:${customer.customer_id}`} value={customer.customer_id}>
                  {visibleControls(customer.display_name)}
                </option>
              ))}
            </select>
          </div>
        ) : null}
        <button type="button" className={BUTTON} disabled={!enabled || running} onClick={props.onRun}>
          {RECALL_RUN_BUTTON}
        </button>
      </div>
      {ack.phase === "sending" ? <p role="status">{ACKNOWLEDGE_SENDING}</p> : null}
      {ack.phase === "done" ? <p role="status">{ACKNOWLEDGE_DONE}</p> : null}
      {ack.phase === "refused" ? <Refusal failure={ack.failure} /> : null}
      <Results props={props} />
    </section>
  );
}
