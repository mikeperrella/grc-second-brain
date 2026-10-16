// Polls `readiness` through the host proxy (M6_PLAN B.4, ADR-020 consequences).
//
// The window starts this ONLY while health is `healthy` and stops it when health leaves that state
// (the hook in hooks/useReadiness.ts), so a starting or dead sidecar is never asked. The next timer
// is set only after the previous probe has finished, so probes never overlap. The snapshot the
// sidecar returns can be stale (ADR-011), so a typed `503 models_unavailable` from another route is
// still handled where that route is used.

import type { ReadinessView } from "./readiness";

export const READINESS_POLL_MS = 5000;

/**
 * Ask once now, then every `READINESS_POLL_MS`. Returns a function that stops everything,
 * including a probe still in flight (its answer is dropped). `probe` must not reject; the readiness
 * view builder never does.
 */
export function startReadinessPolling(
  probe: () => Promise<ReadinessView>,
  onView: (view: ReadinessView) => void,
  intervalMs: number = READINESS_POLL_MS,
): () => void {
  let cancelled = false;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const tick = async (): Promise<void> => {
    const view = await probe();
    if (cancelled) {
      return;
    }
    onView(view);
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
