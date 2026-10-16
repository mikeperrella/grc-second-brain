// A stoppable poller for the screens' status routes (ADR-020 decision 4: about once a second).
//
// Same shape as readinessPoller.ts: the next timer is set only after the previous probe has
// finished, so two probes never overlap, and the stop function also drops a probe that is still in
// flight. The hooks start this only while health is `healthy` and a job or run is going, and stop
// it on unmount, when health leaves `healthy`, and when the job ends.

export const STATUS_POLL_MS = 1000;

/**
 * Ask once now, then `intervalMs` after each answer. `probe` must not reject (the route client
 * never does). Returns a function that stops everything.
 */
export function startPolling<T>(
  probe: () => Promise<T>,
  onResult: (result: T) => void,
  intervalMs: number = STATUS_POLL_MS,
): () => void {
  let cancelled = false;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const tick = async (): Promise<void> => {
    const result = await probe();
    if (cancelled) {
      return;
    }
    onResult(result);
    if (cancelled) {
      return;
    }
    timer = setTimeout(() => void tick(), intervalMs);
  };

  void tick();

  return () => {
    cancelled = true;
    if (timer !== undefined) {
      clearTimeout(timer);
    }
  };
}

/**
 * The id a hook may poll right now: the job's own target, but ONLY while health is `healthy`.
 * When health leaves `healthy` this becomes `null`, the hook's effect cleans up, and polling stops.
 */
export function pollTargetWhen(healthy: boolean, target: string | null): string | null {
  return healthy ? target : null;
}

/** Whole seconds from `sinceMs` to `nowMs`, never negative. A local counter, not an estimate. */
export function secondsSince(nowMs: number, sinceMs: number): number {
  return Math.max(0, Math.floor((nowMs - sinceMs) / 1000));
}
