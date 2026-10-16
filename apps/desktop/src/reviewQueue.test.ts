// The review queue's pure logic: every state of the reducer, the ordering and the per-row rules.

import { describe, expect, it } from "vitest";
import {
  DRAFT_UNREADABLE_TEXT,
  HEADERS_CHANGED_TEXT,
  QUESTION_MISMATCH_TEXT,
  RUN_RECORD_MISSING_TEXT,
} from "./fixedTexts";
import { routeReplySchemas } from "./ipc/proxy";
import {
  INITIAL_REVIEW_QUEUE_STATE,
  orderRows,
  reasonCode,
  reduceReviewQueue,
  riskRank,
  showsReviewSentence,
  type DraftRow,
  type ReviewQueueEvent,
  type ReviewQueueState,
} from "./reviewQueue";
import { errorOf, replyOf } from "./testSupport";

function row(over: Partial<DraftRow> = {}): DraftRow {
  return {
    row: 1,
    question_text: "q",
    status: "SUPPORTED",
    reasons: [],
    answer_text: "a",
    citation: { source_path: "p.md", start_line: 1, end_line: 2, quote: "quote" },
    status_edited: false,
    answer_edited: false,
    evidence_edited: false,
    ...over,
  };
}

const body = (rows: DraftRow[]) => ({
  model: "m",
  file_name: "A.xlsx",
  complete: true,
  question_count: rows.length,
  rows,
});

function play(events: ReviewQueueEvent[], from: ReviewQueueState = INITIAL_REVIEW_QUEUE_STATE) {
  return events.reduce(reduceReviewQueue, from);
}

const listing = (files: string[], truncated = false): ReviewQueueEvent => ({
  type: "files_result",
  call: { kind: "ok", value: { kind: "exports", files, truncated } },
});

const opening = (file: string): ReviewQueueEvent[] => [
  { type: "file_selected", file },
  { type: "draft_requested" },
];

describe("the file list", () => {
  it("starts idle, loads, and lists", () => {
    expect(INITIAL_REVIEW_QUEUE_STATE.files).toEqual({ phase: "idle" });
    expect(play([{ type: "files_requested" }]).files).toEqual({ phase: "loading" });
    expect(play([listing(["A.xlsx"], true)]).files).toEqual({
      phase: "listed",
      files: ["A.xlsx"],
      truncated: true,
    });
  });

  it("an empty folder is a listed state with no files, not a failure", () => {
    expect(play([listing([])]).files).toEqual({ phase: "listed", files: [], truncated: false });
  });

  it("not configured is a failure naming the exports folder", () => {
    const state = play([{ type: "files_result", call: { kind: "folder_error", error: "not_configured" } }]);
    expect(state.files.phase).toBe("failed");
    if (state.files.phase === "failed") {
      expect(state.files.failure.code).toBe("export_root_not_configured");
      expect(state.files.failure.text).toContain("exports");
    }
  });

  it("other host failures are described by fixed words", () => {
    for (const call of [
      { kind: "folder_error", error: "unavailable" },
      { kind: "invalid_response" },
      { kind: "rejected" },
    ] as const) {
      expect(play([{ type: "files_result", call }]).files.phase).toBe("failed");
    }
  });

  it("forgets the chosen file, and its rows, when it leaves the folder", () => {
    const loaded = play([
      listing(["A.xlsx"]),
      ...opening("A.xlsx"),
      { type: "draft_result", file: "A.xlsx", result: replyOf("draft_rows", 200, body([row()])) },
    ]);
    expect(loaded.draft.phase).toBe("loaded");
    const after = reduceReviewQueue(loaded, listing(["B.xlsx"]));
    expect(after.selected).toBeNull();
    expect(after.draft).toEqual({ phase: "none" });
    // Still there: nothing is forgotten.
    expect(reduceReviewQueue(loaded, listing(["A.xlsx", "B.xlsx"])).draft.phase).toBe("loaded");
  });
});

describe("reading a draft", () => {
  it("none, then loading, then loaded with header and ordered rows", () => {
    expect(INITIAL_REVIEW_QUEUE_STATE.draft).toEqual({ phase: "none" });
    const loading = play([listing(["A.xlsx"]), ...opening("A.xlsx")]);
    expect(loading.draft).toEqual({ phase: "loading" });
    const loaded = reduceReviewQueue(loading, {
      type: "draft_result",
      file: "A.xlsx",
      result: replyOf("draft_rows", 200, { ...body([row({ row: 1 }), row({ row: 2, status: "NEEDS_REVIEW" })]), complete: false }),
    });
    expect(loaded.draft.phase).toBe("loaded");
    if (loaded.draft.phase === "loaded") {
      expect(loaded.draft.header).toEqual({ model: "m", fileName: "A.xlsx", complete: false, questionCount: 2 });
      expect(loaded.draft.rows.map((r) => r.row)).toEqual([2, 1]);
    }
  });

  it("draft_requested with nothing selected does nothing", () => {
    expect(reduceReviewQueue(INITIAL_REVIEW_QUEUE_STATE, { type: "draft_requested" })).toBe(
      INITIAL_REVIEW_QUEUE_STATE,
    );
  });

  it("selecting another file drops the previous file's rows", () => {
    const loaded = play([
      listing(["A.xlsx", "B.xlsx"]),
      ...opening("A.xlsx"),
      { type: "draft_result", file: "A.xlsx", result: replyOf("draft_rows", 200, body([row()])) },
    ]);
    const next = reduceReviewQueue(loaded, { type: "file_selected", file: "B.xlsx" });
    expect(next.selected).toBe("B.xlsx");
    expect(next.draft).toEqual({ phase: "none" });
  });

  it("a late result for a previously selected file does not change the current selection's state", () => {
    const stateB = play([listing(["A.xlsx", "B.xlsx"]), ...opening("A.xlsx"), ...opening("B.xlsx")]);
    expect(stateB.selected).toBe("B.xlsx");
    expect(stateB.draft).toEqual({ phase: "loading" });
    for (const late of [
      replyOf("draft_rows", 200, body([row()])),
      errorOf("draft_rows", 422, "draft_refused", { code: "question_mismatch" }),
      { kind: "proxy_error", error: "unreachable" } as const,
    ]) {
      const after = reduceReviewQueue(stateB, { type: "draft_result", file: "A.xlsx", result: late as never });
      expect(after).toBe(stateB);
    }
    // And after B has loaded, a late answer for A still changes nothing.
    const loadedB = reduceReviewQueue(stateB, {
      type: "draft_result",
      file: "B.xlsx",
      result: replyOf("draft_rows", 200, body([row({ row: 9 })])),
    });
    const lateA = reduceReviewQueue(loadedB, {
      type: "draft_result",
      file: "A.xlsx",
      result: replyOf("draft_rows", 200, body([row({ row: 1 })])),
    });
    expect(lateA).toBe(loadedB);
  });

  it("an answer nobody is waiting for changes nothing", () => {
    const state = play([listing(["A.xlsx"]), { type: "file_selected", file: "A.xlsx" }]);
    const after = reduceReviewQueue(state, {
      type: "draft_result",
      file: "A.xlsx",
      result: replyOf("draft_rows", 200, body([row()])),
    });
    expect(after).toBe(state);
  });

  it("every draft_refused code gives a refused state with its fixed text and no rows", () => {
    const expected: Record<string, string> = {
      question_mismatch: QUESTION_MISMATCH_TEXT,
      run_record_missing: RUN_RECORD_MISSING_TEXT,
      headers_changed: HEADERS_CHANGED_TEXT,
      unreadable: DRAFT_UNREADABLE_TEXT,
    };
    for (const [code, text] of Object.entries(expected)) {
      const state = play([
        listing(["A.xlsx"]),
        ...opening("A.xlsx"),
        { type: "draft_result", file: "A.xlsx", result: errorOf("draft_rows", 422, "draft_refused", { code }) as never },
      ]);
      expect(state.draft.phase, code).toBe("refused");
      if (state.draft.phase === "refused") {
        expect(state.draft.failure.text).toBe(text);
        expect(state.draft.failure.code).toBe(`draft_refused:${code}`);
        expect(state.draft.counts).toEqual([]);
      }
    }
  });

  it("question_mismatch keeps integer counts under code-word keys only", () => {
    const state = play([
      listing(["A.xlsx"]),
      ...opening("A.xlsx"),
      {
        type: "draft_result",
        file: "A.xlsx",
        result: errorOf("draft_rows", 422, "draft_refused", {
          code: "question_mismatch",
          counts: { rows_record: 3, rows_file: 4, "Bad Key": 1 },
        }) as never,
      },
    ]);
    if (state.draft.phase !== "refused") {
      throw new Error("expected refused");
    }
    expect(state.draft.counts).toEqual([
      ["rows_record", 3],
      ["rows_file", 4],
    ]);
  });

  it("the other whole-queue refusals and host errors are refused states with fixed words", () => {
    const cases: Array<[unknown, string]> = [
      [errorOf("draft_rows", 503, "ledger_root_not_configured"), "ledger_root_not_configured"],
      [errorOf("draft_rows", 503, "export_root_not_configured"), "export_root_not_configured"],
      [errorOf("draft_rows", 404, "file_not_found"), "file_not_found"],
      [errorOf("draft_rows", 400, "path_not_allowed"), "path_not_allowed"],
      [{ kind: "proxy_error", error: "unreachable" }, "unreachable"],
      [{ kind: "invalid_response" }, "invalid_response"],
      [{ kind: "rejected" }, "rejected"],
    ];
    for (const [result, code] of cases) {
      const state = play([
        listing(["A.xlsx"]),
        ...opening("A.xlsx"),
        { type: "draft_result", file: "A.xlsx", result: result as never },
      ]);
      expect(state.draft.phase, code).toBe("refused");
      if (state.draft.phase === "refused") {
        expect(state.draft.failure.code).toBe(code);
      }
    }
  });

  it("an unknown status loads (it is shown as an error block, not refused)", () => {
    const state = play([
      listing(["A.xlsx"]),
      ...opening("A.xlsx"),
      { type: "draft_result", file: "A.xlsx", result: replyOf("draft_rows", 200, body([row({ status: "WEIRD" })])) },
    ]);
    expect(state.draft.phase).toBe("loaded");
  });
});

describe("what the sidecar can send (the contract)", () => {
  it("has no field that could carry an edited cell's text", () => {
    const withCell = { ...body([row()]), rows: [{ ...row(), status_cell: "SUPPORTED" }] };
    expect(routeReplySchemas.draft_rows.safeParse({ status: 200, body: withCell }).success).toBe(false);
    expect(routeReplySchemas.draft_rows.safeParse({ status: 200, body: body([row()]) }).success).toBe(true);
  });
});

describe("ordering and per-row rules", () => {
  it("ranks edited-status, NEEDS_REVIEW and unknown first, then PARTIAL, then SUPPORTED", () => {
    expect(riskRank(row({ status: "NEEDS_REVIEW" }))).toBe(0);
    expect(riskRank(row({ status: "SUPPORTED", status_edited: true }))).toBe(0);
    expect(riskRank(row({ status: "PARTIAL", status_edited: true }))).toBe(0);
    expect(riskRank(row({ status: "WEIRD" }))).toBe(0);
    expect(riskRank(row({ status: "PARTIAL" }))).toBe(1);
    expect(riskRank(row({ status: "SUPPORTED" }))).toBe(2);
  });

  it("orders by risk, then row number, without changing its input", () => {
    const rows = [
      row({ row: 5, status: "SUPPORTED" }),
      row({ row: 4, status: "PARTIAL" }),
      row({ row: 3, status: "SUPPORTED", status_edited: true }),
      row({ row: 2, status: "NEEDS_REVIEW" }),
      row({ row: 1, status: "SUPPORTED" }),
    ];
    const copy = rows.map((r) => r.row);
    expect(orderRows(rows).map((r) => r.row)).toEqual([2, 3, 4, 1, 5]);
    expect(rows.map((r) => r.row)).toEqual(copy);
  });

  it("the review sentence goes on SUPPORTED and PARTIAL rows, and on others only with a quote", () => {
    expect(showsReviewSentence(row({ status: "SUPPORTED", citation: null }))).toBe(true);
    expect(showsReviewSentence(row({ status: "PARTIAL", citation: null }))).toBe(true);
    expect(showsReviewSentence(row({ status: "NEEDS_REVIEW", citation: null }))).toBe(false);
    expect(showsReviewSentence(row({ status: "NEEDS_REVIEW" }))).toBe(true);
    expect(showsReviewSentence(row({ status: "WEIRD", citation: null }))).toBe(false);
  });

  it("a reason is shown only if it is a plain code word", () => {
    expect(reasonCode("review_overdue")).toBe("review_overdue");
    expect(reasonCode("Not A Code")).toBeNull();
    expect(reasonCode("<b>")).toBeNull();
    expect(reasonCode("")).toBeNull();
  });
});
