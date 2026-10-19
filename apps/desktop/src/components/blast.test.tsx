// The Blast radius screen as static markup (chunk 65h; ADR-018, ADR-022 decisions 2, 6 and 8, threat
// model F31-4b, F32-2, F32-4, F36-1; docs/DESIGN.md 11.1 and 11.3). No jsdom: every state is built
// with the reducer and rendered to a string. The approved sentences are spelled out as literals.

import { renderToStaticMarkup } from "react-dom/server";
import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";
import {
  INITIAL_BLAST_STATE,
  reduceBlast,
  type BlastEvent,
  type BlastItem,
  type BlastReport,
  type BlastState,
} from "../blast";
import { blastItem, blastReport, citedPaths, recordedBlast, recordedCited } from "../blastTestSupport";
import {
  BLAST_COVERAGE,
  BLAST_NEW_FLAG_LINE,
  BLAST_NEW_FLAG_WORD,
  BLAST_NO_MATCH_LINE,
  BLAST_PASTE_NOTE,
  BLAST_PREVIEW_LINE,
  BLAST_SCREEN_COVERAGE,
  RECALL_SCOPE,
  RECALL_STATEMENT,
} from "../fixedTexts";
import { RECALL_FLAGS, RECALL_STATES } from "../ipc/proxy";
import type { CustomersView } from "../ledger";
import { CANARY, customer } from "../ledgerTestSupport";
import { visibleControls } from "../plainText";
import { errorOf, escapeHtml, replyOf, textOf } from "../testSupport";
import { BlastScreen } from "./BlastScreen";

const NOOP = () => undefined;
const NO_CUSTOMERS: CustomersView = { phase: "idle" };
const PROMISE_1 = "00000000-0000-4000-8000-000000000001";
const PROMISE_2 = "00000000-0000-4000-8000-000000000002";

// DESIGN.md 11.1 and 11.3 (approved 2026-10-19), and the sidecar's own recorded sentences.
const WORDS = {
  coverage: "Blast radius checks promises only. It does not check controls or the documents that link to this file.",
  newFlag: "New: this flag is not on the file as it is now.",
  paste: "The text you paste stays in this window for this preview only. ALIBI does not save it.",
  preview: "this is a preview; nothing was saved or recorded",
  noMatch: "no live promise cites this path as written",
  statement: "intact means the cited lines are unchanged since drafting; it does not mean the promise is still true.",
  scope: "Recall watches the cited lines only; a change elsewhere in the document is not a state change.",
  sidecarCoverage: "promises only; controls and linking documents are not checked",
} as const;

function run(events: BlastEvent[], from: BlastState = INITIAL_BLAST_STATE): BlastState {
  return events.reduce(reduceBlast, from);
}

function render(state: BlastState, over: { customers?: CustomersView; enabled?: boolean } = {}): string {
  return renderToStaticMarkup(
    <BlastScreen
      state={state}
      customers={over.customers ?? NO_CUSTOMERS}
      enabled={over.enabled ?? true}
      onInputChange={NOOP}
      onPreview={NOOP}
    />,
  );
}

function listed(paths = citedPaths("two_paths")): BlastState {
  return run([{ type: "paths_requested" }, { type: "paths_result", result: replyOf("ledger_cited_paths", 200, { paths }) }]);
}

/** A state with a file and the delete mode chosen, ready to preview. */
function ready(): BlastState {
  return run(
    [
      { type: "input_changed", change: { kind: "path", index: 1 } },
      { type: "input_changed", change: { kind: "mode", mode: "delete" } },
    ],
    listed(),
  );
}

/** A state showing the given report, as the reducer would make it. */
function done(report: BlastReport): BlastState {
  const state = ready();
  return run(
    [
      { type: "preview_requested", generation: state.generation },
      {
        type: "preview_result",
        generation: state.generation,
        fields: { path: "policies/encryption-policy.md", change: { kind: "delete" }, customerId: null },
        result: replyOf("ledger_blast_radius", 200, report),
      },
    ],
    state,
  );
}

/** A state showing the given refusal. */
function failed(result: ReturnType<typeof errorOf> | { kind: "proxy_error"; error: "too_large" | "timeout" } | { kind: "invalid_response" }): BlastState {
  const state = ready();
  return run(
    [
      { type: "preview_requested", generation: state.generation },
      {
        type: "preview_result",
        generation: state.generation,
        fields: { path: "policies/encryption-policy.md", change: { kind: "delete" }, customerId: null },
        result: result as never,
      },
    ],
    state,
  );
}

function itemsOf(html: string): string[] {
  return html
    .split("<li ")
    .filter((part) => part.startsWith('class="alibi-block grid min-w-0 gap-2 border border-border p-3" data-promise='));
}

/** Every state the screen can be in, for the "always visible" checks. */
function everyState(): BlastState[] {
  return [
    INITIAL_BLAST_STATE,
    listed(),
    listed(citedPaths("empty")),
    run([{ type: "paths_result", result: errorOf("ledger_cited_paths", 503, "ledger_root_not_configured") }]),
    ready(),
    run([{ type: "preview_requested", generation: ready().generation }], ready()),
    done(blastReport([blastItem()])),
    done(blastReport([], { checked: 0, note: BLAST_NO_MATCH_LINE })),
    failed(errorOf("ledger_blast_radius", 503, "vault_root_not_configured")),
  ];
}

describe("the fixed statements are the approved words", () => {
  it("each constant matches its literal", () => {
    expect(BLAST_SCREEN_COVERAGE).toBe(WORDS.coverage);
    expect(BLAST_NEW_FLAG_LINE).toBe(WORDS.newFlag);
    expect(BLAST_NEW_FLAG_WORD).toBe("New");
    expect(BLAST_PASTE_NOTE).toBe(WORDS.paste);
    expect(BLAST_PREVIEW_LINE).toBe(WORDS.preview);
    expect(BLAST_NO_MATCH_LINE).toBe(WORDS.noMatch);
    expect(RECALL_STATEMENT).toBe(WORDS.statement);
    expect(RECALL_SCOPE).toBe(WORDS.scope);
    expect(BLAST_COVERAGE).toBe(WORDS.sidecarCoverage);
  });

  it("the coverage sentence and the preview line are visible above the results in every state", () => {
    for (const state of everyState()) {
      const html = render(state);
      const text = textOf(html);
      expect(text).toContain(WORDS.coverage);
      expect(text).toContain(WORDS.preview);
      const coverage = html.indexOf(WORDS.coverage);
      expect(coverage).toBeGreaterThan(0);
      expect(html.indexOf(WORDS.preview)).toBeGreaterThan(0);
      expect(coverage).toBeLessThan(html.indexOf(">Preview</button>"));
      const results = html.indexOf('data-report="preview"');
      if (results > 0) {
        expect(coverage).toBeLessThan(results);
        expect(html.indexOf(WORDS.preview)).toBeLessThan(results);
      }
    }
  });

  it("never shows the sidecar's own coverage string", () => {
    for (const state of everyState()) {
      expect(render(state)).not.toContain(WORDS.sidecarCoverage);
    }
  });

  it("the paste note is always visible next to the proposed-text box", () => {
    for (const state of everyState()) {
      const html = render(state);
      const note = html.indexOf(WORDS.paste);
      const box = html.indexOf("<textarea");
      expect(note).toBeGreaterThan(0);
      expect(box).toBeGreaterThan(note);
      // Next to it: inside the same field group, with nothing but the label wrapper between.
      expect(html.slice(note, box)).not.toContain("<fieldset");
      expect(html.slice(note, box)).not.toContain("<select");
      expect(html.slice(note, box).length).toBeLessThan(120);
    }
  });

  it("the fixed statements sit in the statement block at body size, never small", () => {
    const html = render(done(blastReport([blastItem()])));
    for (const words of [WORDS.coverage, WORDS.preview, WORDS.statement, WORDS.scope, WORDS.newFlag, WORDS.paste]) {
      const blocks = html.match(new RegExp(`<p class="alibi-block[^"]*">${escapeHtml(words).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}</p>`, "g")) ?? [];
      expect(blocks.length, words).toBeGreaterThan(0);
      for (const block of blocks) {
        expect(block, words).not.toMatch(/text-xs|text-sm/);
      }
    }
  });
});

describe("the results", () => {
  it("the recall statement and scope appear above the list and inside each item, from the constants", () => {
    const html = render(done(blastReport([blastItem({ promise_id: PROMISE_1 }), blastItem({ promise_id: PROMISE_2 })])));
    const first = html.indexOf(WORDS.statement);
    expect(first).toBeGreaterThan(0);
    expect(first).toBeLessThan(html.indexOf("data-promise="));
    expect(html.indexOf(WORDS.scope)).toBeLessThan(html.indexOf("data-promise="));
    const items = itemsOf(html);
    expect(items).toHaveLength(2);
    for (const item of items) {
      expect(item).toContain(WORDS.statement);
      expect(item).toContain(WORDS.scope);
      expect(item).toContain(WORDS.preview);
    }
    expect(html.split(WORDS.statement)).toHaveLength(4);
    expect(html.split(WORDS.scope)).toHaveLength(4);
  });

  it("shows the window's constants even when the reply carries other sentences", () => {
    const other = "intact means the promise is still true.";
    const report = blastReport(
      [blastItem({ statement: other as never, scope: "all fine" as never, preview: "saved" as never })],
      {
        statement: other as never,
        scope: "all fine" as never,
        coverage: "everything is checked" as never,
        preview: "saved" as never,
      },
    );
    const html = render(done(report));
    expect(html).not.toContain(other);
    expect(html).not.toContain("all fine");
    expect(html).not.toContain("everything is checked");
    expect(html).not.toContain(">saved<");
    expect(html.split(WORDS.statement)).toHaveLength(3);
  });

  it("the new-flag line is visible whenever results show, and only then", () => {
    expect(textOf(render(done(blastReport([blastItem()]))))).toContain(WORDS.newFlag);
    expect(textOf(render(done(blastReport([], { checked: 0 }))))).toContain(WORDS.newFlag);
    expect(textOf(render(ready()))).not.toContain(WORDS.newFlag);
  });

  it("a report with no items shows the no-match line from the constant", () => {
    const html = render(done(blastReport([], { checked: 0, note: "other words" as never })));
    expect(textOf(html)).toContain(WORDS.noMatch);
    expect(html).not.toContain("other words");
  });

  it("is headed as a preview, and never reads like a completed write", () => {
    const text = textOf(render(done(blastReport([blastItem({ would_change: true, state_proposed: "missing" })]))));
    expect(text).toContain("Preview result");
    expect(text).toContain("State would change: yes");
    expect(text).toContain("Proposed state:");
    expect(text).not.toMatch(/\b(Saved|Deleted|Recorded|Done)\b/);
  });

  it("shows checked, would change and the counts exactly as sent, with no locale formatting", () => {
    const html = render(
      done(blastReport([blastItem()], { checked: 12345, would_change: 1000, counts: { intact: 0, moved: 0, changed: 12345, missing: 0 } })),
    );
    const text = textOf(html);
    expect(text).toContain("Checked: 12345 · Would change state: 1000");
    expect(text).not.toContain("12,345");
    expect(html).toContain('<code class="font-mono" translate="no">changed</code>: 12345');
  });

  it("each item shows promise, customer, source path and line range, both states and the proposed detail", () => {
    const html = render(
      done(
        blastReport([
          blastItem({
            state_now: "intact",
            state_proposed: "moved",
            proposed_detail: null,
            locations: [{ start_line: 20, end_line: 21 }],
            locations_not_listed: 3,
          }),
          blastItem({ promise_id: PROMISE_2, state_now: "changed", state_proposed: "missing", proposed_detail: "not_found" }),
        ]),
      ),
      { customers: { phase: "listed", customers: [customer()] } },
    );
    const text = textOf(html);
    expect(text).toContain(`Promise: ${PROMISE_1}`);
    expect(text).toContain("Customer: Acme Test Co");
    expect(text).toContain("policies/encryption-policy.md");
    expect(text).toContain("Line 8");
    expect(text).toContain("State now: Intact");
    expect(text).toContain("Proposed state: Moved");
    expect(text).toContain("In the proposed text, found at: Lines 20 to 21");
    expect(text).toContain("Places not listed: 3");
    expect(html).toContain('<code class="font-mono" translate="no">not_found</code>');
  });

  it("a proposed detail outside the closed set shows as unrecognised and is not echoed", () => {
    const html = render(done(blastReport([blastItem({ proposed_detail: "all_good" as never })])));
    expect(textOf(html)).toContain("Proposed detail: unrecognised detail");
    expect(html).not.toContain("all_good");
  });

  it("an unknown state now or proposed shows the error block, never a label", () => {
    for (const over of [{ state_now: "fine" }, { state_proposed: "fine" }]) {
      const html = render(done(blastReport([blastItem(over as Partial<BlastItem>)])));
      expect(html).toContain("Unknown state: ALIBI cannot show a label for this value.");
      expect(html).not.toContain(">fine<");
    }
  });

  it("each known state shows its shape and word", () => {
    for (const state of RECALL_STATES) {
      const html = render(done(blastReport([blastItem({ state_now: state, state_proposed: state })])));
      expect(html.match(new RegExp(`data-state="${state}"`, "g"))).toHaveLength(2);
    }
  });
});

describe("flags now and proposed, and the New marker (DESIGN.md 11.3)", () => {
  it("shows both lists side by side, and a flag only in the proposed set is marked New", () => {
    const html = render(done(blastReport([blastItem({ flags: ["review_overdue", "document_changed_elsewhere"], flags_now: ["document_changed_elsewhere"] })])));
    const text = textOf(html);
    expect(text).toContain("Flags now: Document changed elsewhere");
    expect(text).toContain("Flags if this change were saved: New Review overdue Document changed elsewhere");
    const marked = html.match(/<span class="([^"]*)" data-flag="new">.*?<\/span><\/span>/g) ?? [];
    expect(marked).toHaveLength(1);
    expect(marked[0]).toContain(">New</span><span>Review overdue</span>");
  });

  it("the marker is a word and a thicker bordered box, never a new colour", () => {
    const html = render(done(blastReport([blastItem({ flags: ["review_overdue"], flags_now: [] })])));
    const box = html.match(/<span class="([^"]*)" data-flag="new">/)?.[1] ?? "";
    expect(box.split(" ")).toEqual(expect.arrayContaining(["border-2", "border-border"]));
    expect(box).not.toMatch(/(^|\s)(bg|text)-/);
    expect(box).not.toMatch(/status-|edited-|badge-/);
  });

  it("a flag in both sets is not marked, and an empty proposed set marks nothing", () => {
    const both = render(done(blastReport([blastItem({ flags: ["review_overdue"], flags_now: ["review_overdue"] })])));
    expect(both).not.toContain('data-flag="new"');
    const none = render(done(blastReport([blastItem({ flags: [], flags_now: [...RECALL_FLAGS] })])));
    expect(none).not.toContain('data-flag="new"');
    expect(textOf(none)).toContain("Flags if this change were saved: none");
    expect(textOf(none)).toContain("Front matter invalid");
  });

  it("an unknown flag shows the error block, never the New marker and never its word", () => {
    for (const over of [{ flags: ["made_up_flag"], flags_now: [] }, { flags: [], flags_now: ["made_up_flag"] }]) {
      const html = render(done(blastReport([blastItem(over as Partial<BlastItem>)])));
      expect(html).toContain("Unknown flag: ALIBI cannot show a label for this value.");
      expect(html).not.toContain("made_up_flag");
      expect(html).not.toContain('data-flag="new"');
    }
  });
});

describe("the inputs", () => {
  it("offers the cited files with their promise counts, and no free-text path box", () => {
    const html = render(listed());
    expect(html).toContain('<select id="blast-path"');
    expect(html).toContain('<option value="0">policies/backup-policy.md (promises: 1)</option>');
    expect(html).toContain('<option value="1">policies/encryption-policy.md (promises: 1)</option>');
    expect(html).not.toMatch(/<input[^>]*type="text"/);
  });

  it("is a radio pair: exactly two radios of one group, none chosen at first", () => {
    const html = render(listed());
    const radios = html.match(/<input[^>]*type="radio"[^>]*>/g) ?? [];
    expect(radios).toHaveLength(2);
    for (const radio of radios) {
      expect(radio).toContain('name="blast-mode"');
      expect(radio).not.toContain("checked");
    }
    expect(textOf(html)).toContain("Preview new text for this file");
    expect(textOf(html)).toContain("Preview deleting this file");
    expect(textOf(html)).toContain("Choose a cited file first.");
  });

  it("each radio sits inside its label, so the control and its words are one hit target (design review)", () => {
    const html = render(listed());
    const labels = html.match(/<label class="[^"]*"><input[^>]*type="radio"[^>]*\/?><span>[^<]+<\/span><\/label>/g) ?? [];
    expect(labels).toHaveLength(2);
  });

  it("a customer id shown in place of a name is marked translate=no; a name is not (design review)", () => {
    const unnamed = render(done(blastReport([blastItem()])));
    expect(unnamed).toContain('translate="no">00000001000000000000000000000000</span>');
    const named = render(done(blastReport([blastItem()])), { customers: { phase: "listed", customers: [customer()] } });
    expect(named).toMatch(/Customer: <span class="[^"]*">Acme Test Co<\/span>/);
  });

  it("shows the byte count against the limit, and over the limit refuses with Preview off", () => {
    const base = run([{ type: "input_changed", change: { kind: "mode", mode: "text" } }], run([{ type: "input_changed", change: { kind: "path", index: 0 } }], listed()));
    const fine = run([{ type: "input_changed", change: { kind: "text", text: String.fromCodePoint(0xe9).repeat(3) } }], base);
    expect(textOf(render(fine))).toContain("Size in UTF-8 bytes: 6 (limit 2097152)");
    const over = run([{ type: "input_changed", change: { kind: "text", text: "a".repeat(2_097_153) } }], base);
    const html = render(over);
    expect(textOf(html.replace(/<textarea[\s\S]*?<\/textarea>/, ""))).toContain("Size in UTF-8 bytes: 2097153 (limit 2097152)");
    expect(textOf(html.replace(/<textarea[\s\S]*?<\/textarea>/, ""))).toContain("The proposed text is over the limit. Nothing was sent.");
    expect(html.match(/<button[^>]*>Preview<\/button>/)?.[0]).toContain('disabled=""');
    expect(render(fine).match(/<button[^>]*>Preview<\/button>/)?.[0]).not.toContain('disabled=""');
  });

  it("the text box is off unless the new-text choice is made", () => {
    const box = (html: string) => html.match(/<textarea[^>]*>/)?.[0] ?? "";
    expect(box(render(ready()))).toContain('disabled=""');
    const text = run([{ type: "input_changed", change: { kind: "mode", mode: "text" } }], ready());
    expect(box(render(text))).not.toContain('disabled=""');
  });

  it("offers the customer filter once customers are listed, with all customers first", () => {
    expect(render(listed())).not.toContain('id="blast-customer"');
    const html = render(listed(), { customers: { phase: "listed", customers: [customer()] } });
    expect(html).toContain('<label for="blast-customer"');
    expect(html).toContain('<option value="" selected="">All customers</option>');
  });

  it("has a Preview button and no confirm step, no write control", () => {
    for (const state of everyState()) {
      const html = render(state);
      expect(html).toMatch(/<button[^>]*>Preview<\/button>/);
      expect(html).not.toMatch(/Confirm|Void|Import|Acknowledge/);
      for (const button of html.match(/<button[^>]*>/g) ?? []) {
        expect(button).toContain('type="button"');
      }
    }
  });
});

describe("every state has its own words", () => {
  it("loading the paths says so", () => {
    expect(textOf(render(run([{ type: "paths_requested" }])))).toContain("Reading the cited files.");
  });

  it("ledger not configured is a refusal with its code, never 0 and never an empty list", () => {
    const html = render(run([{ type: "paths_result", result: errorOf("ledger_cited_paths", 503, "ledger_root_not_configured") }]));
    const text = textOf(html);
    expect(text).toContain("No ledger folder is in use.");
    expect(html).toContain('<code class="font-mono">ledger_root_not_configured</code>');
    expect(text).not.toContain("No live promise cites any file yet");
    // The file part of the screen (before the change box, whose byte count is the window's own).
    expect(textOf(html.slice(0, html.indexOf("<fieldset")))).not.toMatch(/\b0\b/);
    expect(html).not.toContain("<select");
  });

  it("an empty list of cited files says nothing is cited yet", () => {
    const html = render(listed(citedPaths("empty")));
    expect(textOf(html)).toContain("No live promise cites any file yet, so there is nothing to preview.");
    expect(html).not.toContain('id="blast-path"');
    expect(html.match(/<button[^>]*>Preview<\/button>/)?.[0]).toContain('disabled=""');
  });

  it("too_many_paths has its own words and integer counts only", () => {
    const html = render(run([{ type: "paths_result", result: replyOf("ledger_cited_paths", 422, recordedCited("too_many_paths").body) }]));
    expect(textOf(html)).toContain("There are too many cited paths to list.");
    expect(html).toContain("recall_refused:too_many_paths");
    expect(html).toContain('<code class="font-mono" translate="no">paths</code>: 2');
    expect(html).toContain('<code class="font-mono" translate="no">cap</code>: 1');
  });

  const refusals: [string, Parameters<typeof failed>[0], string, string][] = [
    ["vault not configured", errorOf("ledger_blast_radius", 503, "vault_root_not_configured"), "vault_root_not_configured", "No vault folder is in use."],
    ["ledger not configured", errorOf("ledger_blast_radius", 503, "ledger_root_not_configured"), "ledger_root_not_configured", "No ledger folder is in use."],
    ["busy", errorOf("ledger_blast_radius", 409, "run_in_progress"), "run_in_progress", "another job is running"],
    ["text too large", errorOf("ledger_blast_radius", 422, "blast_radius_refused", { code: "proposed_text_too_large", counts: { bytes: 2097153, cap: 2097152 } }), "blast_radius_refused:proposed_text_too_large", "The proposed text is larger than ALIBI accepts for one file."],
    ["text has a NUL", replyOf("ledger_blast_radius", 422, recordedBlast("proposed_text_has_nul").body) as never, "blast_radius_refused:proposed_text_has_nul", "The proposed text contains a NUL character, which ALIBI refuses."],
    ["text not UTF-8", errorOf("ledger_blast_radius", 422, "blast_radius_refused", { code: "proposed_text_not_utf8" }), "blast_radius_refused:proposed_text_not_utf8", "The proposed text cannot be encoded as UTF-8."],
    ["too many promises", replyOf("ledger_blast_radius", 422, recordedBlast("too_many_promises").body) as never, "blast_radius_refused:too_many_promises", "There are too many promises on this file to preview at once."],
    ["a reply too large for the host", { kind: "proxy_error", error: "too_large" }, "too_large", "The sidecar's answer was larger than ALIBI accepts."],
    ["a proxy error", { kind: "proxy_error", error: "timeout" }, "timeout", "The sidecar did not answer in time."],
    ["an invalid answer", { kind: "invalid_response" }, "invalid_response", "The answer did not match what ALIBI expects, so it was not used."],
  ];

  for (const [name, result, code, words] of refusals) {
    it(`${name} is a refusal with its own words, not a result`, () => {
      const html = render(failed(result));
      const text = textOf(html);
      expect(html).toContain(`<code class="font-mono">${code}</code>`);
      expect(text).toContain(words);
      expect(text).not.toContain("Preview result");
      expect(text).not.toContain("Checked:");
      expect(text).not.toContain("The sidecar refused the request.");
    });
  }

  it("the refusal counts are integers only, shown as sent", () => {
    const html = render(
      failed(errorOf("ledger_blast_radius", 422, "blast_radius_refused", { code: "too_many_promises", counts: { promises: 2001, cap: 2000, bad: 1.5 } })),
    );
    expect(html).toContain('<code class="font-mono" translate="no">promises</code>: 2001');
    expect(html).toContain('<code class="font-mono" translate="no">cap</code>: 2000');
    expect(html).not.toContain(">bad<");
  });

  it("previewing says so and turns the inputs and the button off", () => {
    const html = render(run([{ type: "preview_requested", generation: ready().generation }], ready()));
    expect(textOf(html)).toContain("Previewing.");
    expect(html.match(/<button[^>]*>Preview<\/button>/)?.[0]).toContain('disabled=""');
    expect(html.match(/<select id="blast-path"[^>]*>/)?.[0]).toContain('disabled=""');
  });

  it("everything is off while the sidecar is not healthy", () => {
    const html = render(ready(), { enabled: false });
    for (const control of html.match(/<(button|select|textarea|input)[^>]*>/g) ?? []) {
      expect(control, control).toContain('disabled=""');
    }
  });
});

describe("personal data and hostile text (F36-1)", () => {
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

  it("a canary in a customer name, a cited path, an item path and the proposed text is plain text, never a link, never logged", () => {
    const paths = [{ source_path: CANARY, promise_count: 1 }];
    const chosen = run(
      [
        { type: "input_changed", change: { kind: "path", index: 0 } },
        { type: "input_changed", change: { kind: "mode", mode: "text" } },
        { type: "input_changed", change: { kind: "text", text: CANARY } },
      ],
      listed(paths),
    );
    const shown = run(
      [
        { type: "preview_requested", generation: chosen.generation },
        {
          type: "preview_result",
          generation: chosen.generation,
          fields: { path: CANARY, change: { kind: "text", text: CANARY }, customerId: null },
          result: replyOf("ledger_blast_radius", 200, blastReport([blastItem({ source_path: CANARY })])),
        },
      ],
      chosen,
    );
    const customers: CustomersView = { phase: "listed", customers: [customer({ display_name: CANARY })] };
    for (const state of [chosen, shown]) {
      const html = render(state, { customers });
      expect(html).toContain(escapeHtml(visibleControls(CANARY)));
      expect(html).not.toContain("<a ");
      expect(html).not.toContain('href="');
      expect(html).not.toContain(CANARY);
    }
    for (const [index, spy] of spies.entries()) {
      expect(spy, METHODS[index]).not.toHaveBeenCalled();
    }
  });

  it("control and direction characters in a path are shown as visible marks", () => {
    const odd = `policies/${String.fromCodePoint(0x202e)}gpj.md`;
    const html = render(listed([{ source_path: odd, promise_count: 1 }]));
    expect(html).toContain("policies/[U+202E]gpj.md");
    expect(html).not.toContain(String.fromCodePoint(0x202e));
  });

  it("renders no style attribute, link, truncation or locale formatting in any state", () => {
    for (const state of everyState()) {
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
