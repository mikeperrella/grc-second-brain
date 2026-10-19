// The two ledger writes (ADR-022 decision 1, docs/DESIGN.md 11.2). A recording fake `invoke` shows
// that no call is made without the second step and exactly one with it. The tests drive the SAME
// controller the hooks drive (createConfirmController), and mirror the hook's handler: a write is
// made only when an event produced a `Confirmed`.

import { describe, expect, it } from "vitest";
import { createConfirmController, type Confirmed } from "./confirm";
import type { Invoke } from "./ipc/proxy";
import {
  acknowledgePromise,
  voidPromise,
  type AcknowledgeTarget,
  type VoidTarget,
} from "./ledgerActions";
import { acknowledgeTarget } from "./recall";
import { recallItem } from "./ledgerTestSupport";

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
