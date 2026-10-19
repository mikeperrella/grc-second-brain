// The Import screen's pure state (chunk 65g; ADR-022 decisions 1, 4 and 7). The tests drive the SAME
// reducer, request builders and gate the hook (hooks/useImport.ts) and the screen use. Base bodies
// are the sidecar's recorded replies (fixtures/contracts/replies/*.json, synthetic data).

import { describe, expect, it } from "vitest";
import { IMPORT_REFUSAL_CODES, callRoute, routeRequestSchema, type Invoke } from "./ipc/proxy";
import {
  INITIAL_IMPORT_STATE,
  INITIAL_INPUTS,
  alreadyChanged,
  canImport,
  currentFields,
  fieldsKey,
  importFields,
  importTarget,
  inputProblem,
  inputProblemText,
  previewRequest,
  reduceImport,
  reportKind,
  runIdOf,
  type ImportEvent,
  type ImportInputs,
  type ImportState,
  type InputChange,
  type InputProblem,
} from "./ledgerImport";
import { draftBody, importReport, recordedImport } from "./ledgerTestSupport";
import { importRefusalMessage } from "./messages";
import { errorOf, replyOf } from "./testSupport";

const DRAFT = draftBody();
const FILE = DRAFT.file_name;
const RUN = DRAFT.run_id;
const CUSTOMER_ID = "00000001000000000000000000000000";

function run(events: ImportEvent[], from: ImportState = INITIAL_IMPORT_STATE): ImportState {
  return events.reduce(reduceImport, from);
}

/** The state after choosing FILE and reading its draft, then applying `changes`. */
function withDraft(changes: InputChange[] = []): ImportState {
  return run([
    { type: "file_selected", file: FILE },
    { type: "draft_requested" },
    { type: "draft_result", file: FILE, result: replyOf("draft_rows", 200, DRAFT) },
    ...changes.map((change): ImportEvent => ({ type: "input_changed", change })),
  ]);
}

const EXISTING: InputChange[] = [
  { kind: "existing_customer", customerId: CUSTOMER_ID },
  { kind: "sent_on", sentOn: "2026-10-10" },
];
const NEW_NAME: InputChange[] = [
  { kind: "new_customer_name", name: "Acme Test Co" },
  { kind: "confirm_new", confirmNew: true },
  { kind: "sent_on", sentOn: "2026-10-10" },
];

/** The state after a successful preview of the current inputs. */
function previewed(changes: InputChange[] = EXISTING, body = importReport("preview")): ImportState {
  const state = withDraft(changes);
  const fields = currentFields(state);
  if (fields === null) {
    throw new Error("inputs not valid");
  }
  const key = fieldsKey(fields);
  return run(
    [
      { type: "preview_requested", key },
      { type: "preview_result", key, fields, result: replyOf("ledger_import", 200, body) },
    ],
    state,
  );
}

function inputs(over: Partial<ImportInputs>): ImportInputs {
  return { ...INITIAL_INPUTS, file: FILE, ...over };
}

interface Call {
  command: string;
  args: Record<string, unknown>;
}

function recorder(): { invoke: Invoke; calls: Call[] } {
  const calls: Call[] = [];
  const invoke: Invoke = (command, args) => {
    calls.push({ command, args });
    return Promise.resolve({ status: 200, body: importReport("preview") });
  };
  return { invoke, calls };
}

function hasNull(value: unknown): boolean {
  return value === null || (typeof value === "object" && value !== null && Object.values(value).some(hasNull));
}

describe("the import inputs are checked before any call", () => {
  it("needs a draft read for the chosen file", () => {
    expect(inputProblem(inputs({ existingCustomerId: CUSTOMER_ID, sentOn: "2026-10-10" }), null)).toBe("no_draft");
    expect(inputProblem({ ...INITIAL_INPUTS, existingCustomerId: CUSTOMER_ID, sentOn: "2026-10-10" }, RUN)).toBe("no_draft");
    // A draft read for another file gives no run id for this one.
    const other = run([{ type: "file_selected", file: "other.xlsx" }], withDraft(EXISTING));
    expect(runIdOf(other)).toBeNull();
    expect(currentFields(other)).toBeNull();
  });

  it("needs exactly one of an existing customer and a new name", () => {
    expect(inputProblem(inputs({ sentOn: "2026-10-10" }), RUN)).toBe("customer_choice");
    expect(
      inputProblem(inputs({ existingCustomerId: CUSTOMER_ID, newCustomerName: "Acme", confirmNew: true, sentOn: "2026-10-10" }), RUN),
    ).toBe("customer_choice");
  });

  it("a new name has 1 to 1,000 code points, counted as Rust counts characters", () => {
    const ok = (name: string) => inputs({ newCustomerName: name, confirmNew: true, sentOn: "2026-10-10" });
    expect(inputProblem(ok("a".repeat(1_000)), RUN)).toBeNull();
    expect(inputProblem(ok("a".repeat(1_001)), RUN)).toBe("name_length");
    // 1,000 characters outside the BMP are 2,000 UTF-16 units and still 1,000 code points.
    expect(inputProblem(ok("\u{1F600}".repeat(1_000)), RUN)).toBeNull();
    expect(inputProblem(ok("\u{1F600}".repeat(1_001)), RUN)).toBe("name_length");
  });

  it("a new name needs confirm_new", () => {
    expect(inputProblem(inputs({ newCustomerName: "Acme", sentOn: "2026-10-10" }), RUN)).toBe("not_confirmed");
    expect(importFields(inputs({ newCustomerName: "Acme", sentOn: "2026-10-10" }), RUN)).toBeNull();
  });

  it("sent_on must be an ASCII calendar date YYYY-MM-DD (isCalendarDate)", () => {
    const at = (sentOn: string) => inputProblem(inputs({ existingCustomerId: CUSTOMER_ID, sentOn }), RUN);
    expect(at("")).toBe("no_sent_on");
    for (const bad of ["2026-1-05", "2026-02-30", "2025-02-29", "10/10/2026", "２０２６-01-01", "2026-10-10 "]) {
      expect(at(bad), bad).toBe("sent_on_invalid");
    }
    expect(at("2024-02-29")).toBeNull();
    expect(at("2026-10-10")).toBeNull();
  });

  it("every input problem has its own words", () => {
    const problems: InputProblem[] = ["no_draft", "customer_choice", "name_length", "not_confirmed", "no_sent_on", "sent_on_invalid"];
    const texts = problems.map(inputProblemText);
    expect(new Set(texts).size).toBe(problems.length);
    for (const text of texts) {
      expect(text.length).toBeGreaterThan(0);
    }
  });
});

describe("the import request fields", () => {
  it("an existing customer sends its id and leaves the name out, never null", () => {
    const fields = importFields(inputs({ existingCustomerId: CUSTOMER_ID, sentOn: "2026-10-10" }), RUN);
    expect(fields).toEqual({
      run_id: RUN,
      file: FILE,
      existing_customer_id: CUSTOMER_ID,
      confirm_new: false,
      sent_on: "2026-10-10",
    });
    expect(fields).not.toHaveProperty("new_customer_name");
    expect(hasNull(fields)).toBe(false);
  });

  it("a new customer sends the name and confirm_new, and leaves the id out, never null", () => {
    const fields = importFields(inputs({ newCustomerName: "Acme Test Co", confirmNew: true, sentOn: "2026-10-10" }), RUN);
    expect(fields).toEqual({
      run_id: RUN,
      file: FILE,
      new_customer_name: "Acme Test Co",
      confirm_new: true,
      sent_on: "2026-10-10",
    });
    expect(fields).not.toHaveProperty("existing_customer_id");
    expect(hasNull(fields)).toBe(false);
  });

  it("both customer fields set, or neither, builds no request", () => {
    expect(
      importFields(inputs({ existingCustomerId: CUSTOMER_ID, newCustomerName: "Acme", confirmNew: true, sentOn: "2026-10-10" }), RUN),
    ).toBeNull();
    expect(importFields(inputs({ sentOn: "2026-10-10" }), RUN)).toBeNull();
  });

  it("the run id is the full id from the draft_rows reply, never an 8-hex prefix", () => {
    const state = withDraft(EXISTING);
    expect(runIdOf(state)).toBe(RUN);
    expect(currentFields(state)?.run_id).toBe(RUN);
    expect(RUN).toMatch(/^[0-9a-f]{32}$/);
    expect(currentFields(state)?.run_id).not.toBe(RUN.slice(0, 8));
  });

  it("a preview request carries preview true, passes the request schema and has no null", () => {
    for (const changes of [EXISTING, NEW_NAME]) {
      const fields = currentFields(withDraft(changes));
      if (fields === null) {
        throw new Error("inputs not valid");
      }
      const request = previewRequest(fields);
      expect(request.preview).toBe(true);
      expect(request.route).toBe("ledger_import");
      expect(routeRequestSchema.safeParse(request).success).toBe(true);
      expect(hasNull(request)).toBe(false);
      expect("existing_customer_id" in request && "new_customer_name" in request).toBe(false);
    }
  });

  it("goes through callRoute with exactly those fields and preview true", async () => {
    const fields = currentFields(withDraft(NEW_NAME));
    if (fields === null) {
      throw new Error("inputs not valid");
    }
    const { invoke, calls } = recorder();
    await callRoute<"ledger_import">(invoke, previewRequest(fields));
    expect(calls).toEqual([
      {
        command: "sidecar_call",
        args: {
          request: {
            route: "ledger_import",
            run_id: RUN,
            file: FILE,
            new_customer_name: "Acme Test Co",
            confirm_new: true,
            sent_on: "2026-10-10",
            preview: true,
          },
        },
      },
    ]);
  });

  it("a null id would be refused unsent (the reason importFields leaves it out)", async () => {
    const { invoke, calls } = recorder();
    const bad = { ...previewRequest({ run_id: RUN, file: FILE, confirm_new: false, sent_on: "2026-10-10" }), existing_customer_id: null };
    // @ts-expect-error null is not a valid customer id
    const result = await callRoute<"ledger_import">(invoke, bad);
    expect(result).toEqual({ kind: "invalid_request" });
    expect(calls).toHaveLength(0);
  });
});

describe("the import needs a preview of identical inputs", () => {
  it("is off before any preview, and on after a successful one", () => {
    expect(canImport(withDraft(EXISTING))).toBe(false);
    expect(importTarget(withDraft(EXISTING))).toBeNull();
    const state = previewed();
    expect(canImport(state)).toBe(true);
    expect(importTarget(state)).toEqual({ fields: currentFields(state), previewCreated: 3 });
  });

  it("changing any input after a preview turns the import off again", () => {
    const changes: InputChange[] = [
      { kind: "existing_customer", customerId: "00000002000000000000000000000000" },
      { kind: "existing_customer", customerId: null },
      { kind: "new_customer_name", name: "Other" },
      { kind: "confirm_new", confirmNew: true },
      { kind: "sent_on", sentOn: "2026-10-11" },
    ];
    for (const change of changes) {
      const after = reduceImport(previewed(), { type: "input_changed", change });
      expect(canImport(after), change.kind).toBe(false);
      expect(importTarget(after), change.kind).toBeNull();
      expect(after.preview.phase, change.kind).toBe("none");
    }
    // A different file also forgets the preview.
    const otherFile = reduceImport(previewed(), { type: "file_selected", file: "other.xlsx" });
    expect(canImport(otherFile)).toBe(false);
  });

  it("a kept preview of other inputs never turns the import on (the key check, a second layer)", () => {
    // The reducer forgets the preview on every change (tested above). If a preview of other inputs
    // were ever still held, the key comparison alone must keep the import off.
    const kept = previewed();
    const variants: ImportState[] = [
      { ...kept, inputs: { ...kept.inputs, sentOn: "2026-10-11" } },
      { ...kept, inputs: { ...kept.inputs, existingCustomerId: "00000002000000000000000000000000" } },
      { ...kept, inputs: { ...kept.inputs, existingCustomerId: null, newCustomerName: "Acme", confirmNew: true } },
      { ...kept, inputs: { ...kept.inputs, file: "other.xlsx" } },
    ];
    for (const state of variants) {
      expect(state.preview.phase).toBe("previewed");
      expect(canImport(state)).toBe(false);
      expect(importTarget(state)).toBeNull();
    }
  });

  it("changing an input back does not bring the old preview back", () => {
    const changed = reduceImport(previewed(), { type: "input_changed", change: { kind: "sent_on", sentOn: "2026-10-11" } });
    const back = reduceImport(changed, { type: "input_changed", change: { kind: "sent_on", sentOn: "2026-10-10" } });
    expect(canImport(back)).toBe(false);
  });

  it("a late preview answer for other inputs changes nothing", () => {
    const state = withDraft(EXISTING);
    const fields = currentFields(state);
    if (fields === null) {
      throw new Error("inputs not valid");
    }
    const key = fieldsKey(fields);
    const asked = run([{ type: "preview_requested", key }], state);
    const moved = reduceImport(asked, { type: "input_changed", change: { kind: "sent_on", sentOn: "2026-10-11" } });
    const late = reduceImport(moved, {
      type: "preview_result",
      key,
      fields,
      result: replyOf("ledger_import", 200, importReport("preview")),
    });
    expect(late.preview.phase).toBe("none");
    expect(canImport(late)).toBe(false);
  });

  it("a failed preview never turns the import on", () => {
    const state = withDraft(EXISTING);
    const fields = currentFields(state);
    if (fields === null) {
      throw new Error("inputs not valid");
    }
    const key = fieldsKey(fields);
    const failed = run(
      [
        { type: "preview_requested", key },
        { type: "preview_result", key, fields, result: errorOf("ledger_import", 422, "import_refused", { code: "sent_on_in_future" }) },
      ],
      state,
    );
    expect(failed.preview.phase).toBe("failed");
    expect(canImport(failed)).toBe(false);
  });

  it("is off while an import is being sent", () => {
    const sending = reduceImport(previewed(), { type: "import_started" });
    expect(canImport(sending)).toBe(false);
    // Inputs cannot change while it is sent.
    const tried = reduceImport(sending, { type: "input_changed", change: { kind: "sent_on", sentOn: "2026-10-11" } });
    expect(tried.inputs.sentOn).toBe("2026-10-10");
  });
});

describe("a preview is never an import result, and the other way round", () => {
  it("files each reply by its own preview flag", () => {
    expect(reportKind(importReport("preview"))).toBe("preview");
    expect(reportKind(importReport("created"))).toBe("import");
  });

  it("a preview reply is kept as the preview and never as the outcome", () => {
    const state = previewed();
    expect(state.preview.phase).toBe("previewed");
    expect(state.outcome.phase).toBe("none");
  });

  it("a preview call answered with preview false is not kept as a preview", () => {
    const state = previewed(EXISTING, importReport("created"));
    expect(state.preview.phase).toBe("failed");
    expect(canImport(state)).toBe(false);
    if (state.preview.phase === "failed") {
      expect(state.preview.failure.code).toBe("invalid_response");
    }
  });

  it("the import answered with preview true is not shown as an import result", () => {
    const state = run(
      [{ type: "import_started" }, { type: "import_result", result: replyOf("ledger_import", 200, importReport("preview")) }],
      previewed(),
    );
    expect(state.outcome.phase).toBe("failed");
    if (state.outcome.phase === "failed") {
      expect(state.outcome.failure.code).toBe("invalid_response");
    }
  });

  it("a real import result is the outcome, and the old preview is dropped", () => {
    const state = run(
      [{ type: "import_started" }, { type: "import_result", result: replyOf("ledger_import", 200, importReport("created")) }],
      previewed(),
    );
    expect(state.outcome.phase).toBe("imported");
    expect(state.preview.phase).toBe("none");
    expect(canImport(state)).toBe(false);
  });

  it("an import result nobody is waiting for changes nothing", () => {
    const state = reduceImport(previewed(), {
      type: "import_result",
      result: replyOf("ledger_import", 200, importReport("created")),
    });
    expect(state.outcome.phase).toBe("none");
  });
});

describe("already_changed_since_draft", () => {
  it("null stays null (not available), never 0; an integer is kept exactly", () => {
    expect(alreadyChanged(importReport("preview", { already_changed_since_draft: null }))).toBeNull();
    expect(alreadyChanged(importReport("preview", { already_changed_since_draft: 0 }))).toBe(0);
    expect(alreadyChanged(importReport("preview", { already_changed_since_draft: 7 }))).toBe(7);
  });
});

describe("refusals of the import and of its reads", () => {
  function previewWith(result: Parameters<typeof replyOf>[2] | ReturnType<typeof errorOf>): ImportState {
    const state = withDraft(EXISTING);
    const fields = currentFields(state);
    if (fields === null) {
      throw new Error("inputs not valid");
    }
    const key = fieldsKey(fields);
    return run(
      [
        { type: "preview_requested", key },
        { type: "preview_result", key, fields, result: result as ReturnType<typeof errorOf<"ledger_import">> },
      ],
      state,
    );
  }

  it("each import_refused code has its own words and code", () => {
    const texts = new Set<string>();
    for (const code of IMPORT_REFUSAL_CODES) {
      const state = previewWith(errorOf("ledger_import", 422, "import_refused", { code }));
      expect(state.preview.phase, code).toBe("failed");
      if (state.preview.phase === "failed") {
        expect(state.preview.failure.code).toBe(`import_refused:${code}`);
        expect(state.preview.failure.text).toBe(importRefusalMessage(code));
      }
      texts.add(importRefusalMessage(code));
    }
    expect(texts.size).toBe(IMPORT_REFUSAL_CODES.length);
  });

  it("question_mismatch keeps integer counts only", () => {
    const recordedMismatch = recordedImport("question_mismatch");
    const state = previewWith(errorOf("ledger_import", 422, "import_refused", {
      code: "question_mismatch",
      counts: { ...(recordedMismatch.body as { counts: Record<string, number> }).counts, "bad key!": 2, half: 1.5 },
    }));
    expect(state.preview.phase).toBe("failed");
    if (state.preview.phase === "failed") {
      expect(state.preview.counts).toEqual([
        ["mismatched_rows", 1],
        ["workbook_rows", 4],
        ["recorded_rows", 4],
      ]);
    }
  });

  it("customer_exists keeps the id from the refusal and nothing else", () => {
    const state = previewWith(errorOf("ledger_import", 422, "ledger_refused", { code: "customer_exists", customer_id: CUSTOMER_ID }));
    expect(state.preview.phase).toBe("failed");
    if (state.preview.phase === "failed") {
      expect(state.preview.existingCustomerId).toBe(CUSTOMER_ID);
      expect(state.preview.failure.code).toBe("ledger_refused:customer_exists");
    }
  });

  it("the other refusals each keep their own code", () => {
    const cases: [ReturnType<typeof errorOf<"ledger_import">>, string, string][] = [
      [errorOf("ledger_import", 503, "ledger_root_not_configured"), "ledger_root_not_configured", "error"],
      [errorOf("ledger_import", 503, "export_root_not_configured"), "export_root_not_configured", "error"],
      [errorOf("ledger_import", 422, "workbook_refused", { code: "doctype" }), "workbook_refused:doctype", "error"],
      [errorOf("ledger_import", 404, "file_not_found"), "file_not_found", "error"],
      [errorOf("ledger_import", 400, "path_not_allowed"), "path_not_allowed", "error"],
      [errorOf("ledger_import", 409, "run_in_progress"), "run_in_progress", "busy"],
      [errorOf("ledger_import", 422, "ledger_refused", { code: "customer_unknown" }), "ledger_refused:customer_unknown", "error"],
      [errorOf("ledger_import", 422, "ledger_refused", { code: "customer_name_invalid" }), "ledger_refused:customer_name_invalid", "error"],
    ];
    for (const [result, code, category] of cases) {
      const state = previewWith(result);
      expect(state.preview.phase, code).toBe("failed");
      if (state.preview.phase === "failed") {
        expect(state.preview.failure.code, code).toBe(code);
        expect(state.preview.failure.category, code).toBe(category);
        expect(state.preview.existingCustomerId, code).toBeNull();
      }
    }
  });

  it("a proxy error and an invalid answer are failures with their own words", () => {
    for (const result of [{ kind: "proxy_error", error: "timeout" }, { kind: "invalid_response" }] as const) {
      const state = previewWith(result as unknown as ReturnType<typeof errorOf<"ledger_import">>);
      expect(state.preview.phase).toBe("failed");
    }
  });

  it("no exports folder in use is a refusal, never an empty list", () => {
    const state = run([{ type: "files_requested" }, { type: "files_result", call: { kind: "folder_error", error: "not_configured" } }]);
    expect(state.files.phase).toBe("failed");
    if (state.files.phase === "failed") {
      expect(state.files.failure.code).toBe("export_root_not_configured");
    }
  });

  it("an empty folder is a listed empty folder", () => {
    const state = run([{ type: "files_result", call: { kind: "ok", value: { kind: "exports", files: [], truncated: false } } }]);
    expect(state.files).toEqual({ phase: "listed", files: [], truncated: false });
  });

  it("a chosen file gone from a new listing is forgotten with its draft and preview", () => {
    const state = reduceImport(previewed(), {
      type: "files_result",
      call: { kind: "ok", value: { kind: "exports", files: ["other.xlsx"], truncated: false } },
    });
    expect(state.inputs.file).toBeNull();
    expect(state.draft.phase).toBe("none");
    expect(canImport(state)).toBe(false);
  });

  it("a draft refusal keeps its code and integer counts, and gives no run id", () => {
    for (const code of ["run_record_missing", "headers_changed", "question_mismatch"] as const) {
      const state = run([
        { type: "file_selected", file: FILE },
        { type: "draft_requested" },
        {
          type: "draft_result",
          file: FILE,
          result: errorOf("draft_rows", 422, "draft_refused", code === "question_mismatch" ? { code, counts: { mismatched_rows: 2 } } : { code }),
        },
      ]);
      expect(state.draft.phase, code).toBe("failed");
      expect(runIdOf(state)).toBeNull();
      if (state.draft.phase === "failed") {
        expect(state.draft.failure.code).toBe(`draft_refused:${code}`);
        expect(state.draft.counts).toEqual(code === "question_mismatch" ? [["mismatched_rows", 2]] : []);
      }
    }
  });

  it("a late draft answer for another file changes nothing", () => {
    const state = run([
      { type: "file_selected", file: FILE },
      { type: "draft_requested" },
      { type: "file_selected", file: "other.xlsx" },
      { type: "draft_result", file: FILE, result: replyOf("draft_rows", 200, DRAFT) },
    ]);
    expect(runIdOf(state)).toBeNull();
  });
});
