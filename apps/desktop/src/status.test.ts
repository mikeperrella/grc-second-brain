import { describe, expect, it } from "vitest";
import {
  EVIDENCE_STATUSES,
  describeKnownStatus,
  describeStatus,
  type EvidenceStatus,
} from "./status";

describe("describeStatus: the three statuses (DESIGN.md 3.2)", () => {
  it("gives each its own fixed label and shape", () => {
    expect(describeStatus("SUPPORTED")).toEqual({
      kind: "known",
      status: "SUPPORTED",
      label: "Supported",
      shape: "filled-circle-check",
    });
    expect(describeStatus("PARTIAL")).toEqual({
      kind: "known",
      status: "PARTIAL",
      label: "Partial",
      shape: "half-circle",
    });
    expect(describeStatus("NEEDS_REVIEW")).toEqual({
      kind: "known",
      status: "NEEDS_REVIEW",
      label: "Needs review",
      shape: "triangle-exclamation",
    });
  });

  it("gives every status a distinct label and a distinct shape (never colour alone)", () => {
    const parts = EVIDENCE_STATUSES.map((status) => describeKnownStatus(status));
    expect(new Set(parts.map((part) => part.label)).size).toBe(EVIDENCE_STATUSES.length);
    expect(new Set(parts.map((part) => part.shape)).size).toBe(EVIDENCE_STATUSES.length);
  });

  it("covers exactly three statuses; adding one is a visible test edit", () => {
    // A mutation proof adds a member to EvidenceStatus: typecheck then fails at the exhaustive
    // switch, and this pin fails too.
    expect([...EVIDENCE_STATUSES]).toEqual(["SUPPORTED", "PARTIAL", "NEEDS_REVIEW"]);
    const all: EvidenceStatus[] = [...EVIDENCE_STATUSES];
    for (const status of all) {
      expect(describeStatus(status).kind).toBe("known");
    }
  });
});

describe("describeStatus: an unknown value is never a label (F36-2)", () => {
  const unknowns = [
    "",
    "supported",
    "Supported",
    " SUPPORTED",
    "SUPPORTED ",
    "APPROVED",
    "VERIFIED",
    "OK",
    "NEEDS-REVIEW",
    "<b>SUPPORTED</b>",
    "SUPPORTED\u202e",
    "constructor",
    "__proto__",
    "toString",
  ];

  it("returns unknown for each, with nothing of the value in the result", () => {
    for (const value of unknowns) {
      const result = describeStatus(value);
      expect(result, JSON.stringify(value)).toEqual({ kind: "unknown" });
    }
  });
});
