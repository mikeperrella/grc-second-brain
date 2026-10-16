// Long untrusted text (a question, an answer, a quote) behind a "show more" control (docs/DESIGN.md
// 8). Never used for fixed statements, status words or reasons: those are always shown in full.
// The shown text is a plain text node through PlainText, with control characters made visible.

import { useState } from "react";
import { PlainText } from "./PlainText";

/** Longer than this many characters (code points) starts collapsed. */
export const COLLAPSE_AFTER = 300;

/** The first `limit` code points of `text`, so a character is never cut in half. */
export function firstPart(text: string, limit: number): string {
  return Array.from(text).slice(0, limit).join("");
}

export function isLong(text: string): boolean {
  return Array.from(text).length > COLLAPSE_AFTER;
}

export function LongText({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  if (!isLong(text)) {
    return <PlainText text={text} />;
  }
  return (
    <span className="grid gap-1">
      <PlainText text={open ? text : `${firstPart(text, COLLAPSE_AFTER)}...`} />
      <button
        type="button"
        className="w-fit border border-border px-2 py-1 text-sm"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? "Show less" : "Show more"}
      </button>
    </span>
  );
}
