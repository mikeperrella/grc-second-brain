// The Ledger screen as static markup (ADR-022, docs/DESIGN.md 7 and 11; threat model F36-1, F40, F44).
// No jsdom: every state is built with the reducer and rendered to a string. The write guarantee
// (nothing sent without the second step) is proved without a DOM in ledgerActions.test.ts; here the
// markup is checked: what shows in each state, what is absent, and the order of the controls.

import { renderToStaticMarkup } from "react-dom/server";
import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { ConfirmState } from "../confirm";
import {
  LEDGER_IMMUTABLE_NOTE,
  LEDGER_PROMISE_DEFINITION,
  PROMISES_PAGE_TOO_LARGE,
  VOID_STEP_STATEMENT,
} from "../fixedTexts";
import { INITIAL_LEDGER_STATE, reduceLedger, type LedgerEvent, type LedgerState } from "../ledger";
import type { VoidTarget } from "../ledgerActions";
import { CANARY, customer, promiseRow, promisesBody } from "../ledgerTestSupport";
import { proxyErrorMessage } from "../messages";
import { visibleControls } from "../plainText";
import { errorOf, escapeHtml, replyOf, textOf } from "../testSupport";
import { LedgerScreen } from "./LedgerScreen";

const NOOP = () => undefined;
const PROMISE_ID = "00000000-0000-4000-8000-000000000001";
const IDLE: ConfirmState<VoidTarget> = { phase: "idle" };

const WORDS = {
  definition: "A promise is an answer you told ALIBI you sent to a customer. ALIBI never sends anything.",
  immutable:
    "Immutable here means ALIBI has no way to change or delete a ledger row. It is not tamper-evident: another program running as you could edit the file.",
  voidStep: "Voiding cannot be undone. The promise stays in the ledger, marked void, and is left out of recall.",
  tooLarge: "This page of promises is too large to show. Choose a smaller page size.",
} as const;

function run(events: LedgerEvent[], from: LedgerState = INITIAL_LEDGER_STATE): LedgerState {
  return events.reduce(reduceLedger, from);
}

function render(state: LedgerState, confirm: ConfirmState<VoidTarget> = IDLE, enabled = true): string {
  return renderToStaticMarkup(
    <LedgerScreen
      state={state}
      confirm={confirm}
      enabled={enabled}
      onFilterChange={NOOP}
      onChooseReason={NOOP}
      onOpenVoid={NOOP}
      onConfirmVoid={NOOP}
      onCancelVoid={NOOP}
    />,
  );
}

function withPromises(promises = [promiseRow()], total = promises.length, customers = [customer()]): LedgerState {
  return run([
    { type: "customers_result", result: replyOf("ledger_customers", 200, customers) },
    {
      type: "promises_result",
      filter: INITIAL_LEDGER_STATE.filter,
      result: replyOf("ledger_promises", 200, promisesBody(promises, total)),
    },
  ]);
}

function notConfigured(): LedgerState {
  return run([
    { type: "customers_result", result: errorOf("ledger_customers", 503, "ledger_root_not_configured") },
    {
      type: "promises_result",
      filter: INITIAL_LEDGER_STATE.filter,
      result: errorOf("ledger_promises", 503, "ledger_root_not_configured"),
    },
  ]);
}

function openConfirm(over: Partial<VoidTarget> = {}): ConfirmState<VoidTarget> {
  return {
    phase: "open",
    target: {
      promiseId: PROMISE_ID,
      reason: "wrong_customer",
      customerName: "Acme Test Co",
      sourcePath: "policies/encryption-policy.md",
      ...over,
    },
  };
}

describe("the Ledger screen's fixed statements", () => {
  it("matches the owner's approved DESIGN.md 11.1 words", () => {
    expect(LEDGER_PROMISE_DEFINITION).toBe(WORDS.definition);
    expect(LEDGER_IMMUTABLE_NOTE).toBe(WORDS.immutable);
    expect(VOID_STEP_STATEMENT).toBe(WORDS.voidStep);
    expect(PROMISES_PAGE_TOO_LARGE).toBe(WORDS.tooLarge);
  });

  it("shows both ledger statements in every state, not configured included", () => {
    const states: LedgerState[] = [
      INITIAL_LEDGER_STATE,
      notConfigured(),
      withPromises(),
      withPromises([], 0, []),
      run([{ type: "promises_result", filter: INITIAL_LEDGER_STATE.filter, result: { kind: "proxy_error", error: "too_large" } }]),
    ];
    for (const state of states) {
      const text = textOf(render(state));
      expect(text).toContain(WORDS.definition);
      expect(text).toContain(WORDS.immutable);
    }
  });
});

describe("the ledger states", () => {
  it("loading names what is being read", () => {
    const text = textOf(render(run([{ type: "customers_requested" }, { type: "promises_requested" }])));
    expect(text).toContain("Reading the customers.");
    expect(text).toContain("Reading the promises.");
  });

  it("not configured is a refusal with its code, never 0 and never an empty list", () => {
    const html = render(notConfigured());
    const text = textOf(html);
    expect(text).toContain("No ledger folder is in use.");
    expect(html).toContain("ledger_root_not_configured");
    expect(text).not.toContain("The ledger has no customers.");
    expect(text).not.toContain("No promises match this filter.");
    expect(text).not.toMatch(/Promises: \d/);
    expect(html).not.toContain("<ul");
    expect(html.match(/role="alert"/g)).toHaveLength(2);
  });

  it("empty says exactly what is empty, and a listed empty page shows the sidecar's total", () => {
    const text = textOf(render(withPromises([], 0, [])));
    expect(text).toContain("The ledger has no customers.");
    expect(text).toContain("No promises match this filter.");
    expect(text).toContain("Promises: 0");
  });

  it("an unusable ledger, a store error, a busy slot and an invalid answer each show their own words", () => {
    const cases: [LedgerEvent, string][] = [
      [{ type: "promises_result", filter: INITIAL_LEDGER_STATE.filter, result: errorOf("ledger_promises", 503, "ledger_unusable") }, "ledger_unusable"],
      [{ type: "promises_result", filter: INITIAL_LEDGER_STATE.filter, result: errorOf("ledger_promises", 503, "ledger_store_error") }, "ledger_store_error"],
      [{ type: "promises_result", filter: INITIAL_LEDGER_STATE.filter, result: errorOf("ledger_promises", 409, "run_in_progress") }, "run_in_progress"],
      [{ type: "promises_result", filter: INITIAL_LEDGER_STATE.filter, result: { kind: "invalid_response" } }, "invalid_response"],
    ];
    const seen = new Set<string>();
    for (const [event, code] of cases) {
      const html = render(run([event]));
      expect(html).toContain(`<code class="font-mono">${code}</code>`);
      expect(textOf(html)).not.toMatch(/Promises: \d/);
      seen.add(textOf(html));
    }
    expect(seen.size).toBe(cases.length);
    expect(textOf(render(run([cases[2]?.[0] as LedgerEvent])))).toContain("another job is running");
  });

  it("every proxy error word has its words and is not the page-too-large state", () => {
    for (const error of ["not_ready", "unauthorized", "unreachable", "timeout", "bad_request", "bad_response", "internal"] as const) {
      const html = render(
        run([{ type: "promises_result", filter: INITIAL_LEDGER_STATE.filter, result: { kind: "proxy_error", error } }]),
      );
      expect(textOf(html)).toContain(proxyErrorMessage(error));
      expect(textOf(html)).not.toContain(WORDS.tooLarge);
    }
  });

  it("page too large says so plainly and offers only smaller page sizes", () => {
    const html = render(
      run([{ type: "promises_result", filter: INITIAL_LEDGER_STATE.filter, result: { kind: "proxy_error", error: "too_large" } }]),
    );
    const text = textOf(html);
    expect(text).toContain(WORDS.tooLarge);
    for (const size of [5, 10, 25]) {
      expect(text).toContain(`Use page size ${size}`);
    }
    expect(text).not.toContain("Use page size 50");
    expect(text).not.toContain("Use page size 100");
    expect(text).not.toMatch(/Promises: \d/);
  });

  it("shows a page of promises, the total and the dates exactly as sent", () => {
    const state = withPromises([promiseRow({ sent_on: "2026-10-10", created_on: "2026-10-09" })], 1234567);
    const text = textOf(render(state));
    expect(text).toContain("Promises: 1234567");
    expect(text).not.toContain("1,234,567");
    expect(text).toContain("Sent on: 2026-10-10");
    expect(text).toContain("Created on: 2026-10-09");
    expect(text).toContain("Lines 12 to 13");
  });

  it("a promise with a status the code does not know shows an error block, not the value", () => {
    const html = render(withPromises([promiseRow({ status: "SETTLED" })]));
    expect(html).toContain("Unknown status: ALIBI cannot show a label for this value.");
    expect(html).not.toContain("SETTLED");
  });

  it("marks a voided promise with text and a border, and gives it no void control", () => {
    const html = render(withPromises([promiseRow({ voided: true })]));
    expect(textOf(html)).toContain("Voided");
    expect(html).not.toContain("Void this promise");
    expect(html).not.toContain("Reason for voiding");
  });

  it("disables the controls while the sidecar is not healthy", () => {
    const html = render(withPromises(), IDLE, false);
    const buttons = html.match(/<button[^>]*>[^<]*<\/button>/g) ?? [];
    expect(buttons.length).toBeGreaterThan(0);
    for (const button of buttons) {
      // The attribute, not the `disabled:` class every button carries.
      expect(button, button).toContain('disabled=""');
    }
  });
});

describe("the void controls", () => {
  it("the reason picker is the four closed reasons and the void button waits for a choice", () => {
    const html = render(withPromises());
    for (const label of ["Wrong customer", "Imported in error", "Not actually sent", "Other"]) {
      expect(html).toContain(`>${label}</option>`);
    }
    expect(html).toContain('<label for="void-reason-' + PROMISE_ID + '"');
    expect(html.match(/<button[^>]*>Void this promise<\/button>/)?.[0]).toContain('disabled=""');
    const chosen = reduceLedger(withPromises(), { type: "reason_chosen", promiseId: PROMISE_ID, reason: "other" });
    expect(render(chosen).match(/<button[^>]*>Void this promise<\/button>/)?.[0]).not.toContain('disabled=""');
  });

  it("the void button alone shows no confirm step and none of its words", () => {
    const html = render(withPromises());
    expect(html).not.toContain("Confirm void");
    expect(textOf(html)).not.toContain(WORDS.voidStep);
  });

  it("step one opens the in-page confirm with the section 11 words and the target as plain text", () => {
    const html = render(withPromises(), openConfirm());
    const text = textOf(html);
    expect(text).toContain(WORDS.voidStep);
    expect(html).toContain("Confirm void");
    expect(text).toContain("Target:");
    expect(text).toContain("Customer: Acme Test Co");
    expect(text).toContain(PROMISE_ID);
    expect(text).toContain("policies/encryption-policy.md");
    expect(text).toContain("Reason for voiding: Wrong customer");
    expect(html).not.toContain("<a ");
  });

  it("the confirm area shows only under the promise it is for", () => {
    const other = openConfirm({ promiseId: "00000000-0000-4000-8000-000000000002" });
    expect(render(withPromises(), other)).not.toContain("Confirm void");
  });

  it("keeps a keyboard order: reason, void, confirm, cancel; all real buttons, no tabindex", () => {
    const state = reduceLedger(withPromises(), { type: "reason_chosen", promiseId: PROMISE_ID, reason: "wrong_customer" });
    const html = render(state, openConfirm());
    const order = ["<select", ">Void this promise</button>", ">Confirm void</button>", ">Cancel</button>"].map((part) =>
      html.indexOf(part, html.indexOf("data-promise=")),
    );
    expect(order.every((index) => index > 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
    for (const button of html.match(/<button[^>]*>/g) ?? []) {
      expect(button, button).toContain('type="button"');
    }
    expect(html).not.toContain("tabindex");
    expect(html).not.toContain("onclick");
    expect(html).not.toContain("confirm(");
  });

  it("promise_voided and promise_unknown are shown by their own words, with their codes", () => {
    const voided = run([
      { type: "write_result", kind: "void", result: errorOf("ledger_void", 422, "recall_refused", { code: "promise_voided" }) },
    ], withPromises());
    const unknown = run([
      { type: "write_result", kind: "void", result: errorOf("ledger_void", 422, "ledger_refused", { code: "promise_unknown" }) },
    ], withPromises());
    expect(textOf(render(voided))).toContain("That promise is already voided.");
    expect(render(voided)).toContain("recall_refused:promise_voided");
    expect(textOf(render(unknown))).toContain("That promise is not in the ledger.");
    expect(render(unknown)).toContain("ledger_refused:promise_unknown");
  });

  it("shows the sending and done lines for a void", () => {
    expect(textOf(render(run([{ type: "write_started", kind: "void" }], withPromises())))).toContain("Voiding the promise.");
    const done = run(
      [{ type: "write_result", kind: "void", result: replyOf("ledger_void", 200, { promise_id: PROMISE_ID, event_id: 4, kind: "voided" }) }],
      withPromises(),
    );
    expect(textOf(render(done))).toContain("The promise was voided.");
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

  it("a canary in a customer name, a question, an answer and a quote is plain text, never a link, never logged", () => {
    const state = withPromises(
      [promiseRow({ question_text: CANARY, answer_text: CANARY, quote: CANARY })],
      1,
      [customer({ display_name: CANARY })],
    );
    for (const confirm of [IDLE, openConfirm({ customerName: CANARY, sourcePath: CANARY })]) {
      const html = render(state, confirm);
      expect(html).toContain(escapeHtml(visibleControls(CANARY)));
      expect(html).not.toContain("<a ");
      expect(html).not.toContain('href="');
      expect(html).not.toContain(CANARY);
    }
    for (const [index, spy] of spies.entries()) {
      expect(spy, METHODS[index]).not.toHaveBeenCalled();
    }
  });

  it("control and direction characters in a name are made visible", () => {
    const name = "Acme\u202etest\u0000end";
    const html = render(withPromises([promiseRow()], 1, [customer({ display_name: name })]));
    expect(html).toContain("Acme[U+202E]test[U+0000]end");
    expect(html).not.toContain("\u202e");
  });

  it("renders no style attribute, link, truncation or locale formatting in any state", () => {
    const states: LedgerState[] = [INITIAL_LEDGER_STATE, notConfigured(), withPromises(), withPromises([], 0, [])];
    for (const state of states) {
      for (const confirm of [IDLE, openConfirm()]) {
        const html = render(state, confirm);
        expect(html).not.toMatch(/\sstyle=/);
        expect(html).not.toContain("<style");
        expect(html).not.toContain("<a ");
        expect(html).not.toMatch(/truncate|line-clamp/);
      }
    }
    for (const spy of spies) {
      expect(spy).not.toHaveBeenCalled();
    }
  });
});
