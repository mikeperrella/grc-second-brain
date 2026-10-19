// A recall state as shape + word + colour together (docs/DESIGN.md 3.2 and 1 rule 3; threat model
// F31-4b). Presentational: props only. The word comes from the exhaustive switch in recall.ts; a
// value outside the closed set shows an error block and is never echoed as a label.
//
// "Intact" is neutral text, not green: the word means the cited lines are unchanged, not that the
// promise is true, so it must not look like an all-clear.

import { describeRecallState, type RecallShape, type RecallStateName } from "../recall";
import { UNKNOWN_STATE_TEXT } from "../ledgerText";

const SHAPE_SIZE = 16;

function Shape({ shape }: { shape: RecallShape }) {
  // Inline SVG with attributes only (no style attribute). The four outlines differ.
  switch (shape) {
    case "ring":
      return (
        <svg aria-hidden="true" width={SHAPE_SIZE} height={SHAPE_SIZE} viewBox="0 0 16 16">
          <circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" strokeWidth="2" />
        </svg>
      );
    case "diamond":
      return (
        <svg aria-hidden="true" width={SHAPE_SIZE} height={SHAPE_SIZE} viewBox="0 0 16 16">
          <path d="M8 1L15 8L8 15L1 8z" fill="none" stroke="currentColor" strokeWidth="2" />
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
    case "square-cross":
      return (
        <svg aria-hidden="true" width={SHAPE_SIZE} height={SHAPE_SIZE} viewBox="0 0 16 16">
          <rect x="1.5" y="1.5" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2" />
          <path d="M5 5l6 6M11 5l-6 6" stroke="currentColor" strokeWidth="2" />
        </svg>
      );
  }
}

function colourClass(state: RecallStateName): string {
  switch (state) {
    case "intact":
      return "text-text";
    case "moved":
      return "text-status-partial";
    case "changed":
      return "text-status-needs-review";
    case "missing":
      return "text-status-needs-review";
  }
}

export function RecallStateChip({ value }: { value: string }) {
  const description = describeRecallState(value);
  if (description.kind === "unknown") {
    return <UnknownRecallState />;
  }
  return (
    <span
      className={`inline-flex items-center gap-1 text-base font-semibold ${colourClass(description.state)}`}
      data-state={description.state}
    >
      <Shape shape={description.shape} />
      <span>{description.label}</span>
    </span>
  );
}

/** Shown for any state value the code does not know. It never prints the value as a label. */
export function UnknownRecallState() {
  return (
    <span
      role="alert"
      className="alibi-block inline-block border-2 border-status-error px-2 py-1 font-semibold text-status-error"
    >
      {UNKNOWN_STATE_TEXT}
    </span>
  );
}
