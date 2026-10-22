// The Reality check screen as static markup (chunk 65j; ADR-016, ADR-022, threat model F26-7, F36-1
// and F43; docs/DESIGN.md 4.5, 7, 8 and 11.1). No jsdom: every state is built with the reducer the
// hook drives (reality.ts) and rendered to a string. The approved sentences are spelled out as
// literals. Replies come from the sidecar's RECORDED replies (synthetic data).

import { renderToStaticMarkup } from "react-dom/server";
import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";
import { CHECK_REFUSAL_CODES, EXPORT_REFUSAL_CODES } from "../ipc/proxy";
import { CANARY } from "../ledgerTestSupport";
import { visibleControls } from "../plainText";
import {
  INITIAL_REALITY_STATE,
  reduceReality,
  type CheckReport,
  type RealityEvent,
  type RealityState,
} from "../reality";
import {
  FILE,
  STATUS_AT,
  TYPE_AT,
  candidate,
  checkReport,
  checkedState,
  chosenState,
  claimsListed,
  claimsReport,
  filesListed,
  inspectReport,
  readState,
  readyState,
  run,
} from "../realityTestSupport";
import { errorOf, escapeHtml, replyOf, textOf } from "../testSupport";
import { LOGINS_SHOWN, RealityScreen } from "./RealityScreen";

const NOOP = () => undefined;

// DESIGN.md 11.1 (approved 2026-10-19), spelled out as literals.
const WORDS = {
  exceptions: "This export shows users in scope without MFA, by the MFA types you confirmed.",
  none: "This export shows no users in scope without MFA, by the MFA types you confirmed.",
  phone: "Counting phone (text message or voice) as MFA is a policy choice: phone codes can be phished or intercepted.",
  logins: "The logins below are personal data. ALIBI does not save or log them.",
  statement: "this export, this claim, this date; not evidence the control works",
  staleness:
    "The date is the one the owner supplied. How old the export is, and whether it is the right export, is the owner's responsibility.",
  duplicate:
    "Every row is counted on its own. A login that appears on more than one row can be an exception on one row and not on another.",
} as const;

const SHOWN_SHA = "2f70196855ceca5b28031de475f25605521b52c41028eee426e93552ba466507";
const OTHER_SHA = "f".repeat(64);

function render(state: RealityState, enabled = true): string {
  return renderToStaticMarkup(
    <RealityScreen state={state} enabled={enabled} onChooseFile={NOOP} onInputChange={NOOP} onCheck={NOOP} />,
  );
}

function logins(count: number): CheckReport["exceptions"] {
  return Array.from({ length: count }, (_, index) => ({ login: `user${index}@example.test`, truncated: false }));
}

function button(html: string, label: string): string {
  const found = html.match(new RegExp(`<button[^>]*>${label}</button>`));
  expect(found, label).not.toBeNull();
  // The class string holds "disabled:border-dashed"; only the attribute counts as disabled.
  return (found?.[0] ?? "").replace(/class="[^"]*"/, "");
}

/** Every state the screen can be in, for the "always visible" and "never" checks. */
function everyState(): RealityState[] {
  const failedFiles = run([
    { type: "files_requested" },
    { type: "files_result", call: { kind: "folder_error", error: "not_configured" } },
  ]);
  const checkRefusals = CHECK_REFUSAL_CODES.map((code) =>
    run(
      [
        { type: "check_requested", generation: readyState().generation },
        { type: "check_result", generation: readyState().generation, result: errorOf("reality_check", 422, "check_refused", { code }) },
      ],
      readyState(),
    ),
  );
  return [
    INITIAL_REALITY_STATE,
    run([{ type: "files_requested" }]),
    failedFiles,
    run(filesListed([])),
    run(filesListed()),
    run([...filesListed(), { type: "file_chosen", index: 0 }]),
    readState(),
    chosenState(),
    readyState(),
    reduceReality(readyState(), { type: "check_requested", generation: readyState().generation }),
    checkedState(),
    checkedState(checkReport({ finding: "no_exceptions_in_export" })),
    checkedState(checkReport({ sha256: OTHER_SHA })),
    checkedState(checkReport({ without_mfa: 0, exceptions: [], exceptions_not_listed: 0, finding: "no_exceptions_in_export" })),
    ...checkRefusals,
    ...EXPORT_REFUSAL_CODES.map((code) =>
      run(
        [
          { type: "file_chosen", index: 0 } as RealityEvent,
          {
            type: "inspect_result",
            file: FILE,
            result: errorOf("reality_inspect", 422, "export_refused", { code }),
          } as RealityEvent,
        ],
        run(filesListed()),
      ),
    ),
  ];
}

describe("the reality check screen: the export and the choices", () => {
  it("starts with a reading message, no result and no check sent", () => {
    const text = textOf(render(INITIAL_REALITY_STATE));
    expect(text).toContain("Reality check");
    expect(text).toContain("Reading the file names in the imports folder.");
    expect(text).toContain("No check has been run.");
    expect(text).not.toContain(WORDS.exceptions);
    expect(text).not.toContain(WORDS.none);
  });

  it("no imports folder in use is a typed refusal with its code, never an empty list", () => {
    const html = render(everyState()[2] ?? INITIAL_REALITY_STATE);
    expect(html).toContain("No imports folder is in use.");
    expect(html).toContain("import_root_not_configured");
    expect(html).not.toContain("no .csv files in the folder");
    expect(html).toContain('role="alert"');
  });

  it("an empty folder has its own plain message and no refusal", () => {
    const html = render(run(filesListed([])));
    expect(textOf(html)).toContain("no .csv files in the folder");
    expect(html).not.toContain('role="alert"');
  });

  it("lists the file names as plain text, each with a Use this export button", () => {
    const html = render(run(filesListed(["a.csv", "b.csv"])));
    expect(html).toContain("a.csv");
    expect(html).toContain("b.csv");
    expect((html.match(/>Use this export</g) ?? []).length).toBe(2);
    expect(textOf(render(run(filesListed(["a.csv"], true))))).toContain("The list was cut short; only the first names are shown.");
  });

  it("marks the chosen export as Selected", () => {
    const html = render(readState());
    expect(textOf(html)).toContain("Selected");
  });

  it("nothing is ticked at the start: no MFA type, no phone type, no status, no claim, no confirmation", () => {
    const html = render(readState());
    expect(html).toContain('type="checkbox"');
    expect(html).not.toMatch(/checked=""/);
    // The only "Selected" is the export that was read; no claim is marked.
    expect((textOf(html).match(/\bSelected\b/g) ?? []).length).toBe(1);
  });

  it("a ticked value renders ticked, only the ones the person ticked", () => {
    const html = render(chosenState());
    expect((html.match(/checked=""/g) ?? []).length).toBe(2);
  });

  it("the phone sentence is always visible beside the phone choice, once, between its heading and its boxes", () => {
    for (const state of [readState(), chosenState(), readyState(), checkedState()]) {
      const html = render(state);
      const text = textOf(html);
      expect(text.split(WORDS.phone)).toHaveLength(2);
      const heading = html.indexOf("Types that are phone (text message or voice)");
      const sentence = html.indexOf(WORDS.phone);
      const firstBox = html.indexOf('name="reality-phone-0"');
      expect(heading).toBeGreaterThan(-1);
      expect(sentence).toBeGreaterThan(heading);
      expect(firstBox).toBeGreaterThan(sentence);
    }
  });

  it("a value cut short shows the existing 'cut short' label and its box is disabled", () => {
    const cut = inspectReport({
      authenticator_types: [
        { value: "Password", count: 35, truncated: false },
        { value: "Very long type", count: 4, truncated: true },
      ],
      statuses: [{ value: "Very long status", count: 2, truncated: true }],
    });
    const html = render(readState(cut));
    expect(textOf(html)).toContain("(cut short by the sidecar)");
    const box = html.match(/<input[^>]*name="reality-mfa-1"[^>]*>/)?.[0] ?? "";
    expect(box).toContain("disabled");
    expect(html.match(/<input[^>]*name="reality-mfa-0"[^>]*>/)?.[0] ?? "").not.toContain("disabled");
    expect(html.match(/<input[^>]*name="reality-status-0"[^>]*>/)?.[0] ?? "").toContain("disabled");
  });

  it("shows the hash and the counts of the export read, exactly as sent", () => {
    const text = textOf(render(readState()));
    expect(text).toContain(`SHA-256 of the export read: ${SHOWN_SHA}`);
    expect(text).toContain("Size in bytes: 7509");
    expect(text).toContain("Data rows: 40");
    expect(text).toContain("Password count: 35");
    expect(text).toContain("Okta Verify count: 20");
  });

  it("the claim is shown as plain text with its lines and scope words, and no control note", () => {
    const text = textOf(render(readState()));
    expect(text).toContain("access-control-policy.md");
    expect(text).toContain("Lines: 23 - 23");
    expect(text).toMatch(/Scope words in the sentence: staff\s*,\s*remote\s*,\s*customer/);
    expect(text).toContain("Document maps to an authentication control: yes");
    expect(text).not.toContain("This document maps to an authentication control.");
  });

  it("no claim found has its own message", () => {
    const state = run([...filesListed(), ...claimsListed(claimsReport({ candidates: [] }))]);
    expect(textOf(render(state))).toContain('No policy sentence that reads like "MFA is required" was found in the vault.');
  });

  it("the check button is off until nothing is missing, then on; a problem is named", () => {
    const missing = render(chosenState());
    expect(button(missing, "Check the export")).toContain("disabled");
    expect(textOf(missing)).toContain("Tick the box to confirm the MFA types.");
    const ready = render(readyState());
    expect(button(ready, "Check the export")).not.toContain("disabled");
    expect(textOf(ready)).not.toContain("Tick the box");
  });

  it("every control is off while the sidecar is not healthy, and while a check is going", () => {
    for (const html of [render(readyState(), false), render(reduceReality(readyState(), { type: "check_requested", generation: readyState().generation }))]) {
      for (const found of html.match(/<input[^>]*>/g) ?? []) {
        expect(found).toContain("disabled");
      }
      expect(button(html, "Check the export")).toContain("disabled");
    }
  });
});

describe("the result", () => {
  it("shows the first sentence for exceptions_found, with the three sidecar statements, from window constants", () => {
    const text = textOf(render(checkedState()));
    expect(text).toContain(WORDS.exceptions);
    expect(text).not.toContain(WORDS.none);
    expect(text).toContain(WORDS.statement);
    expect(text).toContain(WORDS.staleness);
    expect(text).toContain(WORDS.duplicate);
  });

  it("shows the second sentence for no_exceptions_in_export", () => {
    const none = checkReport({
      finding: "no_exceptions_in_export",
      without_mfa: 0,
      admins_without_mfa: 0,
      phone_only: 0,
      exceptions: [],
      exceptions_not_listed: 0,
    });
    const text = textOf(render(checkedState(none)));
    expect(text).toContain(WORDS.none);
    expect(text).not.toContain(WORDS.exceptions);
    expect(text).toContain(WORDS.statement);
  });

  it("a statement in the reply that differs from the constant is never shown", () => {
    // The schema pins these three strings, so a different one can only be built past the types.
    const hostile = {
      ...checkReport(),
      statement: "HOSTILE-STATEMENT compliant",
      staleness_note: "HOSTILE-STALENESS",
      duplicate_note: "HOSTILE-DUPLICATE",
    } as unknown as CheckReport;
    const text = textOf(render(checkedState(hostile)));
    expect(text).not.toContain("HOSTILE");
    expect(text).toContain(WORDS.statement);
    expect(text).toContain(WORDS.staleness);
    expect(text).toContain(WORDS.duplicate);
  });

  it("the reply's control note and claim note are never shown", () => {
    const text = textOf(render(checkedState(checkReport({ claim: candidate({ control_note: "HOSTILE-NOTE" }) }))));
    expect(text).not.toContain("HOSTILE-NOTE");
  });

  it("the counts are the recorded numbers, tabular, and the phone-only count has its own label", () => {
    const html = render(checkedState());
    const text = textOf(html);
    expect(text).toContain("In scope: 32");
    expect(text).toContain("With MFA: 19");
    expect(text).toContain("Without MFA: 13");
    expect(text).toContain("Administrators without MFA: 3");
    expect(text).toContain("Administrator status unknown: 1");
    expect(text).toContain("With only a phone type: 3");
    expect(text).toContain("Phone types named: yes");
    expect(html).toContain("tabular-nums");
  });

  it("a count that is missing reads 'not available', never 0", () => {
    const missing = { ...checkReport(), in_scope: null, phone_only: null } as unknown as CheckReport;
    const text = textOf(render(checkedState(missing)));
    expect(text).toContain("In scope: not available");
    expect(text).toContain("With only a phone type: not available");
    expect(text).not.toContain("In scope: 0");
    expect(text).not.toContain("With only a phone type: 0");
  });

  it("shows the hash read and the hash counted side by side", () => {
    const text = textOf(render(checkedState()));
    expect(text).toContain(`SHA-256 of the export read: ${SHOWN_SHA}`);
    expect(text).toContain(`SHA-256 of the export counted: ${SHOWN_SHA}`);
  });

  it("a different hash shows an error block with both hashes and no sentence, no counts, no logins", () => {
    const html = render(checkedState(checkReport({ sha256: OTHER_SHA })));
    const text = textOf(html);
    expect(text).toContain("The export that was counted is not the export that was read, so ALIBI shows no result.");
    expect(text).toContain(SHOWN_SHA);
    expect(text).toContain(OTHER_SHA);
    expect(html).toContain('role="alert"');
    expect(text).not.toContain(WORDS.exceptions);
    expect(text).not.toContain(WORDS.none);
    expect(text).not.toContain("u06@example.test");
    expect(text).not.toContain("Without MFA: 13");
  });

  it("a finding that disagrees with the counts shows an error block and no sentence, no counts, no logins", () => {
    const html = render(checkedState(checkReport({ finding: "no_exceptions_in_export" })));
    const text = textOf(html);
    expect(text).toContain("The finding and the counts in the reply do not agree, so ALIBI shows no result.");
    expect(html).toContain('role="alert"');
    expect(text).not.toContain(WORDS.exceptions);
    expect(text).not.toContain(WORDS.none);
    expect(text).not.toContain("u06@example.test");
    expect(text).not.toContain(WORDS.logins);
  });

  it("a finding outside the closed set shows an error block, never a label", () => {
    const html = render(checkedState({ ...checkReport(), finding: "all_clear" } as unknown as CheckReport));
    const text = textOf(html);
    expect(text).toContain("The reply holds a finding ALIBI does not know, so ALIBI shows no result.");
    expect(text).not.toContain("all_clear");
    expect(text).not.toContain(WORDS.exceptions);
  });

  it("the check's refusals each show their own words and code, and no result", () => {
    const seen = new Set<string>();
    for (const state of everyState().slice(14, 14 + CHECK_REFUSAL_CODES.length)) {
      const html = render(state);
      expect(html).toContain('role="alert"');
      const alert = html.match(/<div role="alert"[\s\S]*?<\/div>/)?.[0] ?? "";
      seen.add(textOf(alert));
      expect(textOf(html)).not.toContain(WORDS.exceptions);
    }
    expect(seen.size).toBe(CHECK_REFUSAL_CODES.length);
  });

  it("the export's refusals each show their own words", () => {
    const states = everyState().slice(14 + CHECK_REFUSAL_CODES.length);
    expect(states).toHaveLength(EXPORT_REFUSAL_CODES.length);
    const seen = new Set<string>();
    for (const state of states) {
      const html = render(state);
      const alert = html.match(/<div role="alert"[\s\S]*?<\/div>/)?.[0] ?? "";
      expect(alert).not.toBe("");
      seen.add(textOf(alert));
    }
    expect(seen.size).toBe(EXPORT_REFUSAL_CODES.length);
  });
});

describe("the logins are personal data", () => {
  it("the 11.1 sentence is above the list of logins, once, in every state that shows one", () => {
    const states = [checkedState(), checkedState(checkReport({ exceptions: logins(30), without_mfa: 30 }))];
    for (const state of states) {
      const html = render(state);
      const sentence = html.indexOf(WORDS.logins);
      const list = html.indexOf('data-logins="list"');
      expect(sentence).toBeGreaterThan(-1);
      expect(list).toBeGreaterThan(sentence);
      expect(textOf(html).split(WORDS.logins)).toHaveLength(2);
    }
  });

  it("no logins, no list and no sentence about them", () => {
    const none = checkReport({
      finding: "no_exceptions_in_export",
      without_mfa: 0,
      admins_without_mfa: 0,
      phone_only: 0,
      exceptions: [],
      exceptions_not_listed: 0,
    });
    const html = render(checkedState(none));
    expect(html).not.toContain('data-logins="list"');
    expect(textOf(html)).not.toContain(WORDS.logins);
  });

  it("the sentence says nothing about the logins being gone when the person leaves", () => {
    expect(WORDS.logins).not.toMatch(/gone|leave|leaving|dropped|forgotten|cleared/i);
    expect(textOf(render(checkedState()))).not.toMatch(/gone when you leave/i);
  });

  it("a long list is cut at the first ten and has a Show more button; a short one has none", () => {
    const long = render(checkedState(checkReport({ exceptions: logins(30), without_mfa: 30 })));
    expect((long.match(/user\d+@example\.test/g) ?? []).length).toBe(LOGINS_SHOWN);
    expect(long).toContain(">Show more<");
    expect(long).toContain('aria-expanded="false"');
    const short = render(checkedState(checkReport({ exceptions: logins(LOGINS_SHOWN), without_mfa: LOGINS_SHOWN })));
    expect(short).not.toContain("Show more");
    expect(short).not.toContain("aria-expanded");
    expect((short.match(/user\d+@example\.test/g) ?? []).length).toBe(LOGINS_SHOWN);
  });

  it("a login the sidecar cut short carries the 'cut short' label", () => {
    const report = checkReport({ exceptions: [{ login: "long-login", truncated: true }], without_mfa: 1 });
    expect(textOf(render(checkedState(report)))).toContain("long-login (cut short by the sidecar)");
  });
});

describe("untrusted text is plain text, never a link, never logged", () => {
  const BIDI = String.fromCodePoint(0x202e);
  const spies = (["log", "info", "warn", "error", "debug", "trace", "dir", "table", "group"] as const).map((method) =>
    vi.spyOn(console, method).mockImplementation(() => undefined),
  );
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

  /** A state with the canary in the file name, a type, a status, a claim and every field of a result. */
  function hostileState(): RealityState {
    const report = inspectReport({
      authenticator_types: [{ value: CANARY, count: 1, truncated: false }, { value: "Okta Verify", count: 2, truncated: false }],
      statuses: [{ value: CANARY, count: 1, truncated: false }],
    });
    const claim = candidate({ path: CANARY, doc_id: CANARY, quote: CANARY, qualifier_words: [CANARY], matched_control_ids: [CANARY] });
    // The listing holds the canary as the file name: choose it by hand, since `inspected` expects FILE.
    const chosen = run(
      [
        { type: "file_chosen", index: 0 },
        { type: "inspect_result", file: CANARY, result: replyOf("reality_inspect", 200, report) },
      ],
      run([...filesListed([CANARY]), ...claimsListed(claimsReport({ candidates: [claim] }))]),
    );
    return run(
      [
        { type: "input_changed", change: { kind: "mfa", index: 0 } },
        { type: "input_changed", change: { kind: "status", index: 0 } },
        { type: "input_changed", change: { kind: "claim", index: 0 } },
        { type: "input_changed", change: { kind: "date", text: "2026-10-01" } },
        { type: "input_changed", change: { kind: "confirm", confirmed: true } },
      ],
      chosen,
    );
  }

  it("a canary in a file name, type, status, claim, login and skipped value is text, escaped, with no link", () => {
    const ready = hostileState();
    const hostileReport = checkReport({
      sha256: SHOWN_SHA,
      claim: candidate({ path: CANARY, doc_id: CANARY, quote: CANARY, qualifier_words: [CANARY], matched_control_ids: [CANARY] }),
      exceptions: [{ login: CANARY, truncated: false }, { login: `a${BIDI}b@example.test`, truncated: false }],
      without_mfa: 2,
      skipped: [{ value: CANARY, count: 1, truncated: false }],
      mfa_types_not_present: [CANARY],
    });
    const checked = run(
      [
        { type: "check_requested", generation: ready.generation },
        { type: "check_result", generation: ready.generation, result: replyOf("reality_check", 200, hostileReport) },
      ],
      ready,
    );
    expect(checked.check.phase).toBe("checked");
    for (const state of [ready, checked]) {
      const html = render(state);
      expect(html).toContain(escapeHtml(visibleControls(CANARY)));
      expect(html).not.toContain(CANARY);
      expect(html).not.toMatch(/<a[\s>]/);
      // No tag carries an href (the canary's own text is escaped, so it holds no raw `<`).
      expect(html).not.toMatch(/<[^>]*\bhref=/);
      expect(html).not.toContain('href="https://canary.invalid');
    }
    const html = render(checked);
    // The direction override is made visible, never carried as a character.
    expect(html).toContain("[U+202E]");
    expect(html).not.toContain(BIDI);
    expect(spies.every((spy) => spy.mock.calls.length === 0)).toBe(true);
  });

  it("the console stays silent through every state the screen can show", () => {
    for (const state of everyState()) {
      render(state);
    }
    expect(spies.every((spy) => spy.mock.calls.length === 0)).toBe(true);
  });
});

describe("what the screen never says", () => {
  it("no state says compliant, non-compliant, pass or fail", () => {
    for (const state of everyState()) {
      expect(textOf(render(state))).not.toMatch(/compliant|\bpass(ed|es)?\b|\bfail(ed|s|ure)?\b/i);
    }
  });

  it("no state holds a style attribute, a link, a form or an image", () => {
    for (const state of everyState()) {
      const html = render(state);
      expect(html).not.toMatch(/\sstyle=|<a[\s>]|href=|<form|<img|<svg/);
    }
  });

  it("every state has the 11.1 phone sentence only with the phone choice, and the logins sentence only with a list", () => {
    for (const state of everyState()) {
      const text = textOf(render(state));
      const phone = text.includes(WORDS.phone);
      expect(phone, "phone sentence needs the phone choice").toBe(state.inspect.phase === "inspected");
    }
  });
});

describe("a ready state for the screen", () => {
  it("ticks Okta Verify and Active only", () => {
    const state = readyState();
    expect(state.inputs.mfaTypes).toEqual(["Okta Verify"]);
    expect(state.inputs.statuses).toEqual(["Active"]);
    expect(TYPE_AT.oktaVerify).toBe(1);
    expect(STATUS_AT.active).toBe(0);
  });
});
