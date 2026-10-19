// The two-step confirm reducer and controller (docs/DESIGN.md 11.2, ADR-022 decision 1). Pure, no DOM.
// The hook (hooks/useConfirm.ts) drives the same controller, so what is proved here is the path the
// screens use.

import { describe, expect, it } from "vitest";
import {
  confirmationOf,
  createConfirmController,
  initialConfirmState,
  reduceConfirm,
  type ConfirmState,
} from "./confirm";

const TARGET = { id: "p1" };

describe("the confirm reducer", () => {
  it("opening shows the area and never confirms", () => {
    const after = reduceConfirm(initialConfirmState<typeof TARGET>(), { type: "open", target: TARGET });
    expect(after).toEqual({ phase: "open", target: TARGET });
    expect(confirmationOf(initialConfirmState<typeof TARGET>(), after)).toBeNull();
  });

  it("confirm without an open area does nothing", () => {
    const idle = initialConfirmState<typeof TARGET>();
    expect(reduceConfirm(idle, { type: "confirm" })).toEqual({ phase: "idle" });
  });

  it("confirm after open gives one confirmed state that carries the opened target", () => {
    const open = reduceConfirm(initialConfirmState<typeof TARGET>(), { type: "open", target: TARGET });
    const after = reduceConfirm(open, { type: "confirm" });
    expect(after.phase).toBe("confirmed");
    expect(confirmationOf(open, after)?.target).toEqual(TARGET);
  });

  it("a second confirm is not a new confirmation", () => {
    const open: ConfirmState<typeof TARGET> = { phase: "open", target: TARGET };
    const confirmed = reduceConfirm(open, { type: "confirm" });
    const again = reduceConfirm(confirmed, { type: "confirm" });
    expect(confirmationOf(confirmed, again)).toBeNull();
  });

  it("cancel closes the area and a later confirm does nothing", () => {
    const open: ConfirmState<typeof TARGET> = { phase: "open", target: TARGET };
    const cancelled = reduceConfirm(open, { type: "cancel" });
    expect(cancelled).toEqual({ phase: "idle" });
    expect(confirmationOf(cancelled, reduceConfirm(cancelled, { type: "confirm" }))).toBeNull();
  });

  it("opening while a write is going on is ignored, and done always closes", () => {
    const open: ConfirmState<typeof TARGET> = { phase: "open", target: TARGET };
    const confirmed = reduceConfirm(open, { type: "confirm" });
    expect(reduceConfirm(confirmed, { type: "open", target: { id: "p2" } })).toBe(confirmed);
    expect(reduceConfirm(confirmed, { type: "cancel" })).toBe(confirmed);
    expect(reduceConfirm(confirmed, { type: "done" })).toEqual({ phase: "idle" });
  });

  it("opening another target while open replaces the target", () => {
    const open: ConfirmState<typeof TARGET> = { phase: "open", target: TARGET };
    expect(reduceConfirm(open, { type: "open", target: { id: "p2" } })).toEqual({
      phase: "open",
      target: { id: "p2" },
    });
  });
});

describe("the confirm controller", () => {
  it("gives a Confirmed only for the second step, and only once", () => {
    const controller = createConfirmController<typeof TARGET>();
    expect(controller.dispatch({ type: "open", target: TARGET })).toBeNull();
    expect(controller.dispatch({ type: "confirm" })?.target).toEqual(TARGET);
    expect(controller.dispatch({ type: "confirm" })).toBeNull();
    expect(controller.state().phase).toBe("confirmed");
    expect(controller.dispatch({ type: "done" })).toBeNull();
    expect(controller.state().phase).toBe("idle");
  });

  it("gives nothing after cancel", () => {
    const controller = createConfirmController<typeof TARGET>();
    controller.dispatch({ type: "open", target: TARGET });
    expect(controller.dispatch({ type: "cancel" })).toBeNull();
    expect(controller.dispatch({ type: "confirm" })).toBeNull();
    expect(controller.state().phase).toBe("idle");
  });
});
