// A finding's status as shape + word + colour together (docs/DESIGN.md 3.2 and 1 rule 3; threat
// model F33-9). Presentational: props only. The word comes from the exhaustive switch in
// contradictions.ts; a value outside the closed set shows an error block and is never echoed as a
// label. Neither status is ever called "detected", "confirmed" or "conflict found".

import { describeFindingStatus, type FindingShape, type FindingStatusName } from "../contradictions";
import { CONTRA_UNKNOWN_STATUS_TEXT } from "../ledgerText";

const SHAPE_SIZE = 16;

function Shape({ shape }: { shape: FindingShape }) {
  // Inline SVG with attributes only (no style attribute). The two outlines differ.
  switch (shape) {
    case "triangle-exclamation":
      return (
        <svg aria-hidden="true" width={SHAPE_SIZE} height={SHAPE_SIZE} viewBox="0 0 16 16">
          <path d="M8 1.5L15 14H1z" fill="none" stroke="currentColor" strokeWidth="2" />
          <path d="M8 6v4" stroke="currentColor" strokeWidth="2" />
          <circle cx="8" cy="12" r="1" fill="currentColor" />
        </svg>
      );
    case "half-circle":
      return (
        <svg aria-hidden="true" width={SHAPE_SIZE} height={SHAPE_SIZE} viewBox="0 0 16 16">
          <circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" strokeWidth="2" />
          <path d="M8 1a7 7 0 0 1 0 14z" fill="currentColor" />
        </svg>
      );
  }
}

function colourClass(status: FindingStatusName): string {
  switch (status) {
    case "VALUE_CONFLICT":
      return "text-status-needs-review";
    case "CANDIDATE":
      return "text-status-partial";
  }
}

export function FindingStatusChip({ value }: { value: string }) {
  const description = describeFindingStatus(value);
  if (description.kind === "unknown") {
    return <UnknownBlock text={CONTRA_UNKNOWN_STATUS_TEXT} />;
  }
  return (
    <span
      className={`inline-flex items-center gap-1 text-base font-semibold ${colourClass(description.status)}`}
      data-status={description.status}
    >
      <Shape shape={description.shape} />
      <span>{description.label}</span>
    </span>
  );
}

/** Shown for any value the code does not know. It never prints the value as a label. */
export function UnknownBlock({ text }: { text: string }) {
  return (
    <span
      role="alert"
      className="alibi-block inline-block border-2 border-status-error px-2 py-1 font-semibold text-status-error"
    >
      {text}
    </span>
  );
}
