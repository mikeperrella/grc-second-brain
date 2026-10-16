// The index job's states (M6_PLAN C.3): every refusal, busy, unknown job, finished outcomes. No
// React. A 409 leaves a state with no poll target, so nothing retries.

import { describe, expect, it } from "vitest";
import {
  INITIAL_INDEX_STATE,
  indexPollTarget,
  reduceIndex,
  type IndexEvent,
  type IndexState,
} from "./indexJob";
import { errorOf, replyOf } from "./testSupport";

const JOB = "0123456789abcdef";

const statusBody = (over: object = {}) => ({
  job_id: JOB,
  status: "running",
  files_done: 2,
  files_total: 9,
  files_indexed: 1,
  files_unchanged: 1,
  files_removed: 0,
  files_failed: 0,
  chunks_embedded: 14,
  failure: null,
  ...over,
});

function run(events: IndexEvent[], from: IndexState = INITIAL_INDEX_STATE): IndexState {
  return events.reduce(reduceIndex, from);
}

const started: IndexEvent[] = [
  { type: "start_requested" },
  { type: "start_result", result: replyOf("vault_index_start", 202, { job_id: JOB }), nowMs: 5000 },
];

describe("starting", () => {
  it("idle → starting → running with the job id, polling it", () => {
    expect(run([{ type: "start_requested" }]).phase).toBe("starting");
    const state = run(started);
    expect(state).toMatchObject({ phase: "running", jobId: JOB, startedAtMs: 5000, problem: null });
    expect(indexPollTarget(state)).toBe(JOB);
  });

  it("does not start a second job while one is starting or running", () => {
    const starting = run([{ type: "start_requested" }]);
    expect(reduceIndex(starting, { type: "start_requested" })).toBe(starting);
    const running = run(started);
    expect(reduceIndex(running, { type: "start_requested" })).toBe(running);
  });

  it("ignores a start result nobody is waiting for", () => {
    const result = replyOf("vault_index_start", 202, { job_id: JOB });
    expect(reduceIndex(INITIAL_INDEX_STATE, { type: "start_result", result, nowMs: 1 })).toBe(
      INITIAL_INDEX_STATE,
    );
  });
});

describe("refusals of the start, each a fixed message", () => {
  const refuse = (result: IndexEvent) => run([{ type: "start_requested" }, result]);

  it("409 run_in_progress is busy ('another job is running') and nothing polls or retries", () => {
    const state = refuse({
      type: "start_result",
      result: errorOf("vault_index_start", 409, "run_in_progress"),
      nowMs: 1,
    });
    expect(state.phase).toBe("refused");
    if (state.phase === "refused") {
      expect(state.failure.category).toBe("busy");
      expect(state.failure.text).toContain("another job is running");
    }
    expect(indexPollTarget(state)).toBeNull();
    // Another start is a new, explicit request by the user, never an automatic one.
    expect(reduceIndex(state, { type: "status_result", result: replyOf("vault_index_status", 200, statusBody()) })).toBe(state);
  });

  it("503 models_unavailable carries the missing names and asks for the no-download sentence", () => {
    const state = refuse({
      type: "start_result",
      result: errorOf("vault_index_start", 503, "models_unavailable", { models_missing: ["nomic-embed-text"] }),
      nowMs: 1,
    });
    if (state.phase !== "refused") throw new Error("expected refused");
    expect(state.failure).toMatchObject({
      category: "models",
      models: ["nomic-embed-text"],
      showNoDownload: true,
    });
  });

  it("503 vault_root_not_configured is its own message", () => {
    const state = refuse({
      type: "start_result",
      result: errorOf("vault_index_start", 503, "vault_root_not_configured"),
      nowMs: 1,
    });
    if (state.phase !== "refused") throw new Error("expected refused");
    expect(state.failure.code).toBe("vault_root_not_configured");
  });

  it("every call that gave no reply is refused with a fixed word", () => {
    for (const result of [
      { kind: "proxy_error", error: "unreachable" } as const,
      { kind: "invalid_response" } as const,
      { kind: "rejected" } as const,
      { kind: "invalid_request" } as const,
    ]) {
      const state = refuse({ type: "start_result", result, nowMs: 1 });
      expect(state.phase).toBe("refused");
    }
  });
});

describe("polling the status", () => {
  it("moves the counts while running", () => {
    const state = reduceIndex(run(started), {
      type: "status_result",
      result: replyOf("vault_index_status", 200, statusBody()),
    });
    expect(state).toMatchObject({ phase: "running", progress: { filesDone: 2, filesTotal: 9, chunksEmbedded: 14 } });
  });

  it("finishes as complete, incomplete or failed, and stops polling", () => {
    for (const status of ["complete", "incomplete", "failed"] as const) {
      const state = reduceIndex(run(started), {
        type: "status_result",
        result: replyOf("vault_index_status", 200, statusBody({ status })),
      });
      expect(state).toMatchObject({ phase: "finished", outcome: status });
      expect(indexPollTarget(state)).toBeNull();
    }
  });

  it("shows a failure only if it is a plain code word", () => {
    const code = reduceIndex(run(started), {
      type: "status_result",
      result: replyOf("vault_index_status", 200, statusBody({ status: "failed", failure: "embedding_failed" })),
    });
    expect(code).toMatchObject({ failure: "embedding_failed" });
    const hostile = reduceIndex(run(started), {
      type: "status_result",
      result: replyOf("vault_index_status", 200, statusBody({ status: "failed", failure: "<b>x</b> /home/me/secret" })),
    });
    expect(hostile).toMatchObject({ phase: "finished", failure: null });
  });

  it("404 is an unknown job", () => {
    const state = reduceIndex(run(started), {
      type: "status_result",
      result: errorOf("vault_index_status", 404, "run_not_found"),
    });
    expect(state).toEqual({ phase: "unknown_job" });
    expect(indexPollTarget(state)).toBeNull();
  });

  it("a failed check keeps the job running and records a fixed message", () => {
    const state = reduceIndex(run(started), {
      type: "status_result",
      result: { kind: "proxy_error", error: "timeout" },
    });
    expect(state.phase).toBe("running");
    if (state.phase === "running") expect(state.problem?.code).toBe("timeout");
    expect(indexPollTarget(state)).toBe(JOB);
    const later = reduceIndex(state, { type: "status_result", result: replyOf("vault_index_status", 200, statusBody()) });
    if (later.phase === "running") expect(later.problem).toBeNull();
  });

  it("ignores a status answer when nothing is running", () => {
    expect(
      reduceIndex(INITIAL_INDEX_STATE, { type: "status_result", result: replyOf("vault_index_status", 200, statusBody()) }),
    ).toBe(INITIAL_INDEX_STATE);
  });
});
