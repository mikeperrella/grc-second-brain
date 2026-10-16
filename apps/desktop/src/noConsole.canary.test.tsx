// F36-4 (G29, frontend half): the window never logs. A spy on every console method must record
// zero calls while the IPC clients run through their failure paths and every presentational
// component renders. ESLint `no-console` bans the calls statically (scripts/eslint-rules.test.mjs);
// this is the run-time canary. Hostile values carry a marker that must not appear anywhere.

import { renderToStaticMarkup } from "react-dom/server";
import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";
import { FoldersSummary } from "./components/FoldersSummary";
import { ReadinessIndicator } from "./components/ReadinessIndicator";
import { Shell } from "./components/Shell";
import { StatusBar } from "./components/StatusBar";
import { StatusChip } from "./components/StatusChip";
import { getFolders, listFiles, pickFolder } from "./ipc/folders";
import { fetchHealth } from "./ipc/health";
import { callRoute, type Invoke } from "./ipc/proxy";
import { readinessView } from "./readiness";
import { summarizeFolders } from "./foldersSummary";

const METHODS = ["log", "info", "warn", "error", "debug", "trace", "dir", "table", "group"] as const;
const MARKER = "SECRET-DOCUMENT-TEXT";

describe("no console output", () => {
  const spies = METHODS.map((method) => vi.spyOn(console, method).mockImplementation(() => undefined));

  beforeEach(() => {
    for (const spy of spies) {
      spy.mockClear();
    }
  });
  afterAll(() => {
    for (const spy of spies) {
      spy.mockRestore();
    }
  });

  /** Called at the END of each test, so a call made inside it is seen before the next clear. */
  function expectSilent(): void {
    for (const [index, spy] of spies.entries()) {
      expect(spy, METHODS[index]).not.toHaveBeenCalled();
    }
  }

  it("is silent through every failure path of the clients", async () => {
    const hostile = { get status(): number { throw new Error(MARKER); } };
    const invokes: Invoke[] = [
      () => Promise.reject(MARKER),
      () => Promise.reject({ error: "new_word", detail: MARKER }),
      () => Promise.resolve({ detail: MARKER }),
      () => Promise.resolve(hostile),
      () => Promise.reject(hostile),
      () => {
        throw new Error(MARKER);
      },
    ];
    for (const invoke of invokes) {
      await callRoute<"readiness">(invoke, { route: "readiness" });
      await callRoute<"draft_rows">(invoke, { route: "draft_rows", file: "a.xlsx" });
      await getFolders(invoke);
      await pickFolder(invoke, "vault");
      await listFiles(invoke, "exports");
      await fetchHealth(() => invoke("sidecar_health", {}));
    }
    expect(readinessView({ kind: "invalid_response" })).toEqual({ state: "unavailable", code: "invalid_response" });
    expect(summarizeFolders({ kind: "rejected" })).toEqual({ state: "unavailable", code: "rejected" });
    expectSilent();
  });

  it("is silent while every presentational component renders, including with hostile text", () => {
    const html = [
      renderToStaticMarkup(
        <StatusBar
          health={{ state: "healthy" }}
          readiness={{ state: "degraded", models: [MARKER, "<b>x</b>"], ollama: "bad_response" }}
          folders={{ state: "unavailable", code: "rejected" }}
        />,
      ),
      renderToStaticMarkup(<ReadinessIndicator view={{ state: "unavailable", code: "timeout" }} />),
      renderToStaticMarkup(<FoldersSummary summary={{ state: "unknown" }} />),
      renderToStaticMarkup(<StatusChip value={MARKER} />),
      renderToStaticMarkup(<Shell healthy={false} statusBar={null} />),
    ];
    // The unknown status is shown as an error block, so the marker used as a status never appears.
    expect(html[3]).not.toContain(MARKER);
    expectSilent();
  });

  it("would notice a console call (the spy is live)", () => {
    // Calls the spy itself (not console), so the file holds no console call. If the spies were
    // not attached to the console methods, `expectSilent` above could never fail.
    const first = spies[0];
    expect(first).toBeDefined();
    first?.("canary");
    expect(first).toHaveBeenCalledTimes(1);
  });
});
