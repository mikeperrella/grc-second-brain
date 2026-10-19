// The Ledger screen (ADR-022, docs/DESIGN.md 7 and 11). Presentational: props only.
//
// It lists the customers and one page of promises, and lets a person void a promise through the
// in-page two-step confirm (ConfirmStep). Customer names, questions, answers, quotes and paths are
// untrusted text: they go through PlainText and LongText (React text nodes, no link). Dates and
// numbers are shown exactly as the sidecar sent them. A ledger that is not configured is a refusal
// block, never "0" and never an empty list. There is no truncation of statements, status words or
// reasons.

import type { ConfirmState } from "../confirm";
import {
  LEDGER_IMMUTABLE_NOTE,
  LEDGER_PROMISE_DEFINITION,
  PROMISES_PAGE_TOO_LARGE,
  VOID_STEP_STATEMENT,
} from "../fixedTexts";
import { VOID_REASONS } from "../ipc/proxy";
import {
  PAGE_SIZES,
  customerNameOf,
  hasNextPage,
  smallerPageSizes,
  voidReasonLabel,
  type Customer,
  type FilterChange,
  type LedgerState,
  type PromiseRow,
} from "../ledger";
import type { VoidReason, VoidTarget } from "../ledgerActions";
import {
  CUSTOMERS_HEADING,
  FIELD_ANSWER,
  FIELD_CREATED_ON,
  FIELD_CUSTOMER,
  FIELD_QUESTION,
  FIELD_QUOTE,
  FIELD_SENT_ON,
  FIELD_SOURCE,
  FILTER_ALL_CUSTOMERS,
  FILTER_CUSTOMER_LABEL,
  FILTER_INCLUDE_VOIDED,
  FILTER_PAGE_SIZE_LABEL,
  LEDGER_TITLE,
  NEXT_PAGE,
  NO_CUSTOMERS,
  NO_PROMISES,
  PAGE_LABEL,
  PREVIOUS_PAGE,
  PROMISES_HEADING,
  PROMISE_EDITED_LABEL,
  PROMISE_VOIDED_LABEL,
  READING_CUSTOMERS,
  READING_PROMISES,
  TOTAL_LABEL,
  USE_PAGE_SIZE,
  VOID_BUTTON,
  VOID_CONFIRM_BUTTON,
  VOID_DONE,
  VOID_REASON_LABEL,
  VOID_REASON_PLACEHOLDER,
  VOID_SENDING,
} from "../ledgerText";
import { visibleControls } from "../plainText";
import { lineRangeText } from "../reviewQueue";
import { ConfirmStep } from "./ConfirmStep";
import { LongText } from "./LongText";
import { PlainText } from "./PlainText";
import { Refusal } from "./Refusal";
import { Statement } from "./Statement";
import { StatusChip } from "./StatusChip";

export interface LedgerScreenProps {
  state: LedgerState;
  confirm: ConfirmState<VoidTarget>;
  /** False while the sidecar is not healthy. */
  enabled: boolean;
  onFilterChange: (change: FilterChange) => void;
  onChooseReason: (promiseId: string, reason: VoidReason | null) => void;
  /** Step one of a void: shows the confirm area and sends nothing. */
  onOpenVoid: (target: VoidTarget) => void;
  /** Step two of a void: the only control that sends it. */
  onConfirmVoid: () => void;
  onCancelVoid: () => void;
}

const BUTTON = "border border-border px-3 py-1 disabled:border-dashed";
// A native select takes its own colours unless they are set (Vercel: explicit background and text).
const FIELD = "border border-border bg-surface-raised px-2 py-1 text-text";

function isVoidReason(value: string): value is VoidReason {
  return (VOID_REASONS as readonly string[]).includes(value);
}

function CustomersBlock({ state }: { state: LedgerState }) {
  const view = state.customers;
  switch (view.phase) {
    case "idle":
    case "loading":
      return <p role="status">{READING_CUSTOMERS}</p>;
    case "failed":
      return <Refusal failure={view.failure} />;
    case "listed":
      return view.customers.length === 0 ? (
        <p role="status">{NO_CUSTOMERS}</p>
      ) : (
        <ul className="grid gap-1">
          {view.customers.map((customer, index) => (
            <li key={`${index}:${customer.customer_id}`}>
              <PlainText text={customer.display_name} />
            </li>
          ))}
        </ul>
      );
  }
}

function Filters({
  state,
  enabled,
  onFilterChange,
}: {
  state: LedgerState;
  enabled: boolean;
  onFilterChange: (change: FilterChange) => void;
}) {
  const customers: readonly Customer[] = state.customers.phase === "listed" ? state.customers.customers : [];
  const filter = state.filter;
  return (
    <div className="flex flex-wrap items-end gap-3">
      {state.customers.phase === "listed" ? (
        <div className="grid gap-1">
          <label htmlFor="ledger-customer" className="font-semibold">
            {FILTER_CUSTOMER_LABEL}
          </label>
          <select
            id="ledger-customer"
            name="ledger-customer"
            className={FIELD}
            disabled={!enabled}
            value={filter.customerId ?? ""}
            onChange={(event) =>
              onFilterChange({
                kind: "customer",
                customerId: event.target.value === "" ? null : event.target.value,
              })
            }
          >
            <option value="">{FILTER_ALL_CUSTOMERS}</option>
            {customers.map((customer, index) => (
              <option key={`${index}:${customer.customer_id}`} value={customer.customer_id}>
                {visibleControls(customer.display_name)}
              </option>
            ))}
          </select>
        </div>
      ) : null}
      <div className="flex items-center gap-2">
        <input
          id="ledger-include-voided"
          name="ledger-include-voided"
          type="checkbox"
          autoComplete="off"
          disabled={!enabled}
          checked={filter.includeVoided}
          onChange={(event) => onFilterChange({ kind: "include_voided", includeVoided: event.target.checked })}
        />
        <label htmlFor="ledger-include-voided">{FILTER_INCLUDE_VOIDED}</label>
      </div>
      <div className="grid gap-1">
        <label htmlFor="ledger-page-size" className="font-semibold">
          {FILTER_PAGE_SIZE_LABEL}
        </label>
        <select
          id="ledger-page-size"
          name="ledger-page-size"
          className={`${FIELD} tabular-nums`}
          disabled={!enabled}
          value={String(filter.pageSize)}
          onChange={(event) => {
            const size = PAGE_SIZES.find((candidate) => String(candidate) === event.target.value);
            if (size !== undefined) {
              onFilterChange({ kind: "page_size", pageSize: size });
            }
          }}
        >
          {PAGE_SIZES.map((size) => (
            <option key={size} value={String(size)}>
              {size}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}

function PromiseBlock({
  promise,
  state,
  confirm,
  enabled,
  onChooseReason,
  onOpenVoid,
  onConfirmVoid,
  onCancelVoid,
}: {
  promise: PromiseRow;
  state: LedgerState;
  confirm: ConfirmState<VoidTarget>;
  enabled: boolean;
  onChooseReason: (promiseId: string, reason: VoidReason | null) => void;
  onOpenVoid: (target: VoidTarget) => void;
  onConfirmVoid: () => void;
  onCancelVoid: () => void;
}) {
  const customers: readonly Customer[] = state.customers.phase === "listed" ? state.customers.customers : [];
  const customerName = customerNameOf(customers, promise.customer_id);
  const reason = state.reasons[promise.promise_id];
  const sending = state.write.phase === "sending";
  const confirming = confirm.phase === "open" && confirm.target.promiseId === promise.promise_id;
  const reasonId = `void-reason-${promise.promise_id}`;
  return (
    <li className="alibi-block grid min-w-0 gap-2 border border-border p-3" data-promise={promise.promise_id}>
      <div className="flex flex-wrap items-center gap-2">
        <PlainText text={promise.promise_id} identifier />
        {promise.voided ? (
          <span className="alibi-block border-2 border-border px-2 py-1 font-semibold">
            {PROMISE_VOIDED_LABEL}
          </span>
        ) : null}
        {promise.edited_after_draft ? <span className="font-semibold">{PROMISE_EDITED_LABEL}</span> : null}
      </div>
      <p className="min-w-0 break-words">
        {FIELD_CUSTOMER}: <PlainText text={customerName ?? promise.customer_id} />
      </p>
      <p className="tabular-nums">
        {FIELD_SENT_ON}: {promise.sent_on} · {FIELD_CREATED_ON}: {promise.created_on}
      </p>
      <div className="min-w-0">
        <p className="font-semibold">{FIELD_QUESTION}:</p>
        <LongText text={promise.question_text} />
      </div>
      <div className="min-w-0">
        <p className="font-semibold">{FIELD_ANSWER}:</p>
        <LongText text={promise.answer_text} />
      </div>
      <div>
        <StatusChip value={promise.status} />
      </div>
      <div className="grid min-w-0 gap-2 sm:grid-cols-2">
        <div className="min-w-0">
          <p className="font-semibold">{FIELD_SOURCE}:</p>
          <p>
            <PlainText text={promise.source_path} identifier />
          </p>
          <p className="tabular-nums">{lineRangeText(promise.start_line, promise.end_line)}</p>
        </div>
        <div className="min-w-0">
          <p className="font-semibold">{FIELD_QUOTE}:</p>
          <LongText text={promise.quote} />
        </div>
      </div>
      {promise.voided ? null : (
        <div className="flex flex-wrap items-end gap-2">
          <div className="grid gap-1">
            <label htmlFor={reasonId} className="font-semibold">
              {VOID_REASON_LABEL}
            </label>
            <select
              id={reasonId}
              name={reasonId}
              className={FIELD}
              // While this promise's confirm is open the reason is locked, so the reason shown is
              // always the one the confirm would send (65f tidy-up b).
              disabled={!enabled || sending || confirming}
              value={reason ?? ""}
              onChange={(event) =>
                onChooseReason(promise.promise_id, isVoidReason(event.target.value) ? event.target.value : null)
              }
            >
              <option value="">{VOID_REASON_PLACEHOLDER}</option>
              {VOID_REASONS.map((code) => (
                <option key={code} value={code}>
                  {voidReasonLabel(code)}
                </option>
              ))}
            </select>
          </div>
          <button
            type="button"
            className={BUTTON}
            disabled={!enabled || sending || reason === undefined}
            onClick={() => {
              if (reason !== undefined) {
                onOpenVoid({
                  promiseId: promise.promise_id,
                  reason,
                  customerName,
                  sourcePath: promise.source_path,
                });
              }
            }}
          >
            {VOID_BUTTON}
          </button>
        </div>
      )}
      {confirming && confirm.phase === "open" ? (
        <ConfirmStep
          statement={VOID_STEP_STATEMENT}
          confirmLabel={VOID_CONFIRM_BUTTON}
          enabled={enabled && !sending}
          onConfirm={onConfirmVoid}
          onCancel={onCancelVoid}
          target={
            <ul className="grid gap-1">
              <li>
                {FIELD_CUSTOMER}: <PlainText text={confirm.target.customerName ?? promise.customer_id} />
              </li>
              <li>
                {PROMISES_HEADING}: <PlainText text={confirm.target.promiseId} identifier />
              </li>
              <li>
                {FIELD_SOURCE}: <PlainText text={confirm.target.sourcePath} identifier />
              </li>
              <li>
                {VOID_REASON_LABEL}: {voidReasonLabel(confirm.target.reason)}
              </li>
            </ul>
          }
        />
      ) : null}
    </li>
  );
}

function PromisesBlock({
  props,
}: {
  props: LedgerScreenProps;
}) {
  const { state, onFilterChange } = props;
  const view = state.promises;
  switch (view.phase) {
    case "idle":
    case "loading":
      return <p role="status">{READING_PROMISES}</p>;
    case "failed":
      return <Refusal failure={view.failure} />;
    case "too_large":
      return (
        <div role="alert" className="grid gap-2">
          <Statement>{PROMISES_PAGE_TOO_LARGE}</Statement>
          <div className="flex flex-wrap items-center gap-2">
            {smallerPageSizes(state.filter.pageSize).map((size) => (
              <button
                key={size}
                type="button"
                className={`${BUTTON} tabular-nums`}
                disabled={!props.enabled}
                onClick={() => onFilterChange({ kind: "page_size", pageSize: size })}
              >
                {USE_PAGE_SIZE} {size}
              </button>
            ))}
          </div>
        </div>
      );
    case "listed":
      return (
        <div className="grid gap-3">
          <p className="tabular-nums">
            {TOTAL_LABEL}: {view.total} · {PAGE_LABEL} {state.filter.page}
          </p>
          {view.promises.length === 0 ? (
            <p role="status">{NO_PROMISES}</p>
          ) : (
            <ul className="grid gap-3">
              {view.promises.map((promise, index) => (
                <PromiseBlock
                  key={`${index}:${promise.promise_id}`}
                  promise={promise}
                  state={state}
                  confirm={props.confirm}
                  enabled={props.enabled}
                  onChooseReason={props.onChooseReason}
                  onOpenVoid={props.onOpenVoid}
                  onConfirmVoid={props.onConfirmVoid}
                  onCancelVoid={props.onCancelVoid}
                />
              ))}
            </ul>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              className={BUTTON}
              disabled={!props.enabled || state.filter.page <= 1}
              onClick={() => onFilterChange({ kind: "page", page: state.filter.page - 1 })}
            >
              {PREVIOUS_PAGE}
            </button>
            <button
              type="button"
              className={BUTTON}
              disabled={!props.enabled || !hasNextPage(state.filter, view.total)}
              onClick={() => onFilterChange({ kind: "page", page: state.filter.page + 1 })}
            >
              {NEXT_PAGE}
            </button>
          </div>
        </div>
      );
  }
}

export function LedgerScreen(props: LedgerScreenProps) {
  const { state, enabled, onFilterChange } = props;
  const write = state.write;
  return (
    <section aria-labelledby="ledger-title" className="grid gap-4">
      <h2 id="ledger-title" className="text-xl font-semibold">
        {LEDGER_TITLE}
      </h2>
      <Statement>{LEDGER_PROMISE_DEFINITION}</Statement>
      <Statement>{LEDGER_IMMUTABLE_NOTE}</Statement>

      <div className="grid gap-2">
        <h3 className="text-lg font-semibold">{CUSTOMERS_HEADING}</h3>
        <CustomersBlock state={state} />
      </div>

      <div className="grid gap-3">
        <h3 className="text-lg font-semibold">{PROMISES_HEADING}</h3>
        <Filters state={state} enabled={enabled} onFilterChange={onFilterChange} />
        {write.phase === "sending" && write.kind === "void" ? <p role="status">{VOID_SENDING}</p> : null}
        {write.phase === "done" && write.kind === "void" ? <p role="status">{VOID_DONE}</p> : null}
        {write.phase === "refused" && write.kind === "void" ? <Refusal failure={write.failure} /> : null}
        <PromisesBlock props={props} />
      </div>
    </section>
  );
}
