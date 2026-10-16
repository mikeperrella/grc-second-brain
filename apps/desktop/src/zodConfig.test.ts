// jitless must be on once zodConfig is loaded, or zod probes `new Function` and the CSP reports it.
import "./zodConfig";
import { z } from "zod";
import { describe, expect, it } from "vitest";

describe("zodConfig", () => {
  it("turns jitless on", () => {
    // `z.config()` with no argument reads the live global config without changing it.
    expect(z.config().jitless).toBe(true);
  });
});
