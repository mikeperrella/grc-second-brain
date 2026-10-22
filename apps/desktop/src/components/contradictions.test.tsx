// The Contradictions screen as static markup (chunk 65i; ADR-019 and its addendum, ADR-022
// decisions 2 and 8, threat model F33-9 to F33-11, F36-1; docs/DESIGN.md 4.5 and 11.1). No jsdom:
// every state is built with the reducer the hook drives (contradictions.ts) and rendered to a
// string. The approved sentences are spelled out as literals.

import { renderToStaticMarkup } from "react-dom/server";
import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";
import {
  INITIAL_CONTRADICTIONS_STATE,
  type ContradictionReport,
  type ContradictionsState,
  type Finding,
} from "../contradictions";
import {
  RUN_ID,
  complete,
  finding,
  report,
  run,
  running,
  startReply,
  statusReply,
} from "../contradictionsTestSupport";
import {
  CONTRADICTIONS_BOTH_QUOTES,
  CONTRADICTIONS_MODEL_VARIES,
  CONTRADICTIONS_NOT_KEPT,
  CONTRADICTIONS_NUMERIC_ONLY_STATEMENT,
} from "../fixedTexts";
import { CONTRADICTION_FAILURES } from "../ipc/proxy";
import { CANARY } from "../ledgerTestSupport";
import { visibleControls } from "../plainText";
import { errorOf, escapeHtml, textOf } from "../testSupport";
import { ContradictionsScreen, QUOTE_BLOCK_CLASS } from "./ContradictionsScreen";

const NOOP = () => undefined;

// DESIGN.md 11.1 (approved 2026-10-19) and the sidecar's recorded numbers-only sentence.
const WORDS = {
  numbersOnly:
    "This check compares numbers only (durations, percentages, counts, frequencies). A conflict without numbers is not found, and no finding does not mean the policies agree.",
  modelVaries: "The model's answer for the same pair can differ between identical runs.",
  notKept: "Findings are not kept. Closing ALIBI or starting another run drops them.",
  bothQuotes: "Both quotes are shown so you can decide. ALIBI does not decide which statement is right.",
  valueConflict: "Numbers differ (the model kept this pair in this run)",
  candidate: "Numbers differ (not kept)",
} as const;

function render(state: ContradictionsState, enabled = true): string {
  return renderToStaticMarkup(
    <ContradictionsScreen state={state} enabled={enabled} onInputChange={NOOP} onStart={NOOP} />,
  );
}

/** A state showing a report built from `findings`. */
function shown(findings: Finding[], over: Partial<ContradictionReport> = {}): ContradictionsState {
  return complete(report({ findings, ...over }));
}

function startButton(html: string): string {
  const found = html.match(/<button[^>]*>Start<\/button>/);
  expect(found).not.toBeNull();
  // The class string holds "disabled:border-dashed"; only the attribute counts as disabled.
  return (found?.[0] ?? "").replace(/class="[^"]*"/, "");
}

/** Every state the screen can be in, for the "always visible" and "never" checks. */
function everyState(): ContradictionsState[] {
  return [
    INITIAL_CONTRADICTIONS_STATE,
    run([{ type: "start_requested" }]),
    running(),
    shown([finding()]),
    shown([]),
    shown([finding({ status: "CANDIDATE", reasons: ["weak_subject_overlap"], flags: [], verdict: null })]),
    run([{ type: "status_result", runId: RUN_ID, result: statusReply({ status: "complete", report: null }) }], running()),
    run([{ type: "status_result", runId: RUN_ID, result: statusReply({ status: "failed", failure: null }) }], running()),
    run([{ type: "status_result", runId: RUN_ID, result: statusReply({ status: "cancelled" }) }], running()),
    run([{ type: "status_result", runId: RUN_ID, result: errorOf("contradictions_status", 404, "run_not_found") }], running()),
    run([
      { type: "start_requested" },
      { type: "start_result", result: errorOf("contradictions_start", 503, "vault_root_not_configured") },
    ]),
  ];
}

describe("the fixed statements are the approved words", () => {
  it("each constant matches its literal", () => {
    expect(CONTRADICTIONS_NUMERIC_ONLY_STATEMENT).toBe(WORDS.numbersOnly);
    expect(CONTRADICTIONS_MODEL_VARIES).toBe(WORDS.modelVaries);
    expect(CONTRADICTIONS_NOT_KEPT).toBe(WORDS.notKept);
    expect(CONTRADICTIONS_BOTH_QUOTES).toBe(WORDS.bothQuotes);
  });

  it("all four are always visible with results, with findings or none", () => {
    for (const state of [shown([finding()]), shown([])]) {
      const html = render(state);
      for (const sentence of Object.values({ a: WORDS.numbersOnly, b: WORDS.modelVaries, c: WORDS.notKept, d: WORDS.bothQuotes })) {
        expect(textOf(html)).toContain(sentence);
      }
    }
  });

  it("are the window's constants: a sidecar string that differs is never shown", () => {
    const html = render(shown([finding()], { says: "A DIFFERENT SIDECAR SENTENCE" } as never));
    expect(html).not.toContain("A DIFFERENT SIDECAR SENTENCE");
    expect(textOf(html)).toContain(WORDS.numbersOnly);
  });

  it("are not shown before there are results", () => {
    for (const state of [INITIAL_CONTRADICTIONS_STATE, running()]) {
      const text = textOf(render(state));
      expect(text).not.toContain(WORDS.numbersOnly);
      expect(text).not.toContain(WORDS.notKept);
    }
  });

  it("the statements are bordered blocks in body text, never in an attribute", () => {
    const html = render(shown([finding()]));
    for (const sentence of [WORDS.numbersOnly, WORDS.modelVaries, WORDS.notKept, WORDS.bothQuotes]) {
      expect(html).toContain(`>${escapeHtml(sentence)}</p>`);
      expect(html).not.toContain(`="${escapeHtml(sentence)}`);
    }
  });
});

describe("the start controls", () => {
  it("is enabled when idle and healthy", () => {
    expect(startButton(render(INITIAL_CONTRADICTIONS_STATE))).not.toContain("disabled");
  });

  it("is disabled while starting, while running and while the sidecar is not healthy", () => {
    expect(startButton(render(run([{ type: "start_requested" }])))).toContain("disabled");
    expect(startButton(render(running()))).toContain("disabled");
    expect(startButton(render(INITIAL_CONTRADICTIONS_STATE, false))).toContain("disabled");
  });

  it("is disabled, with the problem in words, while an input problem exists", () => {
    const bad = run([{ type: "input_changed", field: "scope", value: "../x" }]);
    const html = render(bad);
    expect(startButton(html)).toContain("disabled");
    expect(textOf(html)).toContain("The scope is not a valid vault path prefix. Nothing was sent.");
    const badCap = run([{ type: "input_changed", field: "cap", value: "500" }]);
    expect(startButton(render(badCap))).toContain("disabled");
    expect(textOf(render(badCap))).toContain("The pair cap must be a whole number from 1 to 100. Nothing was sent.");
  });

  it("has no cancel control", () => {
    for (const state of everyState()) {
      expect(textOf(render(state))).not.toMatch(/cancel|stop the run|abort/i);
    }
  });

  it("locks both inputs while a run is going", () => {
    const inputs = render(running()).match(/<input[^>]*>/g) ?? [];
    expect(inputs).toHaveLength(2);
    for (const input of inputs) {
      expect(input).toContain("disabled");
    }
  });
});

describe("the states", () => {
  it("says no run has been started before the first Start", () => {
    expect(textOf(render(INITIAL_CONTRADICTIONS_STATE))).toContain("No run has been started.");
  });

  it("running with a known total shows both numbers, a polite status, no estimate, no percent and no 'stuck'", () => {
    const state = run(
      [{ type: "status_result", runId: RUN_ID, result: statusReply({ status: "running", pairs_total: 55, pairs_done: 2 }) }],
      running(),
    );
    const html = render(state);
    expect(html).toContain('role="status"');
    expect(textOf(html)).toContain("Pairs done: 2 of 55");
    expect(html).not.toMatch(/stuck|<progress|role="progressbar"|%|percent|estimate/i);
  });

  it("running with a null total says the pairs are still being counted, never 0", () => {
    const html = render(running());
    expect(textOf(html)).toContain("The pairs are still being counted.");
    expect(textOf(html)).not.toMatch(/ of 0\b|Pairs done: 0 of/);
    expect(html).not.toMatch(/stuck|<progress|role="progressbar"|%/i);
  });

  it("complete with none says so, and still shows the statements and the counts", () => {
    const html = render(shown([]));
    expect(textOf(html)).toContain("The run finished with no findings.");
    expect(textOf(html)).toContain(WORDS.numbersOnly);
    expect(textOf(html)).toContain("Findings: 0");
  });

  it("complete with a null report shows the error block, never 'no findings'", () => {
    const html = render(
      run([{ type: "status_result", runId: RUN_ID, result: statusReply({ status: "complete", report: null }) }], running()),
    );
    expect(html).toContain('role="alert"');
    expect(textOf(html)).toContain("The run finished but sent no report, so ALIBI shows no result.");
    expect(textOf(html)).not.toContain("no findings");
    expect(textOf(html)).not.toContain(WORDS.numbersOnly);
  });

  it("failed shows each failure word's own text and code, and a null word its own text", () => {
    const seen = new Set<string>();
    for (const word of [...CONTRADICTION_FAILURES, null]) {
      const html = render(
        run([{ type: "status_result", runId: RUN_ID, result: statusReply({ status: "failed", failure: word }) }], running()),
      );
      expect(html).toContain('role="alert"');
      const text = textOf(html);
      const message = text.match(/The run failed[^.]*\./)?.[0] ?? "";
      expect(message.length, String(word)).toBeGreaterThan(20);
      seen.add(message);
      expect(text).toContain(`Code: ${word ?? "failure_word_missing"}`);
    }
    expect(seen.size).toBe(CONTRADICTION_FAILURES.length + 1);
  });

  it("cancelled says the sidecar stopped the run when it shut down", () => {
    const html = render(
      run([{ type: "status_result", runId: RUN_ID, result: statusReply({ status: "cancelled" }) }], running()),
    );
    expect(textOf(html)).toContain("The run was stopped when the sidecar shut down.");
    expect(html).not.toContain('role="alert"');
  });

  it("run_not_found says the run is no longer known", () => {
    const html = render(
      run([{ type: "status_result", runId: RUN_ID, result: errorOf("contradictions_status", 404, "run_not_found") }], running()),
    );
    expect(textOf(html)).toContain("This run is no longer known.");
    expect(textOf(html)).toContain("Code: run_not_found");
  });

  it("each typed start refusal shows its own words and code, not an empty result", () => {
    const seen = new Set<string>();
    for (const [status, code] of [
      [503, "vault_root_not_configured"],
      [409, "index_not_built"],
      [409, "run_in_progress"],
    ] as const) {
      const state = run([{ type: "start_requested" }, { type: "start_result", result: errorOf("contradictions_start", status, code) }]);
      const text = textOf(render(state));
      expect(text).toContain(`Code: ${code}`);
      expect(text).not.toContain("The run finished");
      seen.add(text);
    }
    expect(seen.size).toBe(3);
    const busy = run([{ type: "start_requested" }, { type: "start_result", result: errorOf("contradictions_start", 409, "run_in_progress") }]);
    expect(textOf(render(busy))).toContain("another job is running");
    // ALIBI does not retry by itself: the only control is Start, which the person presses.
    expect(render(busy).match(/<button/g)).toHaveLength(1);
  });

  it("proxy error words, an invalid response and a reply over the size cap show their own words", () => {
    for (const [result, code, words] of [
      [{ kind: "proxy_error", route: "contradictions_start", error: "too_large" }, "too_large", "larger than ALIBI accepts"],
      [{ kind: "proxy_error", route: "contradictions_start", error: "timeout" }, "timeout", "did not answer in time"],
      [{ kind: "invalid_response" }, "invalid_response", "did not match what ALIBI expects"],
    ] as const) {
      const state = run([{ type: "start_requested" }, { type: "start_result", result: result as never }]);
      const text = textOf(render(state));
      expect(text).toContain(`Code: ${code}`);
      expect(text).toContain(words);
    }
  });

  it("every state renders without the words detected, confirmed, conflict found, 'with the reason shown' or stuck", () => {
    for (const state of everyState()) {
      expect(textOf(render(state))).not.toMatch(/detected|confirmed|conflict found|with the reason shown|stuck/i);
    }
  });
});

describe("a finding", () => {
  it("VALUE_CONFLICT carries the 11.1 label, with shape and word, and CANDIDATE its own", () => {
    const html = render(
      shown([
        finding(),
        finding({ finding_id: "ffffffffffffffff", status: "CANDIDATE", reasons: ["weak_subject_overlap"], verdict: null }),
      ]),
    );
    expect(html).toContain(`data-status="VALUE_CONFLICT"`);
    expect(html).toContain(`data-status="CANDIDATE"`);
    expect(textOf(html)).toContain(WORDS.valueConflict);
    expect(textOf(html)).toContain(WORDS.candidate);
    // A word beside a shape, not colour alone: every chip holds an SVG and its label.
    const chips = html.match(/<span class="inline-flex[^"]*"[^>]*data-status="[A-Z_]+">.*?<\/span><\/span>/g) ?? [];
    expect(chips).toHaveLength(2);
    for (const chip of chips) {
      expect(chip).toContain("<svg");
    }
    expect(new Set(chips.map((chip) => chip.match(/<path d="([^"]*)"/)?.[1])).size).toBe(2);
    expect(html).not.toMatch(/detected|confirmed|conflict found/i);
  });

  it("the CANDIDATE label does not print 'with the reason shown', and the reasons are shown beside it", () => {
    const html = render(
      shown([finding({ status: "CANDIDATE", reasons: ["days_vs_months", "weak_subject_overlap"], flags: ["model_not_consulted"], verdict: null })]),
    );
    const text = textOf(html);
    expect(text).not.toContain("with the reason shown");
    expect(text).toContain("Reasons:");
    expect(text).toContain("Days compared with months");
    expect(text).toContain("Weak subject overlap");
    expect(text).toContain("Flags:");
    expect(text).toContain("Model not consulted");
  });

  it("each reason, flag and relation word has its own label on the screen", () => {
    const reasons = ["days_vs_months", "weak_subject_overlap", "no_model_verdict", "model_lowered", "model_quote_not_verified"] as const;
    const html = render(shown([finding({ status: "CANDIDATE", reasons: [...reasons], flags: ["model_not_consulted"], verdict: null })]));
    for (const label of [
      "Days compared with months",
      "Weak subject overlap",
      "No model verdict",
      "The model lowered this pair",
      "A model quote was not verified",
      "Model not consulted",
    ]) {
      expect(textOf(html)).toContain(label);
    }
    for (const [relation, label] of [
      ["same_subject_value_mismatch", "Same subject, different value"],
      ["scope_difference", "Different scope"],
      ["unrelated", "Unrelated"],
      ["unclear", "Unclear"],
    ] as const) {
      const one = render(shown([finding({ verdict: { conflict: true, relation, explanation: "x", model_confidence: 0.5 } })]));
      expect(textOf(one)).toContain(`Relation: ${label}`);
    }
  });

  it("shows the subject, the numbers, the two sources and the two quotes as plain text", () => {
    const html = render(shown([finding()]));
    const text = textOf(html);
    expect(text).toContain("Subject: Monitoring alerts");
    expect(text).toContain("2 hours");
    expect(text).toContain("12 hours");
    expect(text).toContain("housekeeping-notes-a.md:13");
    expect(text).toContain("housekeeping-notes-b.md:13");
    expect(text).toContain("Monitoring alerts are acknowledged within 2 hours during business hours.");
    expect(text).toContain("Monitoring alerts are acknowledged within 12 hours outside business hours.");
  });

  it("puts the two quotes in the sidecar's order, in two blocks with the same class string and markup", () => {
    const html = render(shown([finding()]));
    const blocks = html.match(/<div class="[^"]*" data-quote="block">.*?<\/p><\/div>/g) ?? [];
    expect(blocks).toHaveLength(2);
    const [a, b] = blocks;
    expect(a).toContain("housekeeping-notes-a.md:13");
    expect(b).toContain("housekeeping-notes-b.md:13");
    expect(a?.indexOf("Quote A")).toBeGreaterThan(-1);
    expect(b?.indexOf("Quote B")).toBeGreaterThan(-1);
    // The one class string, the same on both.
    const classes = blocks.map((block) => block.match(/^<div class="([^"]*)"/)?.[1]);
    expect(classes[0]).toBe(QUOTE_BLOCK_CLASS);
    expect(classes[1]).toBe(QUOTE_BLOCK_CLASS);
    // The same markup once the text is taken out: nothing in either block differs in structure.
    const skeleton = (block: string | undefined): string =>
      (block ?? "")
        .replace(/>[^<]+</g, "><")
        .replace(/\s+/g, " ");
    expect(skeleton(a)).toBe(skeleton(b));
  });

  it("keeps the same markup when the quote that is first changes (order is the sidecar's)", () => {
    const base = finding();
    const swapped = finding({ quote_a: base.quote_b, source_a: base.source_b, quote_b: base.quote_a, source_b: base.source_a });
    const html = render(shown([swapped]));
    const blocks = html.match(/<div class="[^"]*" data-quote="block">.*?<\/p><\/div>/g) ?? [];
    expect(blocks[0]).toContain("housekeeping-notes-b.md:13");
    expect(blocks[1]).toContain("housekeeping-notes-a.md:13");
  });

  it("collapses a long quote and a long explanation only behind show more, never a statement or a label", () => {
    const long = "word ".repeat(100);
    const html = render(
      shown([
        finding({
          quote_a: long,
          quote_b: long,
          verdict: { conflict: true, relation: "unclear", explanation: long, model_confidence: 0.5 },
        }),
      ]),
    );
    expect(html.match(/Show more/g)).toHaveLength(3);
    for (const sentence of [WORDS.numbersOnly, WORDS.modelVaries, WORDS.notKept, WORDS.bothQuotes, WORDS.valueConflict]) {
      expect(textOf(html)).toContain(sentence);
    }
  });

  it("shows the model's verdict as the model's words, in a labelled block, never as a statement or an error", () => {
    const html = render(shown([finding()]));
    expect(textOf(html)).toContain("The model's verdict:");
    expect(textOf(html)).toContain("The model's words:");
    expect(textOf(html)).toContain("The two statements set different values.");
    const words = html.match(/<div class="[^"]*" data-model-words="block">.*?<\/p><\/div>/)?.[0] ?? "";
    expect(words).toContain("The two statements set different values.");
    expect(words).not.toContain("bg-statement-bg");
    expect(words).not.toContain("status-error");
  });

  it("shows no verdict block when there is no verdict", () => {
    expect(textOf(render(shown([finding({ status: "CANDIDATE", verdict: null, reasons: ["no_model_verdict"], flags: ["model_not_consulted"] })])))).not.toContain(
      "The model's verdict",
    );
  });

  it("does not render model_confidence at all", () => {
    const html = render(
      shown([finding({ verdict: { conflict: true, relation: "unclear", explanation: "plain", model_confidence: 0.123456 } })]),
    );
    expect(html).not.toContain("0.123456");
    expect(html).not.toContain("0.12");
    expect(html).not.toMatch(/confidence/i);
  });
});

describe("a value outside a closed set shows the error block, never a label", () => {
  const unknownText = {
    status: "Unknown status: ALIBI cannot show a label for this value.",
    reason: "Unknown reason: ALIBI cannot show a label for this value.",
    flag: "Unknown flag: ALIBI cannot show a label for this value.",
    relation: "Unknown relation: ALIBI cannot show a label for this value.",
    model: "Unknown model status: ALIBI cannot show a label for this value.",
  } as const;

  it("an unknown status", () => {
    const html = render(shown([finding({ status: "detected" as never })]));
    expect(textOf(html)).toContain(unknownText.status);
    expect(html).toContain('role="alert"');
    expect(html).not.toMatch(/detected/i);
    expect(textOf(html)).not.toContain(WORDS.valueConflict);
  });

  it("an unknown reason", () => {
    const html = render(shown([finding({ status: "CANDIDATE", reasons: ["confirmed_by_model" as never], verdict: null })]));
    expect(textOf(html)).toContain(unknownText.reason);
    expect(html).not.toContain("confirmed_by_model");
  });

  it("an unknown flag", () => {
    const html = render(shown([finding({ flags: ["model_is_sure" as never] })]));
    expect(textOf(html)).toContain(unknownText.flag);
    expect(html).not.toContain("model_is_sure");
  });

  it("an unknown relation", () => {
    const html = render(shown([finding({ verdict: { conflict: true, relation: "definitely_wrong" as never, explanation: "x", model_confidence: 0.5 } })]));
    expect(textOf(html)).toContain(unknownText.relation);
    expect(html).not.toContain("definitely_wrong");
  });

  it("an unknown model status", () => {
    const html = render(shown([finding()], { model_status: "great" as never }));
    expect(textOf(html)).toContain(unknownText.model);
    expect(html).not.toContain("great");
  });
});

describe("the report block", () => {
  it("shows the model as plain text and its status from the exhaustive switch", () => {
    const html = render(shown([finding()]));
    expect(textOf(html)).toContain("Model: llama3.1:8b");
    expect(textOf(html)).toContain("Model status: Available");
    for (const [status, label] of [
      ["unavailable", "Unavailable"],
      ["model_missing", "Model missing"],
      ["non_local_refused", "Non-local model refused"],
      ["partial", "Partial"],
    ] as const) {
      expect(textOf(render(shown([finding()], { model_status: status })))).toContain(`Model status: ${label}`);
    }
  });

  it("shows the nine counts exactly as sent, tabular, never formatted", () => {
    const html = render(
      shown([finding()], {
        over_cap: 12345,
        no_verdict: 2,
        model_calls: 3,
        claims_found: 64,
        pairs_generated: 55,
        withheld_stale: 4,
        excluded_chunks: 5,
        chunks_read: 78901,
      }),
    );
    expect(html).toContain("tabular-nums");
    const text = textOf(html);
    for (const line of [
      "Findings: 1",
      "Left out by the cap: 12345",
      "Pairs with no model verdict: 2",
      "Model calls: 3",
      "Claims found: 64",
      "Pairs generated: 55",
      "Withheld (changed after indexing): 4",
      "Excluded chunks: 5",
      "Chunks read: 78901",
    ]) {
      expect(text).toContain(line);
    }
  });

  it("shows 'not available' and never 0 for a missing count", () => {
    const html = render(shown([finding()], { over_cap: null as never }));
    expect(textOf(html)).toContain("Left out by the cap: not available");
    expect(textOf(html)).not.toContain("Left out by the cap: 0");
  });
});

describe("untrusted text is plain text (F36-1, F33-10)", () => {
  const spies = (["log", "info", "warn", "error", "debug", "trace"] as const).map((method) =>
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

  it("a canary in a quote, a path, a subject, a number, an explanation and the model name is escaped text, never a link, and never logged", () => {
    const html = render(
      shown(
        [
          finding({
            quote_a: CANARY,
            source_a: CANARY,
            quote_b: CANARY,
            source_b: CANARY,
            subject: CANARY,
            numbers: [CANARY, CANARY],
            verdict: { conflict: true, relation: "unclear", explanation: CANARY, model_confidence: 0.5 },
          }),
        ],
        { model: CANARY },
      ),
    );
    expect(html).toContain(escapeHtml(visibleControls(CANARY)));
    expect(html).not.toContain("<a ");
    expect(html).not.toContain("<a>");
    // The canary's own text holds `href=&quot;` once escaped; a real attribute is `href="`.
    expect(html).not.toMatch(/\shref="/);
    expect(html).not.toContain("canary.invalid\"");
    expect(html.match(/<a[\s>]/g)).toBeNull();
    expect(html).not.toContain("style=");
    for (const spy of spies) {
      expect(spy).not.toHaveBeenCalled();
    }
  });

  it("makes control and direction characters visible instead of letting them act", () => {
    const hostile = `x${String.fromCodePoint(0x202e)}y${String.fromCodePoint(0)}z`;
    const html = render(shown([finding({ quote_a: hostile, subject: hostile })]));
    expect(html).not.toContain(String.fromCodePoint(0x202e));
    expect(html).toContain("[U+202E]");
    expect(html).toContain("[U+0000]");
  });

  it("no state renders a link, a form, a style attribute, a confirm step or a write control", () => {
    for (const state of everyState()) {
      const html = render(state);
      expect(html).not.toMatch(/<a[\s>]|\shref="|<form|style=|contenteditable/i);
      // Whole words only: a recorded quote says "acknowledged".
      expect(textOf(html)).not.toMatch(/\b(confirm|void|import|acknowledge|save|delete)\b/i);
    }
  });

  it("starting writes nothing: no state shows a start reply's run id as text", () => {
    const html = render(run([{ type: "start_requested" }, { type: "start_result", result: startReply() }]));
    expect(html).not.toContain(RUN_ID);
  });
});
