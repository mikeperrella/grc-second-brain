// Polling stops when told to (amendment: unmount, health leaving healthy, job end) and never
// overlaps. Timers are faked; no network, no browser.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { indexPollTarget, INITIAL_INDEX_STATE } from "./indexJob";
import { pollTargetWhen, secondsSince, startPolling } from "./poller";
import { INITIAL_QUESTIONNAIRE_STATE, runPollTarget } from "./questionnaireRun";

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("startPolling", () => {
  it("asks once now, then once per interval, and hands each answer on", async () => {
    const probe = vi.fn(() => Promise.resolve("a"));
    const seen: string[] = [];
    const stop = startPolling(probe, (r) => seen.push(r), 1000);
    await vi.advanceTimersByTimeAsync(0);
    expect(probe).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1000);
    expect(probe).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(2000);
    expect(probe).toHaveBeenCalledTimes(4);
    expect(seen).toHaveLength(4);
    stop();
  });

  it("stopping prevents any further call", async () => {
    const probe = vi.fn(() => Promise.resolve(1));
    const stop = startPolling(probe, () => undefined, 1000);
    await vi.advanceTimersByTimeAsync(1000);
    const callsBefore = probe.mock.calls.length;
    expect(callsBefore).toBeGreaterThan(0);
    stop();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(probe).toHaveBeenCalledTimes(callsBefore);
  });

  it("drops the answer of a probe that is still in flight when it is stopped", async () => {
    let finish: (value: string) => void = () => undefined;
    const probe = vi.fn(() => new Promise<string>((resolve) => (finish = resolve)));
    const seen: string[] = [];
    const stop = startPolling(probe, (r) => seen.push(r), 1000);
    await vi.advanceTimersByTimeAsync(0);
    stop();
    finish("late");
    await vi.advanceTimersByTimeAsync(5000);
    expect(seen).toEqual([]);
    expect(probe).toHaveBeenCalledTimes(1);
  });

  it("never starts a second probe while one is unanswered", async () => {
    const probe = vi.fn(() => new Promise<string>(() => undefined));
    const stop = startPolling(probe, () => undefined, 1000);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(probe).toHaveBeenCalledTimes(1);
    stop();
  });
});

describe("the hooks' polling target", () => {
  it("is the job's id only while health is healthy", () => {
    expect(pollTargetWhen(true, "abc12345")).toBe("abc12345");
    expect(pollTargetWhen(false, "abc12345")).toBeNull();
    expect(pollTargetWhen(true, null)).toBeNull();
  });

  it("nothing is polled before a job or run exists", () => {
    expect(indexPollTarget(INITIAL_INDEX_STATE)).toBeNull();
    expect(runPollTarget(INITIAL_QUESTIONNAIRE_STATE)).toBeNull();
  });

  // Mirrors what each hook's effect does: start when there is a target, clean up when it changes.
  it("stops asking when health leaves healthy, and resumes when it returns", async () => {
    const probe = vi.fn(() => Promise.resolve(0));
    let stop: (() => void) | undefined;
    const effect = (healthy: boolean): void => {
      stop?.();
      stop = undefined;
      const target = pollTargetWhen(healthy, "run00001");
      if (target !== null) {
        stop = startPolling(probe, () => undefined, 1000);
      }
    };

    effect(true);
    await vi.advanceTimersByTimeAsync(3000);
    const whileHealthy = probe.mock.calls.length;
    expect(whileHealthy).toBeGreaterThanOrEqual(3);

    effect(false); // health left `healthy`
    await vi.advanceTimersByTimeAsync(30_000);
    expect(probe).toHaveBeenCalledTimes(whileHealthy);

    effect(true); // back to healthy
    await vi.advanceTimersByTimeAsync(1000);
    expect(probe.mock.calls.length).toBeGreaterThan(whileHealthy);

    stop?.(); // unmount
    const afterUnmount = probe.mock.calls.length;
    await vi.advanceTimersByTimeAsync(30_000);
    expect(probe).toHaveBeenCalledTimes(afterUnmount);
  });
});

describe("secondsSince", () => {
  it("counts whole seconds and is never negative", () => {
    expect(secondsSince(10_999, 10_000)).toBe(0);
    expect(secondsSince(11_000, 10_000)).toBe(1);
    expect(secondsSince(75_500, 10_000)).toBe(65);
    expect(secondsSince(5_000, 10_000)).toBe(0);
  });
});
