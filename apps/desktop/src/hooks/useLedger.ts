// Thin hook: the Ledger screen. Three read calls (`ledger_customers`, `ledger_promises`) and one
// write (`ledger_void`, only through the two-step confirm and ledgerActions.ts). The writing route
// name is not written here: the hook hands the `Confirmed` value to `voidPromise`.
//
// Hooks and click handlers are rendered in no test (threat model F6-1c); the logic they call is in
// ledger.ts, confirm.ts and ledgerActions.ts, which are tested without a DOM.

import { useCallback, useReducer, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { ConfirmState } from "../confirm";
import { callRoute } from "../ipc/proxy";
import {
  INITIAL_FILTER,
  INITIAL_LEDGER_STATE,
  applyFilterChange,
  failureOf,
  promisesRequest,
  reduceLedger,
  type FilterChange,
  type LedgerState,
  type PromiseFilter,
} from "../ledger";
import { voidPromise, type VoidReason, type VoidTarget } from "../ledgerActions";
import { useConfirm } from "./useConfirm";

export interface LedgerHandle {
  state: LedgerState;
  confirm: ConfirmState<VoidTarget>;
  refreshCustomers: () => void;
  refreshPromises: () => void;
  changeFilter: (change: FilterChange) => void;
  chooseReason: (promiseId: string, reason: VoidReason | null) => void;
  /** Step one: show the confirm area. Sends nothing. */
  openVoid: (target: VoidTarget) => void;
  /** Step two: the only call that can send the void. */
  confirmVoid: () => void;
  cancelVoid: () => void;
}

export function useLedger(): LedgerHandle {
  const [state, dispatch] = useReducer(reduceLedger, INITIAL_LEDGER_STATE);
  // The filter the last request used, kept in step with the reducer's so a handler never reads a
  // stale one.
  const filterRef = useRef<PromiseFilter>(INITIAL_FILTER);
  const confirm = useConfirm<VoidTarget>();
  const confirmDispatch = confirm.dispatch;

  const refreshCustomers = useCallback(() => {
    dispatch({ type: "customers_requested" });
    void callRoute<"ledger_customers">(invoke, { route: "ledger_customers" }).then((result) =>
      dispatch({ type: "customers_result", result }),
    );
  }, []);

  const loadPromises = useCallback((filter: PromiseFilter) => {
    dispatch({ type: "promises_requested" });
    void callRoute<"ledger_promises">(invoke, promisesRequest(filter)).then((result) =>
      dispatch({ type: "promises_result", filter, result }),
    );
  }, []);

  const refreshPromises = useCallback(() => loadPromises(filterRef.current), [loadPromises]);

  const changeFilter = useCallback(
    (change: FilterChange) => {
      const next = applyFilterChange(filterRef.current, change);
      filterRef.current = next;
      dispatch({ type: "filter_changed", filter: next });
      loadPromises(next);
    },
    [loadPromises],
  );

  const chooseReason = useCallback((promiseId: string, reason: VoidReason | null) => {
    dispatch({ type: "reason_chosen", promiseId, reason });
  }, []);

  const openVoid = useCallback(
    (target: VoidTarget) => {
      confirmDispatch({ type: "open", target });
    },
    [confirmDispatch],
  );

  const cancelVoid = useCallback(() => {
    confirmDispatch({ type: "cancel" });
  }, [confirmDispatch]);

  const confirmVoid = useCallback(() => {
    const confirmed = confirmDispatch({ type: "confirm" });
    if (confirmed === null) {
      return;
    }
    dispatch({ type: "write_started", kind: "void" });
    void voidPromise(invoke, confirmed).then((result) => {
      dispatch({ type: "write_result", kind: "void", result });
      confirmDispatch({ type: "done" });
      if (failureOf(result) === null) {
        loadPromises(filterRef.current);
      }
    });
  }, [confirmDispatch, loadPromises]);

  return {
    state,
    confirm: confirm.state,
    refreshCustomers,
    refreshPromises,
    changeFilter,
    chooseReason,
    openVoid,
    confirmVoid,
    cancelVoid,
  };
}
