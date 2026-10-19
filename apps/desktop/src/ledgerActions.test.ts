// The two ledger writes (ADR-022 decision 1, docs/DESIGN.md 11.2). A recording fake `invoke` shows
// that no call is made without the second step and exactly one with it. The tests drive the SAME
// controller the hooks drive (createConfirmController), and mirror the hook's handler: a write is
// made only when an event produced a `Confirmed`.

import { describe, expect, it } from "vitest";
import { confirmEventOnScreen, createConfirmController, type Confirmed } from "./confirm";
import type { Invoke } from "./ipc/proxy";
import {
  acknowledgePromise,
  importPromises,
  voidPromise,
  type AcknowledgeTarget,
  type VoidTarget,
} from "./ledgerActions";
import {
  INITIAL_IMPORT_STATE,
  currentFields,
  fieldsKey,
  importTarget,
  reduceImport,
  type ImportEvent,
  type ImportState,
  type ImportTarget,
} from "./ledgerImport";
import { acknowledgeTarget } from "./recall";
import { draftBody, importReport, recallItem } from "./ledgerTestSupport";
import { replyOf } from "./testSupport";

const PROMISE = "00000000-0000-4000-8000-000000000001";
const VOID_TARGET: VoidTarget = {
  promiseId: PROMISE,
  reason: "wrong_customer",
  customerName: "Acme Test Co",
  sourcePath: "policies/encryption-policy.md",
};
const ACK_TARGET: AcknowledgeTarget = {
  promiseId: PROMISE,
  seenRecallEventId: 7,
  sourcePath: "policies/encryption-policy.md",
};

interface Call {
  command: string;
  args: Record<string, unknown>;
}

function recorder(body: unknown): { invoke: Invoke; calls: Call[] } {
  const calls: Call[] = [];
  const invoke: Invoke = (command, args) => {
    calls.push({ command, args });
    return Promise.resolve({ status: 200, body });
  };
  return { invoke, calls };
}

type Step = "open" | "confirm" | "cancel";

/** The hook's handler, without React: send only when an event produced a Confirmed. */
async function drive<T>(
  target: T,
  steps: readonly Step[],
  send: (confirmed: Confirmed<T>) => Promise<unknown>,
): Promise<void> {
  const controller = createConfirmController<T>();
  for (const step of steps) {
    const confirmed =
      step === "open"
        ? controller.dispatch({ type: "open", target })
        : controller.dispatch({ type: step });
    if (confirmed !== null) {
      await send(confirmed);
    }
  }
}

describe("a void is sent only by the second step", () => {
  const ok = { promise_id: PROMISE, event_id: 4, kind: "voided" };

  const cases: readonly (readonly [string, readonly Step[], number])[] = [
    ["step one alone sends nothing", ["open"], 0],
    ["step one then step two sends exactly once", ["open", "confirm"], 1],
    ["a second click on step two does not send again", ["open", "confirm", "confirm"], 1],
    ["cancel sends nothing", ["open", "cancel"], 0],
    ["step two after cancel sends nothing", ["open", "cancel", "confirm"], 0],
    ["step two with no step one sends nothing", ["confirm"], 0],
  ];

  for (const [name, steps, expected] of cases) {
    it(name, async () => {
      const { invoke, calls } = recorder(ok);
      await drive(VOID_TARGET, steps, (confirmed) => voidPromise(invoke, confirmed));
      expect(calls).toHaveLength(expected);
    });
  }

  it("the body carries only the route, the promise id and the closed reason", async () => {
    const { invoke, calls } = recorder(ok);
    await drive(VOID_TARGET, ["open", "confirm"], (confirmed) => voidPromise(invoke, confirmed));
    expect(calls).toEqual([
      {
        command: "sidecar_call",
        args: { request: { route: "ledger_void", promise_id: PROMISE, reason: "wrong_customer" } },
      },
    ]);
    // The customer name and the path are shown to the person; they are never sent.
    expect(JSON.stringify(calls)).not.toContain("Acme");
    expect(JSON.stringify(calls)).not.toContain("encryption-policy");
  });

  it("a Confirmed cannot be made by hand", () => {
    const { invoke } = recorder(ok);
    // @ts-expect-error a plain object is not a Confirmed: only the confirm reducer makes one
    void voidPromise(invoke, { target: VOID_TARGET });
  });
});

describe("an acknowledgement is sent only by the second step", () => {
  const ok = { promise_id: PROMISE, event_id: 6, kind: "acknowledged" };

  it("sends nothing after step one or cancel, and exactly once after step two", async () => {
    for (const [steps, expected] of [
      [["open"], 0],
      [["open", "cancel"], 0],
      [["open", "confirm"], 1],
      [["open", "confirm", "confirm"], 1],
    ] as const) {
      const { invoke, calls } = recorder(ok);
      await drive(ACK_TARGET, steps, (confirmed) => acknowledgePromise(invoke, confirmed));
      expect(calls, steps.join(",")).toHaveLength(expected);
    }
  });

  it("sends the item's own recall_event_id as seen_recall_event_id", async () => {
    const { invoke, calls } = recorder(ok);
    await drive(ACK_TARGET, ["open", "confirm"], (confirmed) => acknowledgePromise(invoke, confirmed));
    expect(calls).toEqual([
      {
        command: "sidecar_call",
        args: {
          request: { route: "ledger_acknowledge", promise_id: PROMISE, seen_recall_event_id: 7 },
        },
      },
    ]);
  });
});

describe("an item with no recall_event_id can never be acknowledged (no guessed id)", () => {
  it("has no acknowledge target, so step one opens nothing and nothing is sent", async () => {
    const item = recallItem({ recall_event_id: null, event_recorded: false });
    expect(acknowledgeTarget(item)).toBeNull();
    const { invoke, calls } = recorder({ promise_id: PROMISE, event_id: 6, kind: "acknowledged" });
    // The hook opens the confirm only when acknowledgeTarget is not null: mirror it.
    const target = acknowledgeTarget(item);
    if (target !== null) {
      await drive(target, ["open", "confirm"], (confirmed) => acknowledgePromise(invoke, confirmed));
    }
    expect(calls).toHaveLength(0);
  });

  it("copies the item's own id, not a neighbour's or a count", () => {
    const item = recallItem({ recall_event_id: 41, acknowledged_event_id: 3 });
    expect(acknowledgeTarget(item)).toEqual({
      promiseId: item.promise_id,
      seenRecallEventId: 41,
      sourcePath: item.source_path,
    });
  });
});

describe("the real import is sent only by the second step (chunk 65g)", () => {
  const DRAFT = draftBody();

  /** The state the screen is in after a successful preview (the reducer the hook drives). */
  function previewedState(): ImportState {
    const events: ImportEvent[] = [
      { type: "file_selected", file: DRAFT.file_name },
      { type: "draft_requested" },
      { type: "draft_result", file: DRAFT.file_name, result: replyOf("draft_rows", 200, DRAFT) },
      { type: "input_changed", change: { kind: "new_customer_name", name: "Acme Test Co" } },
      { type: "input_changed", change: { kind: "confirm_new", confirmNew: true } },
      { type: "input_changed", change: { kind: "sent_on", sentOn: "2026-10-10" } },
    ];
    const state = events.reduce(reduceImport, INITIAL_IMPORT_STATE);
    const fields = currentFields(state);
    if (fields === null) {
      throw new Error("inputs not valid");
    }
    const key = fieldsKey(fields);
    const preview: ImportEvent[] = [
      { type: "preview_requested", key },
      { type: "preview_result", key, fields, result: replyOf("ledger_import", 200, importReport("preview")) },
    ];
    return preview.reduce(reduceImport, state);
  }

  /** The hook's openImport: step one opens only when importTarget gives a target. */
  function targetOf(state: ImportState): ImportTarget {
    const target = importTarget(state);
    if (target === null) {
      throw new Error("no import target");
    }
    return target;
  }

  const ok = importReport("created");
  const cases: readonly (readonly [string, readonly Step[], number])[] = [
    ["the import button (step one) alone sends nothing", ["open"], 0],
    ["step one then step two sends exactly once", ["open", "confirm"], 1],
    ["a second click on step two does not send again", ["open", "confirm", "confirm"], 1],
    ["cancel sends nothing", ["open", "cancel"], 0],
    ["step two after cancel sends nothing", ["open", "cancel", "confirm"], 0],
    ["step two with no step one sends nothing", ["confirm"], 0],
  ];

  for (const [name, steps, expected] of cases) {
    it(name, async () => {
      const { invoke, calls } = recorder(ok);
      await drive(targetOf(previewedState()), steps, (confirmed) => importPromises(invoke, confirmed));
      expect(calls).toHaveLength(expected);
    });
  }

  it("the second step sends exactly the previewed fields with preview false", async () => {
    const { invoke, calls } = recorder(ok);
    await drive(targetOf(previewedState()), ["open", "confirm"], (confirmed) => importPromises(invoke, confirmed));
    expect(calls).toEqual([
      {
        command: "sidecar_call",
        args: {
          request: {
            route: "ledger_import",
            run_id: DRAFT.run_id,
            file: DRAFT.file_name,
            new_customer_name: "Acme Test Co",
            confirm_new: true,
            sent_on: "2026-10-10",
            preview: false,
          },
        },
      },
    ]);
  });

  it("there is no step one without a matching preview", () => {
    const changed = reduceImport(previewedState(), {
      type: "input_changed",
      change: { kind: "sent_on", sentOn: "2026-10-11" },
    });
    expect(importTarget(changed)).toBeNull();
  });

  it("a Confirmed import cannot be made by hand", () => {
    const { invoke } = recorder(ok);
    // @ts-expect-error a plain object is not a Confirmed: only the confirm reducer makes one
    void importPromises(invoke, { target: targetOf(previewedState()) });
  });
});

describe("leaving a screen cancels its open confirm (65f tidy-up a)", () => {
  type Screen = "folders" | "index" | "questionnaire" | "review" | "ledger" | "recall" | "import";
  const OTHERS: readonly Screen[] = ["folders", "index", "questionnaire", "review", "ledger", "recall", "import"];

  /**
   * Step one on the owner's screen, then the person goes to `away` and comes back, then clicks the
   * confirm button: what screens.tsx and the hook do, without React.
   */
  async function leaveAndReturn<T>(
    owner: Screen,
    away: Screen,
    target: T,
    send: (confirmed: Confirmed<T>) => Promise<unknown>,
  ): Promise<{ phaseAway: string; phaseBack: string }> {
    const controller = createConfirmController<T>();
    controller.dispatch({ type: "open", target });
    const leave = confirmEventOnScreen<Screen>(owner, away);
    if (leave !== null) {
      controller.dispatch(leave);
    }
    const phaseAway = controller.state().phase;
    const back = confirmEventOnScreen<Screen>(owner, owner);
    if (back !== null) {
      controller.dispatch(back);
    }
    const phaseBack = controller.state().phase;
    const confirmed = controller.dispatch({ type: "confirm" });
    if (confirmed !== null) {
      await send(confirmed);
    }
    return { phaseAway, phaseBack };
  }

  it("a void confirm left open is gone on return and the confirm click sends nothing", async () => {
    for (const away of OTHERS.filter((screen) => screen !== "ledger")) {
      const { invoke, calls } = recorder({ promise_id: PROMISE, event_id: 4, kind: "voided" });
      const phases = await leaveAndReturn("ledger", away, VOID_TARGET, (confirmed) => voidPromise(invoke, confirmed));
      expect(phases, away).toEqual({ phaseAway: "idle", phaseBack: "idle" });
      expect(calls, away).toHaveLength(0);
    }
  });

  it("an acknowledge confirm left open is gone on return and the confirm click sends nothing", async () => {
    for (const away of OTHERS.filter((screen) => screen !== "recall")) {
      const { invoke, calls } = recorder({ promise_id: PROMISE, event_id: 6, kind: "acknowledged" });
      const phases = await leaveAndReturn("recall", away, ACK_TARGET, (confirmed) => acknowledgePromise(invoke, confirmed));
      expect(phases, away).toEqual({ phaseAway: "idle", phaseBack: "idle" });
      expect(calls, away).toHaveLength(0);
    }
  });

  it("an import confirm left open is gone on return and the confirm click sends nothing", async () => {
    const DRAFT = draftBody();
    const events: ImportEvent[] = [
      { type: "file_selected", file: DRAFT.file_name },
      { type: "draft_requested" },
      { type: "draft_result", file: DRAFT.file_name, result: replyOf("draft_rows", 200, DRAFT) },
      { type: "input_changed", change: { kind: "existing_customer", customerId: "00000001000000000000000000000000" } },
      { type: "input_changed", change: { kind: "sent_on", sentOn: "2026-10-10" } },
    ];
    const state = events.reduce(reduceImport, INITIAL_IMPORT_STATE);
    const fields = currentFields(state);
    if (fields === null) {
      throw new Error("inputs not valid");
    }
    const key = fieldsKey(fields);
    const previewed = reduceImport(reduceImport(state, { type: "preview_requested", key }), {
      type: "preview_result",
      key,
      fields,
      result: replyOf("ledger_import", 200, importReport("preview")),
    });
    const target = importTarget(previewed);
    if (target === null) {
      throw new Error("no import target");
    }
    for (const away of OTHERS.filter((screen) => screen !== "import")) {
      const { invoke, calls } = recorder(importReport("created"));
      const phases = await leaveAndReturn("import", away, target, (confirmed) => importPromises(invoke, confirmed));
      expect(phases, away).toEqual({ phaseAway: "idle", phaseBack: "idle" });
      expect(calls, away).toHaveLength(0);
    }
  });

  it("staying on the owner's screen keeps the area open, and the second step still sends once", async () => {
    const { invoke, calls } = recorder({ promise_id: PROMISE, event_id: 4, kind: "voided" });
    const controller = createConfirmController<VoidTarget>();
    controller.dispatch({ type: "open", target: VOID_TARGET });
    expect(confirmEventOnScreen<Screen>("ledger", "ledger")).toBeNull();
    expect(controller.state().phase).toBe("open");
    const confirmed = controller.dispatch({ type: "confirm" });
    if (confirmed !== null) {
      await voidPromise(invoke, confirmed);
    }
    expect(calls).toHaveLength(1);
  });

  it("a write already confirmed is not stopped by leaving (cancel leaves confirmed alone)", () => {
    const controller = createConfirmController<VoidTarget>();
    controller.dispatch({ type: "open", target: VOID_TARGET });
    controller.dispatch({ type: "confirm" });
    const leave = confirmEventOnScreen<Screen>("ledger", "folders");
    expect(leave).toEqual({ type: "cancel" });
    if (leave !== null) {
      controller.dispatch(leave);
    }
    expect(controller.state().phase).toBe("confirmed");
  });
});
