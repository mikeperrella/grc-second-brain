// F6-1b: what the window shows for each parsed state, rendered to static markup (no jsdom, no
// browser). The components are presentational and take props, so every state can be built by hand.
// Also pins the fixed statements and that model text is escaped, never markup (F36-1).

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { FoldersSummary as Summary } from "../foldersSummary";
import type { HealthView } from "../ipc/health";
import { NO_DOWNLOAD_STATEMENT, type ReadinessView } from "../readiness";
import { FoldersSummary } from "./FoldersSummary";
import { ReadinessIndicator } from "./ReadinessIndicator";
import { Shell } from "./Shell";
import { StatusBar } from "./StatusBar";
import { StatusChip, UnknownStatus } from "./StatusChip";

const STARTING: HealthView = { state: "starting" };
const HEALTHY: HealthView = { state: "healthy" };
const FAILED: HealthView = { state: "failed", source: "sidecar", code: "startup_rejected" };

const UNKNOWN_FOLDERS: Summary = { state: "unknown" };
const KNOWN_FOLDERS: Summary = {
  state: "known",
  inUse: 2,
  total: 5,
  pendingNextLaunch: ["ledger"],
  notSet: ["imports", "exports"],
  invalid: [],
};

function bar(health: HealthView, readiness: ReadinessView, folders: Summary = UNKNOWN_FOLDERS) {
  return renderToStaticMarkup(<StatusBar health={health} readiness={readiness} folders={folders} />);
}

describe("every state renders without an inline style, a script or raw HTML sink", () => {
  const states: Array<[string, string]> = [
    ["starting", bar(STARTING, { state: "unknown" })],
    ["failed", bar(FAILED, { state: "unknown" })],
    ["healthy, readiness unknown", bar(HEALTHY, { state: "unknown" })],
    ["healthy, checking", bar(HEALTHY, { state: "checking" })],
    ["healthy, ready", bar(HEALTHY, { state: "ready" }, KNOWN_FOLDERS)],
    ["healthy, degraded", bar(HEALTHY, { state: "degraded", models: ["a"], ollama: "reachable" })],
    ["healthy, unavailable", bar(HEALTHY, { state: "unavailable", code: "timeout" })],
  ];
  for (const [name, html] of states) {
    it(name, () => {
      expect(html).not.toMatch(/\sstyle=/i);
      expect(html).not.toMatch(/<style|<script|<link|javascript:/i);
      expect(html).not.toMatch(/https?:\/\//i);
      expect(html.length).toBeGreaterThan(0);
    });
  }
});

describe("health in the status bar", () => {
  it("shows starting", () => {
    expect(bar(STARTING, { state: "unknown" })).toContain("Sidecar: starting");
  });

  it("shows a failure as its source and fixed code only", () => {
    const html = bar(FAILED, { state: "unknown" });
    expect(html).toContain("Sidecar: failed (sidecar: startup_rejected)");
  });

  it("shows each of the 25 failure kinds by its code", () => {
    const kinds = [
      "timeout", "eof_before_ready", "not_json", "wrong_fields", "port_out_of_range", "line_too_long",
      "read_failed", "exited_before_ready", "startup_rejected", "exited_after_ready", "protocol_violation",
      "not_installed", "not_configured", "outside_sidecar_dir", "spawn_failed", "job_failed",
      "job_assign_failed", "unexpected_thread_state", "log_dir_unavailable", "index_dir_unavailable",
      "system_root_unavailable", "random_unavailable", "env_rejected", "http_client_failed", "internal",
    ] as const;
    expect(kinds).toHaveLength(25);
    for (const code of kinds) {
      const html = bar({ state: "failed", source: "sidecar", code }, { state: "unknown" });
      expect(html).toContain(`Sidecar: failed (sidecar: ${code})`);
    }
  });

  it("shows the other failure sources", () => {
    expect(bar({ state: "failed", source: "stopped", code: "not_running" }, { state: "unknown" })).toContain(
      "Sidecar: failed (stopped: not_running)",
    );
    expect(bar({ state: "failed", source: "ipc", code: "invalid_response" }, { state: "unknown" })).toContain(
      "Sidecar: failed (ipc: invalid_response)",
    );
    expect(bar({ state: "failed", source: "health_check", code: "timeout" }, { state: "unknown" })).toContain(
      "Sidecar: failed (health_check: timeout)",
    );
  });

  it("shows healthy", () => {
    expect(bar(HEALTHY, { state: "unknown" })).toContain("Sidecar: healthy");
  });
});

describe("readiness in the status bar", () => {
  it("says models are not checked yet while health is not healthy", () => {
    const html = bar(STARTING, { state: "unknown" });
    expect(html).toContain("Models: not checked yet");
    expect(html).not.toContain(NO_DOWNLOAD_STATEMENT);
  });

  it("shows checking and ready", () => {
    expect(bar(HEALTHY, { state: "checking" })).toContain("Models: checking");
    expect(bar(HEALTHY, { state: "ready" })).toContain("Models: ready");
  });

  it("lists the missing model names and always shows the fixed statement when degraded", () => {
    const html = bar(HEALTHY, {
      state: "degraded",
      models: ["llama3.1:8b", "nomic-embed-text"],
      ollama: "reachable",
    });
    expect(html).toContain("Models: not ready");
    expect(html).toContain("llama3.1:8b");
    expect(html).toContain("nomic-embed-text");
    expect(html).toContain("Ollama: reachable");
    expect(html).toContain("ALIBI never downloads models; install them with Ollama yourself");
  });

  it("shows the statement even when Ollama is unreachable and no name is known", () => {
    const html = bar(HEALTHY, { state: "degraded", models: [], ollama: "unreachable" });
    expect(html).toContain("Ollama: not reachable");
    expect(html).toContain(NO_DOWNLOAD_STATEMENT);
    expect(html).not.toContain("Missing models:");
  });

  it("shows a model name as escaped text, never as markup (F36-1)", () => {
    const html = bar(HEALTHY, {
      state: "degraded",
      models: ['<img src=x onerror="alert(1)">', "<b>bold</b>", "a\u202eb"],
      ollama: "reachable",
    });
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<b>bold</b>");
    expect(html).toContain("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;");
    expect(html).toContain("&lt;b&gt;bold&lt;/b&gt;");
  });

  it("shows an unavailable check by its fixed word", () => {
    expect(bar(HEALTHY, { state: "unavailable", code: "not_ready" })).toContain(
      "Models: could not be checked (<code",
    );
    expect(bar(HEALTHY, { state: "unavailable", code: "not_ready" })).toContain("not_ready");
  });

  it("keeps the fixed statement out of a tooltip: it is body text, not an attribute", () => {
    const html = renderToStaticMarkup(
      <ReadinessIndicator view={{ state: "degraded", models: ["m"], ollama: "reachable" }} />,
    );
    expect(html).not.toMatch(/title=|aria-describedby|<details|hidden/i);
    expect(html).toContain(`>${NO_DOWNLOAD_STATEMENT}<`);
  });
});

describe("folders in the status bar", () => {
  const render = (summary: Summary) => renderToStaticMarkup(<FoldersSummary summary={summary} />);

  it("says not read yet, never zero results", () => {
    expect(render(UNKNOWN_FOLDERS)).toContain("Folders: not read yet");
    expect(render(UNKNOWN_FOLDERS)).not.toMatch(/0 results/i);
  });

  it("shows counts, the next-launch note and what is not set", () => {
    const html = render(KNOWN_FOLDERS);
    expect(html).toContain("Folders: 2 of 5 in use");
    expect(html).toContain("Saved, applies the next time you start ALIBI: Ledger");
    expect(html).toContain("Not set: Imports, Exports");
  });

  it("flags a saved folder that is not valid", () => {
    const html = render({ ...KNOWN_FOLDERS, invalid: ["imports"] });
    expect(html).toContain("Saved folder is not valid: Imports");
  });

  it("shows an unreadable answer by its fixed word", () => {
    expect(render({ state: "unavailable", code: "rejected" })).toContain("Folders: could not be read");
  });
});

describe("the shell", () => {
  it("disables navigation and says why while the sidecar is not healthy", () => {
    const html = renderToStaticMarkup(<Shell healthy={false} statusBar={<p>bar</p>} />);
    expect(html).toContain("Navigation is off until the sidecar is healthy.");
    expect(html).toMatch(/<button[^>]*disabled/);
    expect(html).toContain("ALIBI");
    expect(html).toContain("<main>");
  });

  it("never offers an enabled button for a screen that is not built", () => {
    const html = renderToStaticMarkup(<Shell healthy statusBar={null} />);
    expect(html).toContain("These screens are not built yet.");
    const buttons = html.match(/<button[^>]*>/g) ?? [];
    expect(buttons.length).toBeGreaterThan(0);
    for (const button of buttons) {
      expect(button).toContain("disabled");
    }
  });
});

describe("the status chip (F36-2)", () => {
  it("shows label and shape for each status", () => {
    const supported = renderToStaticMarkup(<StatusChip value="SUPPORTED" />);
    const partial = renderToStaticMarkup(<StatusChip value="PARTIAL" />);
    const review = renderToStaticMarkup(<StatusChip value="NEEDS_REVIEW" />);
    expect(supported).toContain("Supported");
    expect(partial).toContain("Partial");
    expect(review).toContain("Needs review");
    for (const html of [supported, partial, review]) {
      expect(html).toContain("<svg");
      expect(html).not.toMatch(/\sstyle=/i);
    }
    // The three shapes differ in markup, not only in colour.
    const shapes = [supported, partial, review].map((html) => /<svg.*<\/svg>/.exec(html)?.[0]);
    expect(new Set(shapes).size).toBe(3);
  });

  it("shows an error block for an unknown value and never the value as a label", () => {
    for (const value of ["APPROVED", "", "supported", "<b>SUPPORTED</b>"]) {
      const html = renderToStaticMarkup(<StatusChip value={value} />);
      expect(html).toBe(renderToStaticMarkup(<UnknownStatus />));
      expect(html).toContain('role="alert"');
      expect(html).toContain("Unknown status");
      expect(html).not.toContain("Supported");
      if (value !== "") {
        expect(html).not.toContain(value);
      }
    }
  });
});
