import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CallResult } from "./ipc/proxy";
import { describeOllama, NO_DOWNLOAD_STATEMENT, readinessView } from "./readiness";
import { READINESS_POLL_MS, startReadinessPolling } from "./readinessPoller";
import type { ReadinessView } from "./readiness";

const reply = (body: unknown, status = 200) =>
  ({ kind: "reply", route: "readiness", reply: { status, body } }) as unknown as CallResult<"readiness">;

describe("the fixed statement", () => {
  it("is exactly the words DESIGN.md 7 and M6_PLAN B.4 require", () => {
    expect(NO_DOWNLOAD_STATEMENT).toBe(
      "ALIBI never downloads models; install them with Ollama yourself",
    );
  });
});

describe("readinessView", () => {
  it("maps the three states of the sidecar", () => {
    expect(readinessView(reply({ state: "checking", models_missing: [], ollama: "unknown" }))).toEqual({ state: "checking" });
    expect(readinessView(reply({ state: "ready", models_missing: [], ollama: "reachable" }))).toEqual({ state: "ready" });
    expect(
      readinessView(reply({ state: "degraded", models_missing: ["a", "b"], ollama: "unreachable" })),
    ).toEqual({ state: "degraded", models: ["a", "b"], ollama: "unreachable" });
  });

  it("maps every failure of the call to unavailable with a fixed word", () => {
    expect(readinessView({ kind: "proxy_error", error: "not_ready" })).toEqual({ state: "unavailable", code: "not_ready" });
    expect(readinessView({ kind: "invalid_response" })).toEqual({ state: "unavailable", code: "invalid_response" });
    expect(readinessView({ kind: "rejected" })).toEqual({ state: "unavailable", code: "rejected" });
    expect(readinessView({ kind: "invalid_request" })).toEqual({ state: "unavailable", code: "invalid_request" });
    expect(readinessView(reply({ error: "run_in_progress" }, 409))).toEqual({ state: "unavailable", code: "run_in_progress" });
  });

  it("says each Ollama state in words", () => {
    expect(describeOllama("unknown")).toBe("Ollama: not checked yet");
    expect(describeOllama("reachable")).toBe("Ollama: reachable");
    expect(describeOllama("unreachable")).toBe("Ollama: not reachable");
    expect(describeOllama("bad_response")).toBe("Ollama: gave an answer ALIBI could not read");
  });
});

describe("startReadinessPolling", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  const READY: ReadinessView = { state: "ready" };

  it("asks once at once, then every 5 s, one probe at a time", async () => {
    expect(READINESS_POLL_MS).toBe(5000);
    let calls = 0;
    const views: ReadinessView[] = [];
    const stop = startReadinessPolling(
      () => {
        calls += 1;
        return Promise.resolve(READY);
      },
      (view) => views.push(view),
    );
    await vi.advanceTimersByTimeAsync(0);
    expect(calls).toBe(1);
    await vi.advanceTimersByTimeAsync(4999);
    expect(calls).toBe(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(calls).toBe(2);
    await vi.advanceTimersByTimeAsync(5000);
    expect(calls).toBe(3);
    expect(views).toHaveLength(3);
    stop();
  });

  it("stops at once, and drops the answer of a probe still in flight", async () => {
    let release: (view: ReadinessView) => void = () => undefined;
    const views: ReadinessView[] = [];
    const stop = startReadinessPolling(
      () => new Promise<ReadinessView>((resolve) => (release = resolve)),
      (view) => views.push(view),
    );
    await vi.advanceTimersByTimeAsync(0);
    stop();
    release(READY);
    await vi.advanceTimersByTimeAsync(20000);
    expect(views).toHaveLength(0);
  });

  it("makes no further call after stop", async () => {
    let calls = 0;
    const stop = startReadinessPolling(
      () => {
        calls += 1;
        return Promise.resolve(READY);
      },
      () => undefined,
    );
    await vi.advanceTimersByTimeAsync(0);
    stop();
    await vi.advanceTimersByTimeAsync(60000);
    expect(calls).toBe(1);
  });
});
