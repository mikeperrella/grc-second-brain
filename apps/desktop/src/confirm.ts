// The two-step confirm for an irreversible ledger write (docs/DESIGN.md 11.2, ADR-022 decision 1).
// Pure: no React, no invoke.
//
// Step one (`open`) only shows the in-page confirm area. Step two (`confirm`) is a separate event,
// and it is the only way to get a `Confirmed` value. `ledgerActions.ts` calls a write route only
// when it is handed a `Confirmed`, so no click handler can reach a write without passing through
// this reducer. `cancel` closes the area and produces nothing.
//
// The hook (hooks/useConfirm.ts) drives the controller below, and so does the test that proves
// "first step sends nothing, second step sends once, cancel sends nothing". Hooks and click
// handlers are rendered in no test (threat model F6-1c), so the guarantee lives here.

declare const confirmedBrand: unique symbol;

/** Proof that the confirm reducer saw the second step for this target. Made only in this file. */
export interface Confirmed<T> {
  readonly [confirmedBrand]: true;
  readonly target: T;
}

export type ConfirmState<T> =
  | { phase: "idle" }
  /** Step one was clicked: the area is showing, nothing has been sent. */
  | { phase: "open"; target: T }
  /** Step two was clicked: the write is being made. */
  | { phase: "confirmed"; confirmed: Confirmed<T> };

export type ConfirmEvent<T> =
  | { type: "open"; target: T }
  | { type: "confirm" }
  | { type: "cancel" }
  /** The write has finished (with any outcome): the area is gone. */
  | { type: "done" };

export function initialConfirmState<T>(): ConfirmState<T> {
  return { phase: "idle" };
}

export function reduceConfirm<T>(state: ConfirmState<T>, event: ConfirmEvent<T>): ConfirmState<T> {
  switch (event.type) {
    case "open":
      // Opening never confirms. While a write is going on, a new open is ignored.
      return state.phase === "confirmed" ? state : { phase: "open", target: event.target };
    case "confirm":
      // Only an open area can be confirmed, and only once: a second `confirm` finds `confirmed`.
      return state.phase === "open"
        ? { phase: "confirmed", confirmed: { target: state.target } as Confirmed<T> }
        : state;
    case "cancel":
      return state.phase === "open" ? { phase: "idle" } : state;
    case "done":
      return { phase: "idle" };
  }
}

/**
 * What a screen change does to the confirm area that belongs to the screen `owner` (65f tidy-up a):
 * when the screen now shown is another one, the area is cancelled, so nothing is sent and the area
 * is gone when the person comes back. On the owner's own screen it does nothing. A write already
 * confirmed is not stopped (`cancel` leaves `confirmed` alone; `done` ends it).
 * screens.tsx sends this for every screen that has a confirm area.
 */
export function confirmEventOnScreen<S extends string>(owner: S, shown: S): { type: "cancel" } | null {
  return shown === owner ? null : { type: "cancel" };
}

/** The `Confirmed` made by this step, or `null`: set only when an open area became confirmed. */
export function confirmationOf<T>(before: ConfirmState<T>, after: ConfirmState<T>): Confirmed<T> | null {
  return before.phase === "open" && after.phase === "confirmed" ? after.confirmed : null;
}

export interface ConfirmController<T> {
  state(): ConfirmState<T>;
  /** Apply one event. Returns the `Confirmed` this event produced, else `null`. */
  dispatch(event: ConfirmEvent<T>): Confirmed<T> | null;
}

/**
 * The controller the hook holds. It keeps the latest state itself, so two quick clicks on Confirm
 * cannot both read a stale `open` state and send twice.
 */
export function createConfirmController<T>(): ConfirmController<T> {
  let current: ConfirmState<T> = initialConfirmState<T>();
  return {
    state: () => current,
    dispatch: (event) => {
      const before = current;
      current = reduceConfirm(before, event);
      return confirmationOf(before, current);
    },
  };
}
