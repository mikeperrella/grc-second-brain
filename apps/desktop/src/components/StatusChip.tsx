// The evidence status as label + shape + colour together (DESIGN.md 3.2). Presentational: props
// only, so it renders to static markup in tests. The label comes from the exhaustive switch in
// status.ts; an unknown value shows an error block and is never echoed as a label (F36-2).

import { describeStatus, type StatusShape } from "../status";

const SHAPE_SIZE = 16;

function Shape({ shape }: { shape: StatusShape }) {
  // Inline SVG with attributes only (no style attribute). The three outlines differ, so they stay
  // distinguishable in forced-colors and in greyscale.
  switch (shape) {
    case "filled-circle-check":
      return (
        <svg aria-hidden="true" width={SHAPE_SIZE} height={SHAPE_SIZE} viewBox="0 0 16 16">
          <circle cx="8" cy="8" r="7" fill="currentColor" stroke="currentColor" strokeWidth="1" />
          <path d="M4.5 8.5l2.5 2.5 4.5-5" fill="none" stroke="Canvas" strokeWidth="2" />
        </svg>
      );
    case "half-circle":
      return (
        <svg aria-hidden="true" width={SHAPE_SIZE} height={SHAPE_SIZE} viewBox="0 0 16 16">
          <circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" strokeWidth="2" />
          <path d="M8 1a7 7 0 0 1 0 14z" fill="currentColor" />
        </svg>
      );
    case "triangle-exclamation":
      return (
        <svg aria-hidden="true" width={SHAPE_SIZE} height={SHAPE_SIZE} viewBox="0 0 16 16">
          <path d="M8 1.5L15 14H1z" fill="none" stroke="currentColor" strokeWidth="2" />
          <path d="M8 6v4" stroke="currentColor" strokeWidth="2" />
          <circle cx="8" cy="12" r="1" fill="currentColor" />
        </svg>
      );
  }
}

function colourClass(status: "SUPPORTED" | "PARTIAL" | "NEEDS_REVIEW"): string {
  switch (status) {
    case "SUPPORTED":
      return "text-status-supported";
    case "PARTIAL":
      return "text-status-partial";
    case "NEEDS_REVIEW":
      return "text-status-needs-review";
  }
}

export function StatusChip({ value }: { value: string }) {
  const description = describeStatus(value);
  if (description.kind === "unknown") {
    return <UnknownStatus />;
  }
  return (
    <span
      className={`inline-flex items-center gap-1 font-semibold ${colourClass(description.status)}`}
      data-status={description.status}
    >
      <Shape shape={description.shape} />
      <span>{description.label}</span>
    </span>
  );
}

/** Shown for any status value the code does not know. It never prints the value as a label. */
export function UnknownStatus() {
  return (
    <span
      role="alert"
      className="alibi-block inline-block border-2 border-status-error px-2 py-1 font-semibold text-status-error"
    >
      Unknown status: ALIBI cannot show a label for this value.
    </span>
  );
}
