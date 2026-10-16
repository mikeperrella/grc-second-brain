// Thin hook: poll `readiness` every 5 s, and ONLY while health is `healthy` (M6_PLAN B.4). When
// health leaves `healthy` the polling stops and the view goes back to `unknown`.

import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { callRoute } from "../ipc/proxy";
import { readinessView, type ReadinessView } from "../readiness";
import { startReadinessPolling } from "../readinessPoller";

export function useReadiness(healthy: boolean): ReadinessView {
  const [view, setView] = useState<ReadinessView>({ state: "unknown" });

  useEffect(() => {
    if (!healthy) {
      setView({ state: "unknown" });
      return undefined;
    }
    return startReadinessPolling(
      async () => readinessView(await callRoute<"readiness">(invoke, { route: "readiness" })),
      setView,
    );
  }, [healthy]);

  return view;
}
