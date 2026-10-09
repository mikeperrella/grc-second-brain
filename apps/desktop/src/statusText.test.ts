import { describe, expect, it } from "vitest";
import type { HealthView } from "./ipc/health";
import { describeView, viewClass } from "./statusText";

describe("describeView", () => {
  it("says starting and healthy plainly", () => {
    expect(describeView({ state: "starting" })).toBe("Sidecar: starting");
    expect(describeView({ state: "healthy" })).toBe("Sidecar: healthy");
  });

  it("shows a failure as its source and fixed code only", () => {
    const view: HealthView = { state: "failed", source: "sidecar", code: "startup_rejected" };
    expect(describeView(view)).toBe("Sidecar: failed (sidecar: startup_rejected)");
    expect(describeView({ state: "failed", source: "ipc", code: "rejected" })).toBe(
      "Sidecar: failed (ipc: rejected)",
    );
    expect(
      describeView({ state: "failed", source: "health_check", code: "timeout" }),
    ).toBe("Sidecar: failed (health_check: timeout)");
  });

  it("never shows an exit code, even for an early exit", () => {
    // The view type has no field for it, so a code that arrived from Rust is gone by this point.
    const view: HealthView = { state: "failed", source: "sidecar", code: "exited_before_ready" };
    expect(describeView(view)).toBe("Sidecar: failed (sidecar: exited_before_ready)");
  });
});

describe("viewClass", () => {
  it("gives each state its own class", () => {
    expect(viewClass({ state: "starting" })).toBe("status status-starting");
    expect(viewClass({ state: "healthy" })).toBe("status status-healthy");
    expect(viewClass({ state: "failed", source: "stopped", code: "not_running" })).toBe(
      "status status-failed",
    );
  });
});
