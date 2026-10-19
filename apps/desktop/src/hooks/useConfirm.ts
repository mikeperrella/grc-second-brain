// Thin hook: holds one two-step confirm (confirm.ts). It drives the same controller the test of the
// confirm path drives, so the state a screen shows and the `Confirmed` a write needs come from one
// place. `dispatch` returns the `Confirmed` the event produced, which is non-null only when an open
// area was confirmed.

import { useCallback, useState } from "react";
import {
  createConfirmController,
  type Confirmed,
  type ConfirmEvent,
  type ConfirmState,
} from "../confirm";

export interface ConfirmHandle<T> {
  state: ConfirmState<T>;
  dispatch: (event: ConfirmEvent<T>) => Confirmed<T> | null;
}

export function useConfirm<T>(): ConfirmHandle<T> {
  const [controller] = useState(() => createConfirmController<T>());
  const [state, setState] = useState<ConfirmState<T>>(() => controller.state());

  const dispatch = useCallback(
    (event: ConfirmEvent<T>) => {
      const confirmed = controller.dispatch(event);
      setState(controller.state());
      return confirmed;
    },
    [controller],
  );

  return { state, dispatch };
}
