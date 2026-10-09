// Polls the host for the sidecar's health (M2d plan section 4).
//
// Why polling continues after "healthy": if the sidecar dies later, the host's supervisor records
// it at once, but the window only learns it by asking. Stopping at "healthy" would leave the
// window showing "healthy" for a dead sidecar. A 5 s loopback request costs nothing measurable, and
// a stale "healthy" lasts at most one interval plus one probe. (A Tauri event pushed from the host
// would be instant but needs a core event permission, which would widen the capability.)

import type { HealthView } from "./health";

export const STARTING_POLL_MS = 500;
export const STEADY_POLL_MS = 5000;

/**
 * How long to wait before asking again, or `null` to stop.
 *
 * A failed health CHECK may be transient (a timeout, a refused connection) and, if the process is
 * really gone, the next answer becomes the terminal sidecar failure, so it is retried at the
 * steady rate. Every other failure is terminal: the host has no restart path in M2, and a refused
 * command or an unreadable reply will not fix itself.
 */
export function nextDelayMs(view: HealthView): number | null {
  switch (view.state) {
    case "starting":
      return STARTING_POLL_MS;
    case "healthy":
      return STEADY_POLL_MS;
    case "failed":
      return view.source === "health_check" ? STEADY_POLL_MS : null;
  }
}

/**
 * Ask once now, then keep asking at `nextDelayMs`. The next timer is set only after the previous
 * probe has finished, so probes never overlap. Returns a function that stops everything, including
 * a probe that is still in flight (its answer is dropped). `probe` must not reject; `fetchHealth`
 * never does.
 */
export function startHealthPolling(
  probe: () => Promise<HealthView>,
  onView: (view: HealthView) => void,
): () => void {
  let cancelled = false;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const tick = async (): Promise<void> => {
    const view = await probe();
    if (cancelled) {
      return;
    }
    onView(view);
    const delay = nextDelayMs(view);
    if (delay !== null) {
      timer = setTimeout(() => void tick(), delay);
    }
  };

  void tick();

  return () => {
    cancelled = true;
    if (timer !== undefined) {
      clearTimeout(timer);
    }
  };
}
