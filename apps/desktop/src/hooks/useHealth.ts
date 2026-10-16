// Thin hook: poll `sidecar_health` and hold the latest view. All decisions are in ipc/health.ts and
// ipc/healthPoller.ts, which are tested; this wiring is checked by hand.

import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { fetchHealth, type HealthView } from "../ipc/health";
import { startHealthPolling } from "../ipc/healthPoller";

export function useHealth(): HealthView {
  const [view, setView] = useState<HealthView>({ state: "starting" });

  // Every result enters as `unknown` and is parsed by `fetchHealth` (Zod) before use. The cleanup
  // stops the polling, which also covers React StrictMode mounting the effect twice in dev.
  useEffect(
    () =>
      startHealthPolling(
        () => fetchHealth(() => invoke<unknown>("sidecar_health")),
        setView,
      ),
    [],
  );

  return view;
}
