// The Blast radius screen's pure logic (chunk 65h; ADR-018, ADR-022 decisions 6 and 8, threat model
// F32-2; docs/DESIGN.md 11.3). The preview is driven through `sendPreview`, the one function the
// hook calls, with a recording fake `invoke`: what reaches it is exactly what the host would get.

import { describe, expect, it } from "vitest";
import {
  INITIAL_BLAST_STATE,
  INITIAL_INPUTS,
  blastEventOnScreen,
  blastFailureOf,
  blastProblemText,
  blastRequest,
  chosenIndex,
  currentFields,
  detailCode,
  inputProblem,
  newFlags,
  pathAt,
  reduceBlast,
  sendPreview,
  textSize,
  type BlastEvent,
  type BlastInputs,
  type BlastState,
} from "./blast";
import { blastReport, citedPaths, recordedBlast, recordedCited } from "./blastTestSupport";
import { NAV_ENTRIES } from "./components/Shell";
import { MAX_PROPOSED_TEXT_BYTES, RECALL_FLAGS, routeRequestSchema, type Invoke } from "./ipc/proxy";
import { errorOf, replyOf } from "./testSupport";

const CUSTOMER = "00000001000000000000000000000000";
const PATH = "policies/encryption-policy.md";

/** A fake invoke that records every call and answers with `answer`. */
function recording(answer: unknown = { status: 200, body: recordedBlast("proposed_text").body }) {
  const calls: { command: string; args: Record<string, unknown> }[] = [];
  const invoke: Invoke = (command, args) => {
    calls.push({ command, args });
    return Promise.resolve(answer);
  };
  return { invoke, calls };
}

function inputs(over: Partial<BlastInputs> = {}): BlastInputs {
  return { ...INITIAL_INPUTS, path: PATH, mode: "text", proposedText: "new text", ...over };
}

function run(events: BlastEvent[], from: BlastState = INITIAL_BLAST_STATE): BlastState {
  return events.reduce(reduceBlast, from);
}

function listed(paths = citedPaths("two_paths")): BlastState {
  return run([{ type: "paths_requested" }, { type: "paths_result", result: replyOf("ledger_cited_paths", 200, { paths }) }]);
}

describe("the preview request carries exactly one of proposed_text and delete_file", () => {
  it("text mode sends the text and delete_file false, and passes the host's request schema", () => {
    const request = blastRequest({ path: PATH, change: { kind: "text", text: "abc" }, customerId: null });
    expect(request).toEqual({ route: "ledger_blast_radius", path: PATH, proposed_text: "abc", delete_file: false });
    expect(routeRequestSchema.safeParse(request).success).toBe(true);
  });

  it("delete mode sends delete_file true and no proposed_text key at all", () => {
    const request = blastRequest({ path: PATH, change: { kind: "delete" }, customerId: null });
    expect(request).toEqual({ route: "ledger_blast_radius", path: PATH, delete_file: true });
    expect("proposed_text" in request).toBe(false);
    expect(routeRequestSchema.safeParse(request).success).toBe(true);
  });

  it("delete mode leaves a text typed earlier out of the request", () => {
    const fields = currentFields(inputs({ mode: "delete", proposedText: "kept in the box" }));
    expect(fields).not.toBeNull();
    const request = blastRequest(fields ?? { path: "", change: { kind: "delete" }, customerId: null });
    expect(JSON.stringify(request)).not.toContain("kept in the box");
    expect(request.delete_file).toBe(true);
  });

  it("sends exactly one of the two through sendPreview, for each mode", async () => {
    for (const mode of ["text", "delete"] as const) {
      const { invoke, calls } = recording();
      const sent = sendPreview(invoke, inputs({ mode }));
      expect(sent).not.toBeNull();
      await sent?.reply;
      expect(calls).toHaveLength(1);
      const request = calls[0]?.args.request as Record<string, unknown>;
      expect(calls[0]?.command).toBe("sidecar_call");
      const hasText = "proposed_text" in request;
      const deletes = request.delete_file === true;
      expect(hasText !== deletes, mode).toBe(true);
      expect(typeof request.delete_file, mode).toBe("boolean");
    }
  });
});

describe("the proposed text is limited in UTF-8 bytes, before any call", () => {
  it("2,097,152 bytes is accepted and 2,097,153 bytes is refused", () => {
    expect(MAX_PROPOSED_TEXT_BYTES).toBe(2_097_152);
    expect(textSize("a".repeat(2_097_152))).toEqual({ bytes: 2_097_152, over: false });
    expect(textSize("a".repeat(2_097_153))).toEqual({ bytes: 2_097_153, over: true });
    expect(inputProblem(inputs({ proposedText: "a".repeat(2_097_152) }))).toBeNull();
    expect(inputProblem(inputs({ proposedText: "a".repeat(2_097_153) }))).toBe("text_too_large");
  });

  it("counts bytes, not characters: two-byte and four-byte characters", () => {
    const twoByte = String.fromCodePoint(0xe9);
    const fourByte = String.fromCodePoint(0x1f600);
    // 1,048,576 characters of two bytes each is exactly the limit.
    expect(textSize(twoByte.repeat(1_048_576))).toEqual({ bytes: 2_097_152, over: false });
    // 1,048,577 characters is far under 2,097,152 characters but one character over in bytes.
    const overByBytes = twoByte.repeat(1_048_577);
    expect(Array.from(overByBytes)).toHaveLength(1_048_577);
    expect(textSize(overByBytes)).toEqual({ bytes: 2_097_154, over: true });
    expect(inputProblem(inputs({ proposedText: overByBytes }))).toBe("text_too_large");
    // 524,288 four-byte characters is the limit; one ASCII byte more is over.
    expect(textSize(fourByte.repeat(524_288))).toEqual({ bytes: 2_097_152, over: false });
    expect(textSize(`${fourByte.repeat(524_288)}a`).over).toBe(true);
  });

  it("a text of 2,097,153 bytes is refused before any call: invoke is never called", () => {
    for (const text of ["a".repeat(2_097_153), String.fromCodePoint(0xe9).repeat(1_048_577)]) {
      const { invoke, calls } = recording();
      expect(sendPreview(invoke, inputs({ proposedText: text }))).toBeNull();
      expect(calls).toHaveLength(0);
    }
  });

  it("a text of exactly 2,097,152 bytes is sent, once, as typed", async () => {
    const text = `${String.fromCodePoint(0xe9).repeat(1_048_575)}ab`;
    expect(textSize(text).bytes).toBe(2_097_152);
    const { invoke, calls } = recording();
    await sendPreview(invoke, inputs({ proposedText: text }))?.reply;
    expect(calls).toHaveLength(1);
    expect((calls[0]?.args.request as { proposed_text?: string }).proposed_text).toBe(text);
  });

  it("the delete mode is never stopped by a large text left in the box", () => {
    expect(inputProblem(inputs({ mode: "delete", proposedText: "a".repeat(2_097_153) }))).toBeNull();
  });

  it("the other input problems stop the call too, each with its own words", async () => {
    expect(inputProblem(inputs({ path: null }))).toBe("no_path");
    expect(inputProblem(inputs({ mode: null }))).toBe("no_mode");
    for (const over of [{ path: null }, { mode: null }] as const) {
      const { invoke, calls } = recording();
      expect(sendPreview(invoke, inputs(over))).toBeNull();
      expect(calls).toHaveLength(0);
    }
    const words = (["no_path", "no_mode", "text_too_large"] as const).map(blastProblemText);
    expect(words).toEqual([
      "Choose a cited file first.",
      "Choose new text or deleting the file.",
      "The proposed text is over the limit. Nothing was sent.",
    ]);
  });
});

describe("the path and the customer", () => {
  it("the path sent is exactly the listed one, chosen by its position", async () => {
    const odd = `policies/${String.fromCodePoint(0x202e)}x y.md`;
    const state = listed([
      { source_path: PATH, promise_count: 2 },
      { source_path: odd, promise_count: 1 },
    ]);
    const chosen = run(
      [
        { type: "input_changed", change: { kind: "path", index: 1 } },
        { type: "input_changed", change: { kind: "mode", mode: "delete" } },
      ],
      state,
    );
    expect(chosen.inputs.path).toBe(odd);
    expect(chosenIndex(chosen)).toBe(1);
    const { invoke, calls } = recording();
    await sendPreview(invoke, chosen.inputs)?.reply;
    expect((calls[0]?.args.request as { path: string }).path).toBe(odd);
  });

  it("an index outside the list, or none, chooses no path", () => {
    const state = listed();
    expect(pathAt(state.paths, 5)).toBeNull();
    expect(pathAt(state.paths, null)).toBeNull();
    expect(pathAt(INITIAL_BLAST_STATE.paths, 0)).toBeNull();
    const after = run([{ type: "input_changed", change: { kind: "path", index: 9 } }], state);
    expect(after.inputs.path).toBeNull();
  });

  it("a chosen path that is no longer listed after a refresh is dropped", () => {
    const chosen = run([{ type: "input_changed", change: { kind: "path", index: 0 } }], listed());
    expect(chosen.inputs.path).toBe("policies/backup-policy.md");
    const refreshed = run(
      [{ type: "paths_result", result: replyOf("ledger_cited_paths", 200, recordedCited("one_path").body) }],
      chosen,
    );
    expect(refreshed.inputs.path).toBeNull();
  });

  it("no customer leaves customer_id out, never null; a chosen one is sent as it is", async () => {
    const none = recording();
    await sendPreview(none.invoke, inputs({ customerId: null }))?.reply;
    const sentNone = none.calls[0]?.args.request as Record<string, unknown>;
    expect("customer_id" in sentNone).toBe(false);
    expect(JSON.stringify(sentNone)).not.toContain("null");

    const one = recording();
    await sendPreview(one.invoke, inputs({ customerId: CUSTOMER, mode: "delete" }))?.reply;
    const sentOne = one.calls[0]?.args.request as Record<string, unknown>;
    expect(sentOne.customer_id).toBe(CUSTOMER);
    expect(JSON.stringify(sentOne)).not.toContain("null");
  });
});

describe("new flags (DESIGN.md 11.3)", () => {
  it("a flag only in the proposed set is new", () => {
    expect(newFlags(["review_overdue"], [])).toEqual(["review_overdue"]);
    expect(newFlags(["document_changed_elsewhere", "review_overdue"], ["review_overdue"])).toEqual([
      "document_changed_elsewhere",
    ]);
  });

  it("a flag in both sets is not new", () => {
    expect(newFlags(["review_overdue"], ["review_overdue"])).toEqual([]);
    expect(newFlags([...RECALL_FLAGS], [...RECALL_FLAGS])).toEqual([]);
  });

  it("an empty proposed set gives none, whatever the file has now", () => {
    expect(newFlags([], [])).toEqual([]);
    expect(newFlags([], [...RECALL_FLAGS])).toEqual([]);
  });

  it("an unknown flag is never new: it shows the error block instead", () => {
    expect(newFlags(["made_up_flag"], [])).toEqual([]);
    expect(newFlags(["made_up_flag", "review_overdue"], [])).toEqual(["review_overdue"]);
  });

  it("a repeated flag is listed once", () => {
    expect(newFlags(["review_overdue", "review_overdue"], [])).toEqual(["review_overdue"]);
  });
});

describe("the states", () => {
  it("a ledger that is not configured is a refusal, never an empty list and never 0", () => {
    const state = run([
      { type: "paths_requested" },
      { type: "paths_result", result: errorOf("ledger_cited_paths", 503, "ledger_root_not_configured") },
    ]);
    expect(state.paths.phase).toBe("failed");
    if (state.paths.phase === "failed") {
      expect(state.paths.failure.code).toBe("ledger_root_not_configured");
      expect(state.paths.failure.text).toContain("No ledger folder is in use.");
    }
    expect("paths" in state.paths).toBe(false);
  });

  it("an empty list is listed with no paths (its own message on screen)", () => {
    const state = listed(citedPaths("empty"));
    expect(state.paths).toEqual({ phase: "listed", paths: [] });
  });

  it("too_many_paths keeps its integer counts", () => {
    const state = run([
      { type: "paths_requested" },
      { type: "paths_result", result: replyOf("ledger_cited_paths", 422, recordedCited("too_many_paths").body) },
    ]);
    expect(state.paths).toMatchObject({ phase: "failed", counts: [["paths", 2], ["cap", 1]] });
  });

  it("a preview answer is kept only for the inputs it was asked for", () => {
    const ready = run(
      [
        { type: "input_changed", change: { kind: "path", index: 1 } },
        { type: "input_changed", change: { kind: "mode", mode: "delete" } },
      ],
      listed(),
    );
    const fields = currentFields(ready.inputs);
    if (fields === null) {
      throw new Error("expected fields");
    }
    const asked = run([{ type: "preview_requested", generation: ready.generation }], ready);
    expect(asked.preview.phase).toBe("loading");
    const result = replyOf("ledger_blast_radius", 200, recordedBlast("delete_file").body);
    const done = run([{ type: "preview_result", generation: ready.generation, fields, result }], asked);
    expect(done.preview).toMatchObject({ phase: "previewed", path: "policies/encryption-policy.md", mode: "delete" });

    // An input change while waiting: the late answer is dropped and nothing is shown.
    const changed = run([{ type: "input_changed", change: { kind: "text", text: "x" } }], asked);
    const late = run([{ type: "preview_result", generation: ready.generation, fields, result }], changed);
    expect(late.preview.phase).toBe("none");

    // Any change after a result drops the result.
    const after = run([{ type: "input_changed", change: { kind: "customer", customerId: CUSTOMER } }], done);
    expect(after.preview.phase).toBe("none");
  });

  it("each blast_radius_refused code and the configuration words are failures with their code", () => {
    const cases: [string, ReturnType<typeof errorOf>][] = [
      ["blast_radius_refused:too_many_promises", errorOf("ledger_blast_radius", 422, "blast_radius_refused", { code: "too_many_promises", counts: { promises: 2001, cap: 2000 } })],
      ["blast_radius_refused:proposed_text_too_large", errorOf("ledger_blast_radius", 422, "blast_radius_refused", { code: "proposed_text_too_large", counts: { bytes: 3, cap: 2 } })],
      ["blast_radius_refused:proposed_text_has_nul", errorOf("ledger_blast_radius", 422, "blast_radius_refused", { code: "proposed_text_has_nul" })],
      ["blast_radius_refused:proposed_text_not_utf8", errorOf("ledger_blast_radius", 422, "blast_radius_refused", { code: "proposed_text_not_utf8" })],
      ["vault_root_not_configured", errorOf("ledger_blast_radius", 503, "vault_root_not_configured")],
      ["ledger_root_not_configured", errorOf("ledger_blast_radius", 503, "ledger_root_not_configured")],
      ["run_in_progress", errorOf("ledger_blast_radius", 409, "run_in_progress")],
    ];
    for (const [code, result] of cases) {
      expect(blastFailureOf(result as never)?.code, code).toBe(code);
    }
    expect(blastFailureOf({ kind: "proxy_error", error: "too_large" })?.code).toBe("too_large");
    expect(blastFailureOf({ kind: "invalid_response" })?.code).toBe("invalid_response");
    expect(blastFailureOf(replyOf("ledger_blast_radius", 200, blastReport([])))).toBeNull();
  });

  it("a proposed detail outside the closed set is not a code", () => {
    expect(detailCode("not_found")).toBe("not_found");
    expect(detailCode("all_good")).toBeNull();
    expect(detailCode("")).toBeNull();
  });
});

describe("leaving the screen clears the state (65h fix; DESIGN.md 11.1)", () => {
  /** A state holding a path, a mode, a pasted text, a customer and a shown preview. */
  function holding(): BlastState {
    const ready = run(
      [
        { type: "input_changed", change: { kind: "path", index: 1 } },
        { type: "input_changed", change: { kind: "mode", mode: "text" } },
        { type: "input_changed", change: { kind: "text", text: "pasted text" } },
        { type: "input_changed", change: { kind: "customer", customerId: CUSTOMER } },
      ],
      listed(),
    );
    const fields = currentFields(ready.inputs);
    if (fields === null) {
      throw new Error("expected fields");
    }
    const asked = run([{ type: "preview_requested", generation: ready.generation }], ready);
    const result = replyOf("ledger_blast_radius", 200, recordedBlast("proposed_text").body);
    return run([{ type: "preview_result", generation: ready.generation, fields, result }], asked);
  }

  it("reset clears the proposed text, path, mode, customer and preview", () => {
    const before = holding();
    expect(before.inputs.proposedText).toBe("pasted text");
    expect(before.preview.phase).toBe("previewed");
    const after = run([{ type: "reset" }], before);
    expect(after.inputs).toEqual(INITIAL_INPUTS);
    expect(after.inputs.proposedText).toBe("");
    expect(after.inputs.path).toBeNull();
    expect(after.inputs.mode).toBeNull();
    expect(after.inputs.customerId).toBeNull();
    expect(after.paths).toEqual({ phase: "idle" });
    expect(after.preview).toEqual({ phase: "none" });
    expect(after.generation).toBe(before.generation + 1);
  });

  it("an answer for the old generation arriving after a reset changes nothing", () => {
    const ready = run(
      [
        { type: "input_changed", change: { kind: "path", index: 0 } },
        { type: "input_changed", change: { kind: "mode", mode: "delete" } },
      ],
      listed(),
    );
    const fields = currentFields(ready.inputs);
    if (fields === null) {
      throw new Error("expected fields");
    }
    const asked = run([{ type: "preview_requested", generation: ready.generation }], ready);
    const cleared = run([{ type: "reset" }], asked);
    const result = replyOf("ledger_blast_radius", 200, recordedBlast("delete_file").body);
    const late = reduceBlast(cleared, { type: "preview_result", generation: ready.generation, fields, result });
    expect(late).toBe(cleared);
    expect(late.preview.phase).toBe("none");
  });

  it("reset on a clean state returns the same object", () => {
    expect(reduceBlast(INITIAL_BLAST_STATE, { type: "reset" })).toBe(INITIAL_BLAST_STATE);
    const once = run([{ type: "reset" }], holding());
    expect(reduceBlast(once, { type: "reset" })).toBe(once);
  });

  it("blastEventOnScreen is null on the blast screen and a reset on every other screen", () => {
    expect(blastEventOnScreen("blast")).toBeNull();
    const others = NAV_ENTRIES.map((entry) => entry.screen).filter((screen) => screen !== "blast");
    expect(others.length).toBe(NAV_ENTRIES.length - 1);
    for (const screen of others) {
      expect(blastEventOnScreen(screen), screen).toEqual({ type: "reset" });
    }
  });
});
