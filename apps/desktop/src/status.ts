// Status words come from code (docs/DESIGN.md 1 rule 1 and 3.2; threat model F36-2).
//
// An exhaustive switch maps each evidence status to a FIXED label and shape. A value that is not
// one of the three is `unknown`: the window shows an error block for it and never a label, and
// never the value itself as a label. The label strings are interim; chunk 6h fixes the final
// words, and status.test.ts pins these so a change is a visible test edit.

export const EVIDENCE_STATUSES = ["SUPPORTED", "PARTIAL", "NEEDS_REVIEW"] as const;
export type EvidenceStatus = (typeof EVIDENCE_STATUSES)[number];

/** The three shapes of DESIGN.md 3.2. They differ by outline as well as fill. */
export type StatusShape = "filled-circle-check" | "half-circle" | "triangle-exclamation";

export type StatusDescription =
  | { kind: "known"; status: EvidenceStatus; label: string; shape: StatusShape }
  | { kind: "unknown" };

/** Label and shape for a known status. No wildcard: a new member breaks compilation here. */
export function describeKnownStatus(status: EvidenceStatus): {
  label: string;
  shape: StatusShape;
} {
  switch (status) {
    case "SUPPORTED":
      return { label: "Supported", shape: "filled-circle-check" };
    case "PARTIAL":
      return { label: "Partial", shape: "half-circle" };
    case "NEEDS_REVIEW":
      return { label: "Needs review", shape: "triangle-exclamation" };
    default:
      return assertNever(status);
  }
}

function assertNever(value: never): never {
  // Reached only if the type and the switch disagree at run time; the caller maps any value that
  // is not one of the three to `unknown` before getting here.
  throw new Error(`unhandled status: ${String(value)}`);
}

function isEvidenceStatus(value: string): value is EvidenceStatus {
  return (EVIDENCE_STATUSES as readonly string[]).includes(value);
}

/** Describe a status string from the sidecar. Never throws; an unknown value is `unknown`. */
export function describeStatus(value: string): StatusDescription {
  if (!isEvidenceStatus(value)) {
    return { kind: "unknown" };
  }
  return { kind: "known", status: value, ...describeKnownStatus(value) };
}
