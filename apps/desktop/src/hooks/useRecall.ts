// Thin hook: the Recall screen. One check call (`ledger_recall`, which records a change of state
// when it finds one) and one write (`ledger_acknowledge`, only through the two-step confirm and
// ledgerActions.ts). The writing route name is not written here.
//
// Hooks and click handlers are rendered in no test (threat model F6-1c); the logic is in recall.ts,
// confirm.ts and ledgerActions.ts, which are tested without a DOM.

import { useCallback, useReducer, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { ConfirmState } from "../confirm";
import { callRoute } from "../ipc/proxy";
import { acknowledgePromise, type AcknowledgeTarget } from "../ledgerActions";
import {
  INITIAL_RECALL_STATE,
  acknowledgeTarget,
  recallRequest,
  reduceRecall,
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
  const [state, dispatch] = useReducer(reduceRecall, INITIAL_RECALL_STATE);
  const customerRef = useRef<string | null>(null);
  const confirm = useConfirm<AcknowledgeTarget>();
  const confirmDispatch = confirm.dispatch;

  const chooseCustomer = useCallback((customerId: string | null) => {
    customerRef.current = customerId;
    dispatch({ type: "customer_chosen", customerId });
  }, []);

  const run = useCallback(() => {
    dispatch({ type: "run_requested" });
    void callRoute<"ledger_recall">(invoke, recallRequest(customerRef.current)).then((result) =>
      dispatch({ type: "run_result", result }),
    );
  }, []);

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
  }, [confirmDispatch]);

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
