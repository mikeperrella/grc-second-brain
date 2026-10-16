// The sidecar's health as one line of fixed words (statusText.ts). Presentational: props only.

import type { HealthView } from "../ipc/health";
import { describeView, viewClass } from "../statusText";

export function HealthIndicator({ view }: { view: HealthView }) {
  return (
    <p role="status" aria-live="polite" className={viewClass(view)}>
      {describeView(view)}
    </p>
  );
}
