import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { HealthView } from "./health";
import {
  nextDelayMs,
  startHealthPolling,
  STARTING_POLL_MS,
  STEADY_POLL_MS,
} from "./healthPoller";

const STARTING: HealthView = { state: "starting" };
const HEALTHY: HealthView = { state: "healthy" };
const SIDECAR_FAILED: HealthView = { state: "failed", source: "sidecar", code: "timeout" };
const CHECK_FAILED: HealthView = { state: "failed", source: "health_check", code: "timeout" };
const STOPPED: HealthView = { state: "failed", source: "stopped", code: "not_running" };
const IPC_INVALID: HealthView = { state: "failed", source: "ipc", code: "invalid_response" };
const IPC_REJECTED: HealthView = { state: "failed", source: "ipc", code: "rejected" };

describe("nextDelayMs", () => {
  it("polls fast while starting and slowly while healthy", () => {
    expect(nextDelayMs(STARTING)).toBe(500);
    expect(nextDelayMs(HEALTHY)).toBe(5000);
    expect(STARTING_POLL_MS).toBe(500);
    expect(STEADY_POLL_MS).toBe(5000);
  });

  it("keeps checking a failed health check, at the slow rate", () => {
    expect(nextDelayMs(CHECK_FAILED)).toBe(5000);
  });

  it("stops on every terminal failure", () => {
    for (const view of [SIDECAR_FAILED, STOPPED, IPC_INVALID, IPC_REJECTED]) {
      expect(nextDelayMs(view), JSON.stringify(view)).toBeNull();
    }
  });
});

describe("startHealthPolling", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  /** A probe that answers from a list, then repeats the last answer, and counts its calls. */
  function scripted(...answers: HealthView[]) {
    let calls = 0;
    const probe = () => {
      const answer = answers[Math.min(calls, answers.length - 1)] ?? STARTING;
      calls += 1;
      return Promise.resolve(answer);
    };
    return { probe, calls: () => calls };
  }

  it("asks once immediately and reports the answer", async () => {
    const { probe, calls } = scripted(STARTING);
    const seen: HealthView[] = [];
    const stop = startHealthPolling(probe, (v) => seen.push(v));
    await vi.advanceTimersByTimeAsync(0);
    expect(calls()).toBe(1);
    expect(seen).toEqual([STARTING]);
    stop();
  });

  it("asks every 500 ms while starting, and not before", async () => {
    const { probe, calls } = scripted(STARTING);
    const stop = startHealthPolling(probe, () => {});
    await vi.advanceTimersByTimeAsync(499);
    expect(calls()).toBe(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(calls()).toBe(2);
    await vi.advanceTimersByTimeAsync(500);
    expect(calls()).toBe(3);
    stop();
  });

  it("slows to 5 s once healthy and keeps asking, so a dead sidecar is noticed", async () => {
    const { probe, calls } = scripted(STARTING, HEALTHY, HEALTHY, SIDECAR_FAILED);
    const seen: HealthView[] = [];
    const stop = startHealthPolling(probe, (v) => seen.push(v));
    await vi.advanceTimersByTimeAsync(500); // second call: healthy
    expect(calls()).toBe(2);
    await vi.advanceTimersByTimeAsync(4999);
    expect(calls()).toBe(2); // not yet: the healthy interval is 5 s
    await vi.advanceTimersByTimeAsync(1);
    expect(calls()).toBe(3);
    await vi.advanceTimersByTimeAsync(5000); // fourth call: the sidecar died
    expect(seen[seen.length - 1]).toEqual(SIDECAR_FAILED);
    stop();
  });

  it("stops asking after a terminal failure", async () => {
    const { probe, calls } = scripted(SIDECAR_FAILED);
    startHealthPolling(probe, () => {});
    await vi.advanceTimersByTimeAsync(60_000);
    expect(calls()).toBe(1);
  });

  it("keeps asking after a failed health check", async () => {
    const { probe, calls } = scripted(CHECK_FAILED);
    const stop = startHealthPolling(probe, () => {});
    await vi.advanceTimersByTimeAsync(5000);
    expect(calls()).toBe(2);
    await vi.advanceTimersByTimeAsync(5000);
    expect(calls()).toBe(3);
    stop();
  });

  it("does not start a second probe while one is still running", async () => {
    let calls = 0;
    let finish: (view: HealthView) => void = () => {};
    const probe = () => {
      calls += 1;
      return new Promise<HealthView>((resolve) => {
        finish = resolve;
      });
    };
    const stop = startHealthPolling(probe, () => {});
    await vi.advanceTimersByTimeAsync(30_000);
    expect(calls).toBe(1); // 30 s passed, the first probe has not answered
    finish(STARTING);
    await vi.advanceTimersByTimeAsync(500);
    expect(calls).toBe(2);
    stop();
  });

  it("stop() cancels the pending timer", async () => {
    const { probe, calls } = scripted(STARTING);
    const stop = startHealthPolling(probe, () => {});
    await vi.advanceTimersByTimeAsync(0);
    stop();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(calls()).toBe(1);
  });

  it("stop() drops the answer of a probe that is still in flight", async () => {
    let finish: (view: HealthView) => void = () => {};
    const probe = () =>
      new Promise<HealthView>((resolve) => {
        finish = resolve;
      });
    const seen: HealthView[] = [];
    const stop = startHealthPolling(probe, (v) => seen.push(v));
    stop();
    finish(HEALTHY);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(seen).toEqual([]);
  });
});
