// The Recall screen as static markup (ADR-017 decisions 17 and 20, threat model F31-4b, F40, F44;
// docs/DESIGN.md 11). No jsdom: every state is built with the reducer and rendered to a string. The
// fixed statement and scope are asserted above the list and inside every item, from the window's
// constants, and a different string in the data is never shown.

import { renderToStaticMarkup } from "react-dom/server";
import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { ConfirmState } from "../confirm";
import {
  ACKNOWLEDGE_STATEMENT,
  RECALL_BEFORE_RUN,
  RECALL_SCOPE,
  RECALL_STATEMENT,
} from "../fixedTexts";
import { RECALL_FLAGS, RECALL_STATES } from "../ipc/proxy";
import type { CustomersView } from "../ledger";
import type { AcknowledgeTarget } from "../ledgerActions";
import { CANARY, customer, recallItem, recallReport } from "../ledgerTestSupport";
import { visibleControls } from "../plainText";
import {
  INITIAL_RECALL_STATE,
  reduceRecall,
  type RecallEvent,
  type RecallItem,
  type RecallState,
} from "../recall";
import { errorOf, escapeHtml, replyOf, textOf } from "../testSupport";
import { RecallScreen } from "./RecallScreen";

const NOOP = () => undefined;
const IDLE: ConfirmState<AcknowledgeTarget> = { phase: "idle" };
const PROMISE_1 = "00000000-0000-4000-8000-000000000001";
const PROMISE_2 = "00000000-0000-4000-8000-000000000002";
const NO_CUSTOMERS: CustomersView = { phase: "idle" };

// The owner's approved words (DESIGN.md 11.1) and the sidecar's own sentences, spelled out here so a
// change to fixedTexts.ts is a visible test edit.
const WORDS = {
  statement: "intact means the cited lines are unchanged since drafting; it does not mean the promise is still true.",
  scope: "Recall watches the cited lines only; a change elsewhere in the document is not a state change.",
  beforeRun: "Checking records a change of state in the ledger when one is found. It never edits a promise.",
  acknowledge: "Acknowledging records that you saw this state on this date. It does not change the promise.",
  stale: "This promise's state changed after you acknowledged it.",
} as const;

function run(events: RecallEvent[], from: RecallState = INITIAL_RECALL_STATE): RecallState {
  return events.reduce(reduceRecall, from);
}

function render(
  state: RecallState,
  over: { confirm?: ConfirmState<AcknowledgeTarget>; customers?: CustomersView; enabled?: boolean } = {},
): string {
  return renderToStaticMarkup(
    <RecallScreen
      state={state}
      confirm={over.confirm ?? IDLE}
      customers={over.customers ?? NO_CUSTOMERS}
      enabled={over.enabled ?? true}
      onChooseCustomer={NOOP}
      onRun={NOOP}
      onOpenAcknowledge={NOOP}
      onConfirmAcknowledge={NOOP}
      onCancelAcknowledge={NOOP}
    />,
  );
}

function done(items: RecallItem[]): RecallState {
  return run([
    { type: "run_requested" },
    { type: "run_result", result: replyOf("ledger_recall", 200, recallReport(items)) },
  ]);
}

/** The markup of each `<li data-promise=...>` item, in order. */
function itemsOf(html: string): string[] {
  return html.split("<li ").filter((part) => part.startsWith('class="alibi-block grid min-w-0 gap-2 border border-border p-3" data-promise='));
}

describe("the Recall screen's fixed statements", () => {
  it("match the approved words and the sidecar's recorded sentences", () => {
    expect(RECALL_STATEMENT).toBe(WORDS.statement);
    expect(RECALL_SCOPE).toBe(WORDS.scope);
    expect(RECALL_BEFORE_RUN).toBe(WORDS.beforeRun);
    expect(ACKNOWLEDGE_STATEMENT).toBe(WORDS.acknowledge);
  });

  it("shows the 'checking records a change of state' sentence before the run action, in every state", () => {
    const states: RecallState[] = [
      INITIAL_RECALL_STATE,
      run([{ type: "run_requested" }]),
      done([recallItem()]),
      run([{ type: "run_requested" }, { type: "run_result", result: errorOf("ledger_recall", 503, "vault_root_not_configured") }]),
    ];
    for (const state of states) {
      const html = render(state);
      expect(textOf(html)).toContain(WORDS.beforeRun);
      expect(html.indexOf(WORDS.beforeRun)).toBeGreaterThan(0);
      expect(html.indexOf(WORDS.beforeRun)).toBeLessThan(html.indexOf("Check promises now"));
    }
  });
});

describe("the recall statement and scope (F31-4b)", () => {
  it("appear above the list and inside each item, from the constants", () => {
    const html = render(done([recallItem({ promise_id: PROMISE_1 }), recallItem({ promise_id: PROMISE_2 })]));
    const first = html.indexOf(WORDS.statement);
    expect(first).toBeGreaterThan(0);
    expect(first).toBeLessThan(html.indexOf("data-promise="));
    expect(html.split(WORDS.statement)).toHaveLength(4);
    expect(html.split(WORDS.scope)).toHaveLength(4);
    const items = itemsOf(html);
    expect(items).toHaveLength(2);
    for (const item of items) {
      expect(item).toContain(WORDS.statement);
      expect(item).toContain(WORDS.scope);
    }
  });

  it("sit at the same weight as the state word: body text size, never small", () => {
    const html = render(done([recallItem()]));
    const item = itemsOf(html)[0] ?? "";
    const blocks = item.match(/<p class="alibi-block[^>]*>intact means[^<]*<\/p>/g) ?? [];
    expect(blocks).toHaveLength(1);
    expect(blocks[0]).not.toMatch(/text-xs|text-sm/);
    expect(item).toContain("text-base font-semibold");
  });

  it("show the window's constants even when the data carries other sentences", () => {
    const other = "intact means the promise is still true.";
    const report = recallReport([recallItem({ statement: other as never, scope: "all fine" as never })], {
      statement: other as never,
      scope: "all fine" as never,
    });
    const state: RecallState = { ...INITIAL_RECALL_STATE, run: { phase: "done", report } };
    const html = render(state);
    expect(html).not.toContain(other);
    expect(html).not.toContain("all fine");
    expect(html.split(WORDS.statement)).toHaveLength(3);
    expect(html.split(WORDS.scope)).toHaveLength(3);
  });

  it("show above the list even when no promise was checked", () => {
    const html = render(done([]));
    expect(textOf(html)).toContain(WORDS.statement);
    expect(textOf(html)).toContain("No promises were checked.");
  });
});

describe("state and flag words", () => {
  it("each state is shape plus word plus colour class, never colour alone", () => {
    const shapes = new Set<string>();
    for (const state of RECALL_STATES) {
      const html = render(done([recallItem({ state })]));
      const chip = html.match(new RegExp(`<span class="[^"]*" data-state="${state}">.*?</span></span>`))?.[0] ?? "";
      expect(chip, state).toContain("<svg");
      expect(chip, state).toMatch(/<span>[A-Z][a-z]+<\/span>/);
      expect(chip, state).toMatch(/text-(text|status-partial|status-needs-review)/);
      shapes.add(chip.replace(/<span class="[^"]*"/, "").replace(/<span>[^<]*<\/span>/, ""));
    }
    expect(shapes.size).toBe(RECALL_STATES.length);
  });

  it("an unknown state shows the error block and is never echoed or labelled", () => {
    const html = render(done([recallItem({ state: "all_clear" as never })]));
    expect(html).toContain("Unknown state: ALIBI cannot show a label for this value.");
    expect(html).not.toContain("all_clear");
    expect(html).not.toContain("data-state=");
    expect(textOf(html)).not.toContain("Intact");
  });

  it("an unknown previous state or acknowledged state also shows the error block", () => {
    const html = render(
      done([
        recallItem({
          state: "changed",
          previous_state: "fine" as never,
          acknowledged_event_id: 3,
          acknowledged_state: "ok" as never,
        }),
      ]),
    );
    expect(html.match(/Unknown state: ALIBI cannot show a label for this value\./g)).toHaveLength(2);
    expect(html).not.toContain(">fine<");
    expect(html).not.toContain(">ok<");
  });

  it("an unknown flag shows the error block, and each known flag its own word", () => {
    const bad = render(done([recallItem({ flags: ["healthy" as never] })]));
    expect(bad).toContain("Unknown flag: ALIBI cannot show a label for this value.");
    expect(bad).not.toContain("healthy");
    const good = textOf(render(done([recallItem({ flags: [...RECALL_FLAGS] })])));
    expect(good).toContain("Document changed elsewhere");
    expect(good).toContain("Review overdue");
    expect(good).toContain("Front matter invalid");
  });

  it("shows source path, line range, detail, moved places and the places not listed", () => {
    const html = render(
      done([
        recallItem({
          state: "moved",
          detail: null,
          locations: [{ start_line: 20, end_line: 21 }],
          locations_not_listed: 3,
          start_line: 8,
          end_line: 8,
        }),
        recallItem({ promise_id: PROMISE_2, state: "changed", detail: "quote_not_found", previous_state: "intact" }),
      ]),
    );
    const text = textOf(html);
    expect(text).toContain("policies/encryption-policy.md");
    expect(text).toContain("Line 8");
    expect(text).toContain("Now found at: Lines 20 to 21");
    expect(text).toContain("Places not listed: 3");
    expect(html).toContain('<code class="font-mono">quote_not_found</code>');
    expect(text).toContain("Previous state: Intact");
  });

  it("shows whether an acknowledgement exists, what state it was for and whether it is current", () => {
    const none = textOf(render(done([recallItem({ acknowledged_event_id: null })])));
    expect(none).toContain("Acknowledgement: Not acknowledged");
    const current = textOf(
      render(done([recallItem({ acknowledged_event_id: 4, acknowledged_state: "changed", acknowledgement_current: true })])),
    );
    expect(current).toContain("Acknowledged as: Changed");
    expect(current).toContain("The acknowledgement is current");
    const old = textOf(
      render(done([recallItem({ acknowledged_event_id: 4, acknowledged_state: "intact", acknowledgement_current: false })])),
    );
    expect(old).toContain("The acknowledgement is out of date");
    const before = textOf(
      render(done([recallItem({ acknowledged_event_id: 4, acknowledged_state: null, acknowledgement_current: false })])),
    );
    expect(before).toContain("Acknowledged before ALIBI recorded the state seen");
  });
});

describe("the refusals", () => {
  const refusals: [string, ReturnType<typeof errorOf>, string, string][] = [
    ["no vault folder", errorOf("ledger_recall", 503, "vault_root_not_configured"), "vault_root_not_configured", "No vault folder is in use."],
    ["no ledger folder", errorOf("ledger_recall", 503, "ledger_root_not_configured"), "ledger_root_not_configured", "No ledger folder is in use."],
    ["unusable ledger", errorOf("ledger_recall", 503, "ledger_unusable"), "ledger_unusable", "ALIBI cannot use it."],
    ["busy", errorOf("ledger_recall", 409, "run_in_progress"), "run_in_progress", "another job is running"],
  ];

  for (const [name, result, code, words] of refusals) {
    it(`${name} is a refusal, not an empty result`, () => {
      const html = render(run([{ type: "run_requested" }, { type: "run_result", result: result as never }]));
      expect(html).toContain(`<code class="font-mono">${code}</code>`);
      expect(textOf(html)).toContain(words);
      expect(textOf(html)).not.toContain("No promises were checked.");
      expect(textOf(html)).not.toContain("Checked:");
    });
  }

  it("too many promises shows its own words and integer counts only", () => {
    const html = render(
      run([
        { type: "run_requested" },
        {
          type: "run_result",
          result: errorOf("ledger_recall", 422, "recall_refused", { code: "too_many_promises", counts: { promises: 2, cap: 1 } }),
        },
      ]),
    );
    const text = textOf(html);
    expect(text).toContain("There are too many promises to check at once.");
    expect(html).toContain("recall_refused:too_many_promises");
    expect(html).toContain('<code class="font-mono">promises</code>: 2');
    expect(html).toContain('<code class="font-mono">cap</code>: 1');
  });

  it("an invalid answer and a proxy error show their words", () => {
    expect(render(run([{ type: "run_requested" }, { type: "run_result", result: { kind: "invalid_response" } }]))).toContain(
      "invalid_response",
    );
    expect(
      textOf(render(run([{ type: "run_requested" }, { type: "run_result", result: { kind: "proxy_error", error: "timeout" } }]))),
    ).toContain("The sidecar did not answer in time.");
  });

  it("the run action is off while a check runs, and while the sidecar is not healthy", () => {
    const button = (html: string) => html.match(/<button[^>]*>Check promises now<\/button>/)?.[0] ?? "";
    expect(button(render(run([{ type: "run_requested" }])))).toContain('disabled=""');
    expect(button(render(INITIAL_RECALL_STATE, { enabled: false }))).toContain('disabled=""');
    expect(button(render(INITIAL_RECALL_STATE))).not.toContain('disabled=""');
    expect(textOf(render(run([{ type: "run_requested" }])))).toContain("Checking the promises.");
  });

  it("shows the customer choice only once the customers are listed", () => {
    expect(render(INITIAL_RECALL_STATE)).not.toContain("<select");
    const html = render(INITIAL_RECALL_STATE, { customers: { phase: "listed", customers: [customer()] } });
    expect(html).toContain('<label for="recall-customer"');
    expect(html).toContain(">All customers</option>");
    expect(textOf(html)).toContain("Acme Test Co");
  });
});

describe("acknowledge", () => {
  const target: AcknowledgeTarget = {
    promiseId: PROMISE_1,
    seenRecallEventId: 3,
    sourcePath: "policies/encryption-policy.md",
  };
  const openAck: ConfirmState<AcknowledgeTarget> = { phase: "open", target };

  it("is offered only for an item that has a recall_event_id", () => {
    const withId = render(done([recallItem({ recall_event_id: 3 })]));
    expect(withId).toContain(">Acknowledge this state</button>");
    const without = render(done([recallItem({ recall_event_id: null, event_recorded: false })]));
    expect(without).not.toContain("Acknowledge this state");
    const mixed = render(
      done([recallItem({ promise_id: PROMISE_1, recall_event_id: 3 }), recallItem({ promise_id: PROMISE_2, recall_event_id: null })]),
    );
    expect(mixed.split(">Acknowledge this state</button>")).toHaveLength(2);
  });

  it("shows no confirm step and none of its words until step one", () => {
    const html = render(done([recallItem({ recall_event_id: 3 })]));
    expect(html).not.toContain("Confirm acknowledgement");
    expect(textOf(html)).not.toContain(WORDS.acknowledge);
  });

  it("step one shows the approved words and the target as plain text, with confirm before cancel", () => {
    const html = render(done([recallItem({ promise_id: PROMISE_1, recall_event_id: 3 })]), { confirm: openAck });
    const text = textOf(html);
    expect(text).toContain(WORDS.acknowledge);
    expect(text).toContain("Target:");
    expect(text).toContain(PROMISE_1);
    expect(text).toContain("policies/encryption-policy.md");
    const trigger = html.indexOf(">Acknowledge this state</button>");
    const confirm = html.indexOf(">Confirm acknowledgement</button>");
    const cancel = html.indexOf(">Cancel</button>");
    expect(trigger).toBeGreaterThan(0);
    expect(confirm).toBeGreaterThan(trigger);
    expect(cancel).toBeGreaterThan(confirm);
    for (const button of html.match(/<button[^>]*>/g) ?? []) {
      expect(button, button).toContain('type="button"');
    }
    expect(html).not.toContain("tabindex");
  });

  it("the confirm step shows only under the item it is for", () => {
    const html = render(
      done([recallItem({ promise_id: PROMISE_1, recall_event_id: 3 }), recallItem({ promise_id: PROMISE_2, recall_event_id: 5 })]),
      { confirm: openAck },
    );
    expect(html.split(">Confirm acknowledgement</button>")).toHaveLength(2);
    expect(itemsOf(html)[1]).not.toContain("Confirm acknowledgement");
  });

  it("a stale refusal says so in the approved words and to run recall again", () => {
    const state = run(
      [{ type: "ack_result", result: errorOf("ledger_acknowledge", 422, "recall_refused", { code: "acknowledge_stale" }) }],
      done([recallItem({ recall_event_id: 3 })]),
    );
    const html = render(state);
    expect(textOf(html)).toContain(WORDS.stale);
    expect(textOf(html)).toContain("Run the check again, then acknowledge the new state.");
    expect(html).toContain("recall_refused:acknowledge_stale");
  });

  it("promise_voided and promise_unknown have their own words", () => {
    const base = done([recallItem({ recall_event_id: 3 })]);
    const voided = run(
      [{ type: "ack_result", result: errorOf("ledger_acknowledge", 422, "recall_refused", { code: "promise_voided" }) }],
      base,
    );
    const unknown = run(
      [{ type: "ack_result", result: errorOf("ledger_acknowledge", 422, "ledger_refused", { code: "promise_unknown" }) }],
      base,
    );
    expect(textOf(render(voided))).toContain("That promise is already voided.");
    expect(textOf(render(unknown))).toContain("That promise is not in the ledger.");
  });

  it("shows the sending and done lines", () => {
    const base = done([recallItem({ recall_event_id: 3 })]);
    expect(textOf(render(run([{ type: "ack_started" }], base)))).toContain("Recording the acknowledgement.");
    const ok = run(
      [{ type: "ack_result", result: replyOf("ledger_acknowledge", 200, { promise_id: PROMISE_1, event_id: 6, kind: "acknowledged" }) }],
      base,
    );
    expect(textOf(render(ok))).toContain("The acknowledgement was recorded.");
  });
});

describe("personal data and hostile text (F36-1, F40)", () => {
  const METHODS = ["log", "info", "warn", "error", "debug", "trace", "dir", "table", "group"] as const;
  const spies = METHODS.map((method) => vi.spyOn(console, method).mockImplementation(() => undefined));
  beforeEach(() => {
    for (const spy of spies) {
      spy.mockClear();
    }
  });
  afterAll(() => {
    for (const spy of spies) {
      spy.mockRestore();
    }
  });

  it("a canary in a source path and a customer name is plain text, never a link, never logged", () => {
    const state = done([recallItem({ source_path: CANARY, recall_event_id: 3 })]);
    const customers: CustomersView = {
      phase: "listed",
      customers: [customer({ display_name: CANARY })],
    };
    const confirm: ConfirmState<AcknowledgeTarget> = {
      phase: "open",
      target: { promiseId: PROMISE_1, seenRecallEventId: 3, sourcePath: CANARY },
    };
    for (const over of [{}, { customers }, { customers, confirm }]) {
      const html = render(state, over);
      expect(html).toContain(escapeHtml(visibleControls(CANARY)));
      expect(html).not.toContain("<a ");
      expect(html).not.toContain('href="');
      expect(html).not.toContain(CANARY);
    }
    for (const [index, spy] of spies.entries()) {
      expect(spy, METHODS[index]).not.toHaveBeenCalled();
    }
  });

  it("renders no style attribute, link or truncation in any state", () => {
    const states: RecallState[] = [
      INITIAL_RECALL_STATE,
      run([{ type: "run_requested" }]),
      done([]),
      done([recallItem({ recall_event_id: 3, flags: [...RECALL_FLAGS], state: "moved" })]),
    ];
    for (const state of states) {
      const html = render(state);
      expect(html).not.toMatch(/\sstyle=/);
      expect(html).not.toContain("<style");
      expect(html).not.toContain("<a ");
      expect(html).not.toMatch(/truncate|line-clamp/);
    }
    for (const spy of spies) {
      expect(spy).not.toHaveBeenCalled();
    }
  });
});
