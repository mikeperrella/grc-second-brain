// The questionnaire flow's states (M6_PLAN C.4, DESIGN.md 4.5 and 7). No React.

import { describe, expect, it } from "vitest";
import { mappingProblems, mappingRequest } from "./mapping";
import {
  INITIAL_QUESTIONNAIRE_STATE,
  isWritingFile,
  orderedCounts,
  reduceQuestionnaire,
  runIsActive,
  runPollTarget,
  runRecordMissing,
  secondsSinceLastAnswer,
  type QuestionnaireEvent,
  type QuestionnaireState,
  type RunSummary,
} from "./questionnaireRun";
import { errorOf, replyOf } from "./testSupport";

const RUN = "00ff00ff00ff";

const inspectBody = (over: object = {}) => ({
  sheets: [
    { name: "Sheet1", rows: 40, columns: 5 },
    { name: "Other", rows: 3, columns: 2 },
  ],
  suggestion: { sheet: "Sheet1", header_row: 2, question_col: 3, id_col: 1 },
  macro_enabled: false,
  parts: { "xl/workbook.xml": 1 },
  warnings: [],
  ...over,
});

const summaryBody = (over: object = {}) => ({
  model: "llama3.1:8b",
  digest_before: null,
  digest_after: null,
  template_sha256: "t",
  input_sha256: "i",
  complete: true,
  counts: { SUPPORTED: 2, PARTIAL: 1, NEEDS_REVIEW: 3 },
  reason_counts: { review_overdue: 1 },
  file_name: "q-UNREVIEWED-DRAFT-00ff00ff.xlsx",
  rows_written: 6,
  dropped_parts: [],
  warnings: [],
  seconds: 12.34,
  ...over,
});

const statusBody = (over: object = {}) => ({
  run_id: RUN,
  status: "running",
  total: 6,
  completed: 0,
  failure: null,
  summary: null,
  ...over,
});

function run(events: QuestionnaireEvent[], from: QuestionnaireState = INITIAL_QUESTIONNAIRE_STATE) {
  return events.reduce(reduceQuestionnaire, from);
}

const listed: QuestionnaireEvent = {
  type: "files_result",
  call: { kind: "ok", value: { kind: "questionnaires", files: ["a.xlsx", "b.xlsm"], truncated: false } },
};
const inspected: QuestionnaireEvent[] = [
  listed,
  { type: "file_selected", file: "a.xlsx" },
  { type: "inspect_requested" },
  { type: "inspect_result", result: replyOf("questionnaire_inspect", 200, inspectBody()) },
];
const ready = run(inspected);
const startedEvents: QuestionnaireEvent[] = [
  { type: "start_requested" },
  { type: "start_result", result: replyOf("questionnaire_start", 202, { run_id: RUN, total: 6 }), nowMs: 1000 },
];
const running = run(startedEvents, ready);

describe("the file list", () => {
  it("is loading, then lists names", () => {
    expect(run([{ type: "files_requested" }]).files).toEqual({ phase: "loading" });
    expect(run([listed]).files).toEqual({ phase: "listed", files: ["a.xlsx", "b.xlsm"], truncated: false });
  });

  it("an empty folder is an empty list, not a failure", () => {
    const state = run([
      { type: "files_result", call: { kind: "ok", value: { kind: "questionnaires", files: [], truncated: false } } },
    ]);
    expect(state.files).toEqual({ phase: "listed", files: [], truncated: false });
  });

  it("not_configured is the questionnaires folder not being set", () => {
    const state = run([{ type: "files_result", call: { kind: "folder_error", error: "not_configured" } }]);
    expect(state.files.phase === "failed" && state.files.failure.code).toBe("questionnaire_root_not_configured");
  });

  it("other failures use their own fixed words", () => {
    for (const call of [
      { kind: "folder_error", error: "not_listable" } as const,
      { kind: "rejected" } as const,
      { kind: "invalid_response" } as const,
    ]) {
      expect(run([{ type: "files_result", call }]).files.phase).toBe("failed");
    }
  });

  it("forgets the chosen file when it is gone from the folder", () => {
    const gone = reduceQuestionnaire(ready, {
      type: "files_result",
      call: { kind: "ok", value: { kind: "questionnaires", files: ["z.xlsx"], truncated: false } },
    });
    expect(gone).toMatchObject({ selected: null, inspect: { phase: "idle" }, mapping: null });
    const kept = reduceQuestionnaire(ready, listed);
    expect(kept.selected).toBe("a.xlsx");
    expect(kept.inspect.phase).toBe("ready");
  });
});

describe("inspect and the mapping", () => {
  it("reads the sheets and seeds the mapping from the suggestion", () => {
    expect(ready.inspect.phase).toBe("ready");
    expect(ready.mapping).toEqual({ sheet: "Sheet1", headerRow: 2, questionCol: 3, idCol: 1 });
  });

  it("with no suggestion starts on the first sheet, row 1, column 1", () => {
    const state = run(
      [
        { type: "inspect_requested" },
        { type: "inspect_result", result: replyOf("questionnaire_inspect", 200, inspectBody({ suggestion: null })) },
      ],
      run([listed, { type: "file_selected", file: "a.xlsx" }]),
    );
    expect(state.mapping).toEqual({ sheet: "Sheet1", headerRow: 1, questionCol: 1, idCol: null });
  });

  it("every workbook_refused code and a loading state are shown, never raw text", () => {
    const selected = run([listed, { type: "file_selected", file: "a.xlsx" }, { type: "inspect_requested" }]);
    expect(selected.inspect.phase).toBe("loading");
    const refused = reduceQuestionnaire(selected, {
      type: "inspect_result",
      result: errorOf("questionnaire_inspect", 422, "workbook_refused", { code: "doctype" }),
    });
    if (refused.inspect.phase !== "refused") throw new Error("expected refused");
    expect(refused.inspect.failure.code).toBe("workbook_refused:doctype");
  });

  it("a not-found file and a missing questionnaires folder are typed refusals", () => {
    const selected = run([listed, { type: "file_selected", file: "a.xlsx" }, { type: "inspect_requested" }]);
    for (const word of ["file_not_found", "path_not_allowed", "questionnaire_root_not_configured"]) {
      const state = reduceQuestionnaire(selected, {
        type: "inspect_result",
        result: errorOf("questionnaire_inspect", word === "file_not_found" ? 404 : 503, word),
      });
      expect(state.inspect.phase === "refused" && state.inspect.failure.code).toBe(word);
    }
  });

  it("choosing another file clears what was read from the first", () => {
    const other = reduceQuestionnaire(ready, { type: "file_selected", file: "b.xlsm" });
    expect(other).toMatchObject({ selected: "b.xlsm", inspect: { phase: "idle" }, mapping: null });
  });

  it("checks the mapping against the request bounds before Start", () => {
    const sheets = [{ name: "Sheet1" }];
    const good = { sheet: "Sheet1", headerRow: 1, questionCol: 2, idCol: null };
    expect(mappingProblems(good, sheets)).toEqual([]);
    expect(mappingRequest(good, sheets)).toEqual({ sheet: "Sheet1", header_row: 1, question_col: 2 });
    expect(mappingRequest({ ...good, idCol: 4 }, sheets)).toEqual({
      sheet: "Sheet1",
      header_row: 1,
      question_col: 2,
      id_col: 4,
    });
    expect(mappingProblems({ ...good, sheet: "Nope" }, sheets)).toEqual(["sheet_not_listed"]);
    expect(mappingProblems({ ...good, headerRow: 0, questionCol: 99_999 }, sheets)).toEqual([
      "header_row_out_of_range",
      "question_col_out_of_range",
    ]);
    expect(mappingProblems({ ...good, idCol: 1.5 }, sheets)).toEqual(["id_col_out_of_range"]);
    expect(mappingRequest({ ...good, headerRow: 0 }, sheets)).toBeNull();
  });
});

describe("starting a run", () => {
  it("only starts after the sheets were read", () => {
    const early = run([listed, { type: "file_selected", file: "a.xlsx" }, { type: "start_requested" }]);
    expect(early.run).toEqual({ phase: "none" });
  });

  it("202 starts a run and polls it", () => {
    expect(running.run).toMatchObject({ phase: "running", runId: RUN, total: 6, completed: 0, lastChangeMs: 1000 });
    expect(runPollTarget(running)).toBe(RUN);
    expect(runIsActive(running.run)).toBe(true);
  });

  it("locks the file, the mapping and Start while a run is going", () => {
    expect(reduceQuestionnaire(running, { type: "file_selected", file: "b.xlsm" })).toBe(running);
    expect(reduceQuestionnaire(running, { type: "start_requested" })).toBe(running);
    expect(reduceQuestionnaire(running, { type: "inspect_requested" })).toBe(running);
    expect(
      reduceQuestionnaire(running, {
        type: "mapping_changed",
        mapping: { sheet: "Other", headerRow: 1, questionCol: 1, idCol: null },
      }),
    ).toBe(running);
    expect(reduceQuestionnaire(running, { type: "run_dismissed" })).toBe(running);
  });

  const refusals: Array<[string, number, string, object]> = [
    ["busy", 409, "run_in_progress", {}],
    ["index not built", 409, "index_not_built", {}],
    ["vault changed", 409, "vault_changed_reindex", {}],
    ["retrieval", 503, "retrieval_unavailable", {}],
    ["models", 503, "models_unavailable", { models_missing: ["llama3.1:8b"] }],
    ["mapping", 422, "mapping_refused", { code: "too_many_questions" }],
    ["export", 503, "export_root_not_configured", {}],
    ["questionnaires", 503, "questionnaire_root_not_configured", {}],
    ["workbook", 422, "workbook_refused", { code: "unreadable" }],
  ];
  for (const [name, status, word, extra] of refusals) {
    it(`${name} (${word}) is a refusal with its own message and no polling`, () => {
      const state = run(
        [
          { type: "start_requested" },
          { type: "start_result", result: errorOf("questionnaire_start", status, word, extra), nowMs: 1 },
        ],
        ready,
      );
      expect(state.run.phase).toBe("refused");
      expect(runPollTarget(state)).toBeNull();
      if (state.run.phase === "refused") {
        expect(state.run.failure.code.startsWith(word)).toBe(true);
        expect(state.run.failure.text.length).toBeGreaterThan(10);
      }
    });
  }

  it("409 busy says 'another job is running' (a run while an index job is going)", () => {
    const state = run(
      [
        { type: "start_requested" },
        { type: "start_result", result: errorOf("questionnaire_start", 409, "run_in_progress"), nowMs: 1 },
      ],
      ready,
    );
    if (state.run.phase !== "refused") throw new Error("expected refused");
    expect(state.run.failure.category).toBe("busy");
    expect(state.run.failure.text).toContain("another job is running");
    // No automatic retry: the only way out is the user pressing Start again.
    expect(runPollTarget(state)).toBeNull();
    expect(reduceQuestionnaire(state, { type: "status_result", result: replyOf("questionnaire_status", 200, statusBody()), nowMs: 9 })).toBe(state);
  });

  it("a call that gave no reply is refused with a fixed word", () => {
    const state = run(
      [
        { type: "start_requested" },
        { type: "start_result", result: { kind: "proxy_error", error: "timeout" }, nowMs: 1 },
      ],
      ready,
    );
    expect(state.run.phase === "refused" && state.run.failure.code).toBe("timeout");
  });
});

describe("progress", () => {
  const poll = (state: QuestionnaireState, body: object, nowMs: number) =>
    reduceQuestionnaire(state, {
      type: "status_result",
      result: replyOf("questionnaire_status", 200, statusBody(body)),
      nowMs,
    });

  it("moves completed / total and restarts the local counter only when completed changes", () => {
    const one = poll(running, { completed: 1 }, 41_000);
    expect(one.run).toMatchObject({ completed: 1, total: 6, lastChangeMs: 41_000 });
    const same = poll(one, { completed: 1 }, 50_000);
    expect(same.run).toMatchObject({ completed: 1, lastChangeMs: 41_000 });
    expect(secondsSinceLastAnswer(same.run, 71_999)).toBe(30);
    expect(secondsSinceLastAnswer(same.run, 72_000)).toBe(31);
  });

  it("the counter exists only while running", () => {
    expect(secondsSinceLastAnswer({ phase: "none" }, 5)).toBeNull();
    expect(secondsSinceLastAnswer({ phase: "unknown_run" }, 5)).toBeNull();
  });

  it("completed equal to total while running is 'writing the answers file', not done", () => {
    const writing = poll(running, { completed: 6 }, 90_000);
    expect(writing.run.phase).toBe("running");
    expect(isWritingFile(writing.run)).toBe(true);
    expect(isWritingFile(poll(running, { completed: 5 }, 90_000).run)).toBe(false);
    expect(isWritingFile({ phase: "none" })).toBe(false);
    expect(runPollTarget(writing)).toBe(RUN);
  });

  it("complete finishes the run with the summary and stops polling", () => {
    const done = poll(running, { status: "complete", completed: 6, summary: summaryBody() }, 100_000);
    expect(done.run).toMatchObject({ phase: "finished", outcome: "complete", completed: 6, failure: null });
    expect(runPollTarget(done)).toBeNull();
    if (done.run.phase === "finished") {
      expect(done.run.summary).toMatchObject({ fileName: "q-UNREVIEWED-DRAFT-00ff00ff.xlsx", rowsWritten: 6, seconds: 12.34 });
    }
  });

  it("stopped (the model went away) and failed are their own outcomes", () => {
    const stopped = poll(running, { status: "stopped", completed: 2, summary: summaryBody({ complete: false }) }, 1);
    expect(stopped.run).toMatchObject({ phase: "finished", outcome: "stopped", completed: 2 });
    const failed = poll(running, { status: "failed", failure: "generation_failed" }, 1);
    expect(failed.run).toMatchObject({ phase: "finished", outcome: "failed", failure: "generation_failed", summary: null });
    const hostile = poll(running, { status: "failed", failure: "<script>alert(1)</script>" }, 1);
    expect(hostile.run).toMatchObject({ outcome: "failed", failure: null });
  });

  it("404 run_not_found is the unknown-run state", () => {
    const unknown = reduceQuestionnaire(running, {
      type: "status_result",
      result: errorOf("questionnaire_status", 404, "run_not_found"),
      nowMs: 1,
    });
    expect(unknown.run).toEqual({ phase: "unknown_run" });
    expect(runPollTarget(unknown)).toBeNull();
  });

  it("a failed check keeps the run and records a fixed message; the next good answer clears it", () => {
    const problem = reduceQuestionnaire(running, {
      type: "status_result",
      result: { kind: "proxy_error", error: "unreachable" },
      nowMs: 1,
    });
    expect(problem.run.phase === "running" && problem.run.problem?.code).toBe("unreachable");
    expect(runPollTarget(problem)).toBe(RUN);
    const cleared = poll(problem, { completed: 1 }, 2000);
    expect(cleared.run.phase === "running" && cleared.run.problem).toBeNull();
  });

  it("ignores a status answer when no run is going", () => {
    const result = replyOf("questionnaire_status", 200, statusBody());
    expect(reduceQuestionnaire(ready, { type: "status_result", result, nowMs: 1 })).toBe(ready);
  });

  it("a finished run can be cleared; the file stays chosen", () => {
    const done = poll(running, { status: "complete", completed: 6, summary: summaryBody() }, 1);
    const cleared = reduceQuestionnaire(done, { type: "run_dismissed" });
    expect(cleared.run).toEqual({ phase: "none" });
    expect(cleared.selected).toBe("a.xlsx");
  });
});

describe("the summary", () => {
  const base: RunSummary = {
    model: "m",
    complete: true,
    counts: {},
    reasonCounts: {},
    fileName: null,
    rowsWritten: 0,
    droppedParts: [],
    warnings: [],
    seconds: 1,
  };

  it("orders the three known statuses first, then any other key", () => {
    expect(
      orderedCounts({ ZZZ: 1, NEEDS_REVIEW: 3, SUPPORTED: 2, AAA: 4 }).map((c) => c.key),
    ).toEqual(["SUPPORTED", "NEEDS_REVIEW", "AAA", "ZZZ"]);
    expect(orderedCounts({})).toEqual([]);
  });

  it("run_record_not_kept and run_record_failed mean there is no run record", () => {
    expect(runRecordMissing(null)).toBe(false);
    expect(runRecordMissing(base)).toBe(false);
    expect(runRecordMissing({ ...base, warnings: ["run_record_not_kept"] })).toBe(true);
    expect(runRecordMissing({ ...base, warnings: ["x", "run_record_failed"] })).toBe(true);
    expect(runRecordMissing({ ...base, warnings: ["run_record_not_kept_really"] })).toBe(false);
  });
});
