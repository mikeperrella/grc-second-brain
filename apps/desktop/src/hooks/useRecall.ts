// Thin hook: the Recall screen. One check call (`ledger_recall`, which records a change of state
// when it finds one) and one write (`ledger_acknowledge`, only through the two-step confirm and
// ledgerActions.ts). The writing route name is not written here.
//
// Hooks and click handlers are rendered in no test (threat model F6-1c); the logic is in recall.ts,
// confirm.ts and ledgerActions.ts, which are tested without a DOM.
//
// The state lives in one store (recall.ts createRecallStore). A run's customer is read from that
// state, so a customer choice the reducer ignores (a run is in progress) changes nothing.

import { useCallback, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { ConfirmState } from "../confirm";
import { callRoute } from "../ipc/proxy";
import { acknowledgePromise, type AcknowledgeTarget } from "../ledgerActions";
import {
  acknowledgeTarget,
  createRecallStore,
  type RecallEvent,
  type RecallItem,
  type RecallState,
} from "../recall";
import { useConfirm } from "./useConfirm";

export interface RecallHandle {
  state: RecallState;
  confirm: ConfirmState<AcknowledgeTarget>;
  chooseCustomer: (customerId: string | null) => void;
  run: () => void;
  /** Step one: show the confirm area. Sends nothing. A no-op for an item with no event id. */
  openAcknowledge: (item: RecallItem) => void;
  /** Step two: the only call that can send the acknowledgement. */
  confirmAcknowledge: () => void;
  cancelAcknowledge: () => void;
}

export function useRecall(): RecallHandle {
  const [store] = useState(createRecallStore);
  const [state, setState] = useState<RecallState>(() => store.state());
  const confirm = useConfirm<AcknowledgeTarget>();
  const confirmDispatch = confirm.dispatch;

  const dispatch = useCallback(
    (event: RecallEvent) => {
      setState(store.dispatch(event));
    },
    [store],
  );

  const chooseCustomer = useCallback(
    (customerId: string | null) => {
      dispatch({ type: "customer_chosen", customerId });
    },
    [dispatch],
  );

  const run = useCallback(() => {
    const request = store.request();
    dispatch({ type: "run_requested" });
    void callRoute<"ledger_recall">(invoke, request).then((result) => dispatch({ type: "run_result", result }));
  }, [store, dispatch]);

  const openAcknowledge = useCallback(
    (item: RecallItem) => {
      const target = acknowledgeTarget(item);
      if (target === null) {
        return;
      }
      confirmDispatch({ type: "open", target });
    },
    [confirmDispatch],
  );

  const cancelAcknowledge = useCallback(() => {
    confirmDispatch({ type: "cancel" });
  }, [confirmDispatch]);

  const confirmAcknowledge = useCallback(() => {
    const confirmed = confirmDispatch({ type: "confirm" });
    if (confirmed === null) {
      return;
    }
    dispatch({ type: "ack_started" });
    void acknowledgePromise(invoke, confirmed).then((result) => {
      dispatch({ type: "ack_result", result });
      confirmDispatch({ type: "done" });
    });
  }, [confirmDispatch, dispatch]);

  return {
    state,
    confirm: confirm.state,
    chooseCustomer,
    run,
    openAcknowledge,
    confirmAcknowledge,
    cancelAcknowledge,
  };
}
