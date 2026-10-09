import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import "./App.css";
import { fetchHealth, type HealthView } from "./ipc/health";
import { startHealthPolling } from "./ipc/healthPoller";
import { describeView, viewClass } from "./statusText";

function App() {
  const [view, setView] = useState<HealthView>({ state: "starting" });

  // Every result enters as `unknown` and is parsed by `fetchHealth` (Zod) before use. The
  // cleanup stops the polling, which also covers React StrictMode mounting the effect twice in dev.
  useEffect(
    () =>
      startHealthPolling(
        () => fetchHealth(() => invoke<unknown>("sidecar_health")),
        setView,
      ),
    [],
  );

  return (
    <main className="container">
      <h1>GRC Second Brain</h1>
      <p>Local-first, air-gapped GRC workspace.</p>
      <p role="status" aria-live="polite" className={viewClass(view)}>
        {describeView(view)}
      </p>
    </main>
  );
}

export default App;
