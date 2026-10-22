// The Contradictions screen's pure logic (chunk 65i; ADR-019 and its addendum, ADR-022 decisions 2
// and 8, threat model F33-9 to F33-11; docs/DESIGN.md 4.5 and 11.1). A run is started through
// `sendStart` and polled through `pollStatus`, the two functions the hook calls, with a recording
// fake `invoke`: what reaches it is exactly what the host would get.

import { describe, expect, it } from "vitest";
import {
  INITIAL_CONTRADICTIONS_STATE,
  INITIAL_CONTRADICTION_INPUTS,
  contradictionPollTarget,
  contradictionProblem,
  contradictionProblemText,
  createStartGate,
  describeFindingStatus,
  describeFlag,
  describeModelStatus,
  describeReason,
  describeRelation,
  pollStatus,
  progressText,
  reduceContradictions,
  reportCountRows,
  runIsActive,
  sendStart,
  startRequest,
  type ContradictionInputs,
} from "./contradictions";
import {
  OTHER_RUN_ID,
  RUN_ID,
  complete,
  recording,
  recordedStart,
  recordedStatus,
  report,
  run,
  running,
  startReply,
  statusReply,
} from "./contradictionsTestSupport";
import {
  CONTRADICTIONS_BOTH_QUOTES,
  CONTRADICTIONS_LABEL_CANDIDATE,
  CONTRADICTIONS_LABEL_VALUE_CONFLICT,
  CONTRADICTIONS_MODEL_VARIES,
  CONTRADICTIONS_NOT_KEPT,
} from "./fixedTexts";
import {
  CANDIDATE_REASONS,
  CONTRADICTION_FAILURES,
  FINDING_FLAGS,
  FINDING_STATUSES,
  MODEL_USES,
  RELATIONS,
  routeRequestSchema,
  type Invoke,
  type ProxyError,
} from "./ipc/proxy";
import {
  CONTRA_FAILED_NO_WORD_TEXT,
  CONTRA_NOT_AVAILABLE,
  CONTRA_PAIRS_COUNTING,
  CONTRA_PROBLEM_CAP,
  CONTRA_PROBLEM_SCOPE,
  CONTRA_UNKNOWN_RUN_TEXT,
} from "./ledgerText";
import {
  contradictionFailureMessage,
  describeContradictionError,
  describeContradictionFailure,
  describeNonReply,
  proxyErrorMessage,
} from "./messages";
import { pollTargetWhen } from "./poller";
import { errorOf, replyOf } from "./testSupport";

function inputs(over: Partial<ContradictionInputs> = {}): ContradictionInputs {
  return { ...INITIAL_CONTRADICTION_INPUTS, ...over };
}

/** The `request` argument of the one recorded host call. */
function sent(calls: { args: Record<string, unknown> }[]): Record<string, unknown> {
  expect(calls).toHaveLength(1);
  return calls[0]?.args.request as Record<string, unknown>;
}

describe("the start request (65i)", () => {
  it("leaves both values out when neither is set, never null and never empty", async () => {
    const { invoke, calls } = recording();
    const promise = sendStart(invoke, inputs());
    expect(promise).not.toBeNull();
    await promise;
    expect(sent(calls)).toEqual({ route: "contradictions_start" });
    expect("scope" in sent(calls)).toBe(false);
    expect("cap" in sent(calls)).toBe(false);
  });

  it("sends the scope alone, as typed", async () => {
    const { invoke, calls } = recording();
    await sendStart(invoke, inputs({ scope: "policies/" }));
    expect(sent(calls)).toEqual({ route: "contradictions_start", scope: "policies/" });
  });

  it("sends the cap alone, as a whole number", async () => {
    const { invoke, calls } = recording();
    await sendStart(invoke, inputs({ cap: "30" }));
    expect(sent(calls)).toEqual({ route: "contradictions_start", cap: 30 });
    expect(typeof sent(calls).cap).toBe("number");
  });

  it("sends both when both are set", async () => {
    const { invoke, calls } = recording();
    await sendStart(invoke, inputs({ scope: "policies", cap: "1" }));
    expect(sent(calls)).toEqual({ route: "contradictions_start", scope: "policies", cap: 1 });
  });

  it("builds the same request the host's schema accepts, and never a null", () => {
    for (const value of [inputs(), inputs({ scope: "a/b" }), inputs({ cap: "100" }), inputs({ scope: "a", cap: "7" })]) {
      const request = startRequest(value);
      expect(routeRequestSchema.safeParse(request).success).toBe(true);
      expect(Object.values(request)).not.toContain(null);
      expect(Object.values(request)).not.toContain("");
      expect(Object.values(request)).not.toContain(undefined);
    }
  });
});

describe("an input problem is refused before any call (65i)", () => {
  const badScopes: ReadonlyArray<readonly [string, string]> = [
    ["longer than 260 characters", "a".repeat(261)],
    ["a backslash", "policies\\hr"],
    ["a colon", "C:policies"],
    ["a NUL", `policies${String.fromCodePoint(0)}x`],
    ["a leading slash", "/policies"],
    ["a .. segment", "policies/../secrets"],
    ["only a .. segment", ".."],
  ];

  for (const [name, scope] of badScopes) {
    it(`refuses a scope with ${name}`, async () => {
      const { invoke, calls } = recording();
      expect(contradictionProblem(inputs({ scope }))).toBe("scope_invalid");
      expect(sendStart(invoke, inputs({ scope }))).toBeNull();
      expect(calls).toHaveLength(0);
    });
  }

  it("accepts a scope of exactly 260 characters and refuses one of 261", () => {
    expect(contradictionProblem(inputs({ scope: "a".repeat(260) }))).toBeNull();
    expect(contradictionProblem(inputs({ scope: "a".repeat(261) }))).toBe("scope_invalid");
  });

  it("counts characters, not UTF-16 units, as the host does", () => {
    expect(contradictionProblem(inputs({ scope: "\u{1F600}".repeat(260) }))).toBeNull();
    expect(contradictionProblem(inputs({ scope: "\u{1F600}".repeat(261) }))).toBe("scope_invalid");
  });

  for (const cap of ["0", "101", "-1", "1.5", "1e1", "abc", " 5", "5 ", "+5", "1000", "0x10"]) {
    it(`refuses the cap ${JSON.stringify(cap)}`, async () => {
      const { invoke, calls } = recording();
      expect(contradictionProblem(inputs({ cap }))).toBe("cap_invalid");
      expect(sendStart(invoke, inputs({ cap }))).toBeNull();
      expect(calls).toHaveLength(0);
    });
  }

  it("accepts the caps 1 and 100", () => {
    expect(contradictionProblem(inputs({ cap: "1" }))).toBeNull();
    expect(contradictionProblem(inputs({ cap: "100" }))).toBeNull();
  });

  it("names a scope problem before a cap problem, each with its own fixed words", () => {
    expect(contradictionProblem(inputs({ scope: "/x", cap: "0" }))).toBe("scope_invalid");
    expect(contradictionProblemText("scope_invalid")).toBe(CONTRA_PROBLEM_SCOPE);
    expect(contradictionProblemText("cap_invalid")).toBe(CONTRA_PROBLEM_CAP);
    expect(CONTRA_PROBLEM_SCOPE).not.toBe(CONTRA_PROBLEM_CAP);
  });

  it("does not start a run from the reducer while an input problem exists", () => {
    const bad = run([{ type: "input_changed", field: "cap", value: "0" }]);
    expect(reduceContradictions(bad, { type: "start_requested" })).toBe(bad);
  });
});

describe("the run id (65i)", () => {
  it("comes only from the start reply and is the one polled", async () => {
    const state = running(OTHER_RUN_ID);
    const target = contradictionPollTarget(state);
    expect(target).toBe(OTHER_RUN_ID);
    const { invoke, calls } = recording({ status: 200, body: recordedStatus("running").body });
    await pollStatus(invoke, target ?? "");
    expect(sent(calls)).toEqual({ route: "contradictions_status", run_id: OTHER_RUN_ID });
  });

  it("is never taken from a status body for another run", () => {
    const state = running(RUN_ID);
    const next = reduceContradictions(state, {
      type: "status_result",
      runId: RUN_ID,
      result: statusReply({ run_id: OTHER_RUN_ID, status: "complete", report: report() }),
    });
    expect(next).toBe(state);
  });

  it("there is no target before a start has been answered", () => {
    expect(contradictionPollTarget(INITIAL_CONTRADICTIONS_STATE)).toBeNull();
    expect(contradictionPollTarget(run([{ type: "start_requested" }]))).toBeNull();
  });
});

describe("a late answer (65i)", () => {
  it("for an older run is ignored", () => {
    const state = running(RUN_ID);
    const late = reduceContradictions(state, {
      type: "status_result",
      runId: OTHER_RUN_ID,
      result: statusReply({ run_id: OTHER_RUN_ID, status: "complete", report: report() }),
    });
    expect(late).toBe(state);
  });

  it("polled for another run id is ignored even when its body names the current run", () => {
    const state = running(RUN_ID);
    const late = reduceContradictions(state, {
      type: "status_result",
      runId: OTHER_RUN_ID,
      result: statusReply({ run_id: RUN_ID, status: "complete", report: report() }),
    });
    expect(late).toBe(state);
  });

  it("after the run ended changes nothing", () => {
    const done = complete();
    const late = reduceContradictions(done, {
      type: "status_result",
      runId: RUN_ID,
      result: statusReply({ status: "running", pairs_done: 1 }),
    });
    expect(late).toBe(done);
  });

  it("for a start nobody waits for changes nothing", () => {
    expect(reduceContradictions(INITIAL_CONTRADICTIONS_STATE, { type: "start_result", result: startReply() })).toBe(
      INITIAL_CONTRADICTIONS_STATE,
    );
    const active = running();
    expect(reduceContradictions(active, { type: "start_result", result: startReply(OTHER_RUN_ID) })).toBe(active);
  });

  it("a second Start while running is ignored, so the first run keeps its id", () => {
    const active = running(RUN_ID);
    expect(reduceContradictions(active, { type: "start_requested" })).toBe(active);
    expect(runIsActive(active.run)).toBe(true);
  });
});

describe("polling stops on a terminal status and not before (65i)", () => {
  it("keeps polling while the run is running, with or without a failed poll", () => {
    let state = running();
    expect(contradictionPollTarget(state)).toBe(RUN_ID);
    state = reduceContradictions(state, {
      type: "status_result",
      runId: RUN_ID,
      result: statusReply({ status: "running", pairs_total: 3, pairs_done: 1 }),
    });
    expect(contradictionPollTarget(state)).toBe(RUN_ID);
    state = reduceContradictions(state, {
      type: "status_result",
      runId: RUN_ID,
      result: { kind: "proxy_error", route: "contradictions_status", error: "timeout" } as never,
    });
    expect(contradictionPollTarget(state)).toBe(RUN_ID);
    expect(state.run.phase).toBe("running");
  });

  const terminal: ReadonlyArray<readonly [string, Record<string, unknown>]> = [
    ["complete", { status: "complete", pairs_done: 2, report: report() }],
    ["failed", { status: "failed", failure: "detector_error" }],
    ["cancelled", { status: "cancelled" }],
    ["complete without a report", { status: "complete", report: null }],
  ];
  for (const [name, over] of terminal) {
    it(`stops on ${name}`, () => {
      const state = run([{ type: "status_result", runId: RUN_ID, result: statusReply(over) }], running());
      expect(contradictionPollTarget(state)).toBeNull();
      expect(runIsActive(state.run)).toBe(false);
    });
  }

  it("stops on 404 run_not_found", () => {
    const state = run(
      [{ type: "status_result", runId: RUN_ID, result: errorOf("contradictions_status", 404, "run_not_found") }],
      running(),
    );
    expect(state.run.phase).toBe("unknown_run");
    expect(contradictionPollTarget(state)).toBeNull();
  });

  it("stops when the start is refused (409 gets no retry)", () => {
    const state = run(
      [{ type: "start_requested" }, { type: "start_result", result: errorOf("contradictions_start", 409, "run_in_progress") }],
    );
    expect(state.run.phase).toBe("refused");
    expect(contradictionPollTarget(state)).toBeNull();
  });
});

describe("polling resumes when health comes back while the run is still running (65i)", () => {
  it("is off while not healthy, and on again with the same id when healthy returns", () => {
    const state = running();
    const target = contradictionPollTarget(state);
    expect(pollTargetWhen(true, target)).toBe(RUN_ID);
    expect(pollTargetWhen(false, target)).toBeNull();
    expect(pollTargetWhen(true, target)).toBe(RUN_ID);
    // The state itself kept the run through the gap: nothing in it depends on health.
    expect(state.run.phase).toBe("running");
  });

  it("stays off after the run ended, whatever health does", () => {
    const done = complete();
    expect(pollTargetWhen(true, contradictionPollTarget(done))).toBeNull();
    expect(pollTargetWhen(false, contradictionPollTarget(done))).toBeNull();
  });
});

describe("progress (DESIGN.md 4.5)", () => {
  it("shows pairs done and pairs total as numbers", () => {
    expect(progressText(2, 55)).toBe("Pairs done: 2 of 55");
    expect(progressText(0, 3)).toBe("Pairs done: 0 of 3");
  });

  it("says the pairs are still being counted while the total is null, and never shows a total of 0", () => {
    const text = progressText(0, null);
    expect(text).toBe(CONTRA_PAIRS_COUNTING);
    expect(text).toBe("The pairs are still being counted.");
    expect(text).not.toMatch(/\d/);
    expect(progressText(4, null)).toBe(CONTRA_PAIRS_COUNTING);
  });

  it("a running run keeps a null total until the sidecar reports one", () => {
    let state = running();
    expect(state.run).toMatchObject({ phase: "running", pairsTotal: null, pairsDone: 0 });
    state = reduceContradictions(state, {
      type: "status_result",
      runId: RUN_ID,
      result: statusReply({ status: "running", pairs_total: null, pairs_done: 0 }),
    });
    expect(state.run).toMatchObject({ pairsTotal: null });
    state = reduceContradictions(state, {
      type: "status_result",
      runId: RUN_ID,
      result: statusReply({ status: "running", pairs_total: 55, pairs_done: 2 }),
    });
    expect(state.run).toMatchObject({ pairsTotal: 55, pairsDone: 2 });
  });

  it("has no estimate, no percent and never the word stuck", () => {
    for (const text of [progressText(1, 9), progressText(0, null), CONTRA_PAIRS_COUNTING]) {
      expect(text).not.toMatch(/stuck|eta|remaining|left|%|percent|second|minute/i);
    }
  });
});

describe("failures and refusals each have their own words (65i)", () => {
  it("each failure word has its own message, and none is blank", () => {
    const texts = CONTRADICTION_FAILURES.map((word) => contradictionFailureMessage(word));
    expect(new Set(texts).size).toBe(CONTRADICTION_FAILURES.length);
    for (const text of texts) {
      expect(text.length).toBeGreaterThan(10);
    }
    expect(contradictionFailureMessage("index_not_built")).toBe("The run failed because the vault has not been indexed yet.");
    expect(contradictionFailureMessage("detector_error")).toBe("The run failed because the detector hit an error.");
    expect(contradictionFailureMessage("internal_error")).toBe("The run failed because the sidecar hit an internal error.");
    expect(describeContradictionFailure("detector_error").code).toBe("detector_error");
  });

  it("a failed run with a null failure word shows its own fixed text, never a blank", () => {
    const own = contradictionFailureMessage(null);
    expect(own).toBe(CONTRA_FAILED_NO_WORD_TEXT);
    expect(own).toBe("The run failed and the sidecar gave no reason.");
    expect(CONTRADICTION_FAILURES.map((word) => contradictionFailureMessage(word))).not.toContain(own);
    const state = run([{ type: "status_result", runId: RUN_ID, result: statusReply({ status: "failed", failure: null }) }], running());
    expect(state.run.phase).toBe("failed");
    if (state.run.phase === "failed") {
      expect(state.run.failure.text).toBe(own);
      expect(state.run.failure.text.trim()).not.toBe("");
      expect(state.run.failure.code).toMatch(/^[a-z0-9_:]+$/);
    }
  });

  it("a failed run keeps its own word's text in the state", () => {
    for (const word of CONTRADICTION_FAILURES) {
      const state = run([{ type: "status_result", runId: RUN_ID, result: statusReply({ status: "failed", failure: word }) }], running());
      expect(state.run).toMatchObject({ phase: "failed", failure: { text: contradictionFailureMessage(word), code: word } });
    }
  });

  it("a complete status with a null report is its own phase, never 'no findings'", () => {
    const state = run([{ type: "status_result", runId: RUN_ID, result: statusReply({ status: "complete", report: null }) }], running());
    expect(state.run).toEqual({ phase: "report_missing" });
  });

  it("each typed start refusal has its own words", () => {
    const words = new Map<string, string>();
    for (const [status, code] of [
      [503, "vault_root_not_configured"],
      [409, "index_not_built"],
      [409, "run_in_progress"],
      [404, "run_not_found"],
    ] as const) {
      const failure = describeContradictionError({ error: code });
      words.set(code, failure.text);
      expect(failure.code, `${status}`).toBe(code);
      expect(failure.text.length).toBeGreaterThan(10);
    }
    expect(new Set(words.values()).size).toBe(4);
    expect(words.get("run_in_progress")).toContain("another job is running");
    expect(describeContradictionError({ error: "run_in_progress" }).category).toBe("busy");
    expect(words.get("run_not_found")).toBe(CONTRA_UNKNOWN_RUN_TEXT);
    expect(words.get("vault_root_not_configured")).toContain("No vault folder is in use");
    expect(words.get("index_not_built")).toContain("not been indexed");
  });

  it("a refused start is shown as refused, with the typed words, not as an empty result", () => {
    for (const [status, code] of [
      [503, "vault_root_not_configured"],
      [409, "index_not_built"],
      [409, "run_in_progress"],
    ] as const) {
      const state = run([{ type: "start_requested" }, { type: "start_result", result: errorOf("contradictions_start", status, code) }]);
      expect(state.run).toMatchObject({ phase: "refused", failure: { code } });
    }
  });

  it("every host error word, an invalid response and a reply over the size cap have their own words", () => {
    const proxy = ["not_ready", "unauthorized", "unreachable", "timeout", "too_large", "bad_request", "bad_response", "internal"] as const satisfies readonly ProxyError[];
    const texts = proxy.map((error) => proxyErrorMessage(error));
    expect(new Set(texts).size).toBe(proxy.length);
    const tooLarge = run([
      { type: "start_requested" },
      { type: "start_result", result: { kind: "proxy_error", route: "contradictions_start", error: "too_large" } as never },
    ]);
    expect(tooLarge.run).toMatchObject({ phase: "refused", failure: { code: "too_large", text: "The sidecar's answer was larger than ALIBI accepts." } });
    const invalid = run([
      { type: "start_requested" },
      { type: "start_result", result: { kind: "invalid_response" } as never },
    ]);
    expect(invalid.run).toMatchObject({ phase: "refused", failure: { code: "invalid_response" } });
    expect(describeNonReply({ kind: "invalid_response" } as never).text).toBe(
      "The answer did not match what ALIBI expects, so it was not used.",
    );
  });

  it("a poll that fails keeps the run going and records the problem, which clears on the next good answer", () => {
    let state = run(
      [
        {
          type: "status_result",
          runId: RUN_ID,
          result: { kind: "proxy_error", route: "contradictions_status", error: "too_large" } as never,
        },
      ],
      running(),
    );
    expect(state.run).toMatchObject({ phase: "running", problem: { code: "too_large" } });
    state = reduceContradictions(state, { type: "status_result", runId: RUN_ID, result: statusReply({ pairs_total: 2 }) });
    expect(state.run).toMatchObject({ phase: "running", problem: null, pairsTotal: 2 });
  });
});

describe("the state keeps a run only as long as the window does (65i)", () => {
  it("starting another run drops the previous result", () => {
    const done = complete();
    expect(done.run.phase).toBe("complete");
    const again = reduceContradictions(done, { type: "start_requested" });
    expect(again.run).toEqual({ phase: "starting" });
  });

  it("locks the inputs while a run is going", () => {
    const active = running();
    expect(reduceContradictions(active, { type: "input_changed", field: "scope", value: "x" })).toBe(active);
    const idle = reduceContradictions(INITIAL_CONTRADICTIONS_STATE, { type: "input_changed", field: "scope", value: "x" });
    expect(idle.inputs.scope).toBe("x");
  });

  it("the cancelled phase is its own state and stops the polling", () => {
    const state = run([{ type: "status_result", runId: RUN_ID, result: statusReply({ status: "cancelled" }) }], running());
    expect(state.run).toEqual({ phase: "cancelled" });
  });
});

describe("the words of a finding (DESIGN.md 11.1)", () => {
  it("VALUE_CONFLICT and CANDIDATE use the 11.1 labels", () => {
    expect(CONTRADICTIONS_LABEL_VALUE_CONFLICT).toBe("Numbers differ (the model kept this pair in this run)");
    expect(CONTRADICTIONS_LABEL_CANDIDATE).toBe("Numbers differ (not kept)");
    expect(describeFindingStatus("VALUE_CONFLICT")).toMatchObject({ kind: "known", label: CONTRADICTIONS_LABEL_VALUE_CONFLICT });
    expect(describeFindingStatus("CANDIDATE")).toMatchObject({ kind: "known", label: CONTRADICTIONS_LABEL_CANDIDATE });
  });

  it("no label says detected, confirmed or conflict found, and no label prints 'with the reason shown'", () => {
    for (const status of FINDING_STATUSES) {
      const description = describeFindingStatus(status);
      expect(description.kind).toBe("known");
      if (description.kind === "known") {
        expect(description.label).not.toMatch(/detected|confirmed|conflict found/i);
        expect(description.label).not.toContain("with the reason shown");
      }
    }
  });

  it("every status, reason, flag, relation and model status has its own label", () => {
    const labels = (values: readonly string[], describe: (value: string) => { kind: string; label?: string }) =>
      values.map((value) => {
        const description = describe(value);
        expect(description.kind, value).toBe("known");
        return description.label;
      });
    expect(new Set(labels(FINDING_STATUSES, describeFindingStatus)).size).toBe(FINDING_STATUSES.length);
    expect(new Set(labels(CANDIDATE_REASONS, describeReason)).size).toBe(CANDIDATE_REASONS.length);
    expect(new Set(labels(FINDING_FLAGS, describeFlag)).size).toBe(FINDING_FLAGS.length);
    expect(new Set(labels(RELATIONS, describeRelation)).size).toBe(RELATIONS.length);
    expect(new Set(labels(MODEL_USES, describeModelStatus)).size).toBe(MODEL_USES.length);
  });

  it("an unknown status, reason, flag, relation or model status is `unknown`, never a label", () => {
    for (const bad of ["detected", "confirmed", "", "VALUE_CONFLICT ", "value_conflict", "__proto__", "toString"]) {
      expect(describeFindingStatus(bad), bad).toEqual({ kind: "unknown" });
      expect(describeReason(bad), bad).toEqual({ kind: "unknown" });
      expect(describeFlag(bad), bad).toEqual({ kind: "unknown" });
      expect(describeRelation(bad), bad).toEqual({ kind: "unknown" });
      expect(describeModelStatus(bad), bad).toEqual({ kind: "unknown" });
    }
  });

  it("the three added fixed lines are the DESIGN.md 11.1 sentences", () => {
    expect(CONTRADICTIONS_MODEL_VARIES).toBe("The model's answer for the same pair can differ between identical runs.");
    expect(CONTRADICTIONS_NOT_KEPT).toBe("Findings are not kept. Closing ALIBI or starting another run drops them.");
    expect(CONTRADICTIONS_BOTH_QUOTES).toBe(
      "Both quotes are shown so you can decide. ALIBI does not decide which statement is right.",
    );
  });
});

describe("the report's counts (65i)", () => {
  it("are the integers the sidecar sent, in a fixed order, with no locale formatting", () => {
    const rows = reportCountRows(
      report({
        over_cap: 12345,
        no_verdict: 1,
        model_calls: 2,
        claims_found: 3,
        pairs_generated: 4,
        withheld_stale: 5,
        excluded_chunks: 6,
        chunks_read: 7890,
      }),
    );
    expect(rows).toHaveLength(9);
    expect(rows.map(([, value]) => value)).toEqual(["3", "12345", "1", "2", "3", "4", "5", "6", "7890"]);
    // No thousands separator of any kind: not a comma, not a no-break space, not a thin space.
    const joined = rows.map(([, value]) => value).join(" ");
    for (const separator of [",", String.fromCodePoint(0xa0), String.fromCodePoint(0x202f), String.fromCodePoint(0x2009)]) {
      expect(joined.includes(separator), `U+${separator.codePointAt(0)?.toString(16)}`).toBe(false);
    }
  });

  it("show 'not available', never 0, for a count that is missing or null", () => {
    const broken = { ...report(), over_cap: null, chunks_read: undefined } as never;
    const rows = reportCountRows(broken);
    const values = new Map(rows);
    expect(values.get("Left out by the cap")).toBe(CONTRA_NOT_AVAILABLE);
    expect(values.get("Chunks read")).toBe(CONTRA_NOT_AVAILABLE);
    expect(CONTRA_NOT_AVAILABLE).toBe("not available");
  });

  it("an empty findings list is a count of 0 findings, which is a real zero", () => {
    expect(new Map(reportCountRows(report({ findings: [] }))).get("Findings")).toBe("0");
  });
});

describe("only one start is in flight (65i fix)", () => {
  /** The sequence hooks/useContradictions.ts `start` runs, with `settle` standing for the reply. */
  async function attempt(gate: ReturnType<typeof createStartGate>, invoke: Invoke, value: ContradictionInputs) {
    if (!gate.tryBegin()) {
      return "gated";
    }
    const sent = sendStart(invoke, value);
    if (sent === null) {
      gate.end();
      return "problem";
    }
    await sent;
    gate.end();
    return "sent";
  }

  it("gives true once, false to a second tryBegin before end, and true again after end", () => {
    const gate = createStartGate();
    expect(gate.tryBegin()).toBe(true);
    expect(gate.tryBegin()).toBe(false);
    expect(gate.tryBegin()).toBe(false);
    gate.end();
    expect(gate.tryBegin()).toBe(true);
    expect(gate.tryBegin()).toBe(false);
  });

  it("end without a begin leaves the gate open, and each gate has its own flag", () => {
    const gate = createStartGate();
    gate.end();
    expect(gate.tryBegin()).toBe(true);
    expect(createStartGate().tryBegin()).toBe(true);
  });

  it("two start attempts in a row send exactly one start request", async () => {
    const gate = createStartGate();
    const { invoke, calls } = recording();
    const first = attempt(gate, invoke, inputs());
    const second = attempt(gate, invoke, inputs());
    expect(await second).toBe("gated");
    expect(await first).toBe("sent");
    expect(sent(calls)).toEqual({ route: "contradictions_start" });
  });

  it("a start can be made again once the first has settled", async () => {
    const gate = createStartGate();
    const { invoke, calls } = recording();
    expect(await attempt(gate, invoke, inputs())).toBe("sent");
    expect(await attempt(gate, invoke, inputs())).toBe("sent");
    expect(calls).toHaveLength(2);
  });

  it("an input problem sends nothing and does not leave the gate stuck", async () => {
    const gate = createStartGate();
    const { invoke, calls } = recording();
    expect(await attempt(gate, invoke, inputs({ cap: "0" }))).toBe("problem");
    expect(calls).toHaveLength(0);
    expect(await attempt(gate, invoke, inputs())).toBe("sent");
    expect(calls).toHaveLength(1);
  });
});

describe("the recorded start replies parse (65i)", () => {
  it("accepted gives a run id the reducer uses", () => {
    const accepted = recordedStart("accepted");
    expect(accepted.status).toBe(202);
    const state = run([{ type: "start_requested" }, { type: "start_result", result: replyOf("contradictions_start", 202, accepted.body) }]);
    expect(state.run).toMatchObject({ phase: "running", runId: (accepted.body as { run_id: string }).run_id });
  });
});
