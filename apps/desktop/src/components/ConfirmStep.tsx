// The in-page second step of an irreversible write (docs/DESIGN.md 11.2). Presentational: props only.
//
// A bordered block in the 4.1 style that restates, in fixed words, what will happen and what cannot
// be undone, and shows the target as plain text. A separate Confirm button carries out the write and
// Cancel closes the block and sends nothing. It is never `confirm()`, a tooltip or a modal. The
// statement is a constant from fixedTexts.ts, never sidecar text. In the page order the Confirm
// button comes first and Cancel second, directly after the target.

import type { ReactNode } from "react";
import { CANCEL_BUTTON, CONFIRM_TARGET_LABEL } from "../ledgerText";
import { Statement } from "./Statement";

export interface ConfirmStepProps {
  /** The fixed words that say what will happen (a constant from fixedTexts.ts). */
  statement: string;
  /** The target, as plain text nodes (PlainText), never a link. */
  target: ReactNode;
  confirmLabel: string;
  /** False while a write is going on. */
  enabled: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmStep({ statement, target, confirmLabel, enabled, onConfirm, onCancel }: ConfirmStepProps) {
  return (
    <div className="alibi-block grid min-w-0 gap-2 border-2 border-border p-2">
      <Statement>{statement}</Statement>
      <div className="min-w-0">
        <p className="font-semibold">{CONFIRM_TARGET_LABEL}:</p>
        {target}
      </div>
      <div className="flex flex-wrap gap-2">
        <button type="button" className="border border-border px-3 py-1" disabled={!enabled} onClick={onConfirm}>
          {confirmLabel}
        </button>
        <button type="button" className="border border-border px-3 py-1" onClick={onCancel}>
          {CANCEL_BUTTON}
        </button>
      </div>
    </div>
  );
}
