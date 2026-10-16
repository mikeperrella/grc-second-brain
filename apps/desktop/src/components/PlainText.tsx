// Untrusted text (a file name, a sheet name, a path, a model name) as a plain text node
// (docs/DESIGN.md 8). Monospace, wrapped, direction-isolated, control characters made visible.
// No markup, no link, no collapsing of fixed statements. Presentational: props only.

import { visibleControls } from "../plainText";

const TEXT_CLASS = "font-mono whitespace-pre-wrap break-words [unicode-bidi:isolate]";

export function PlainText({ text }: { text: string }) {
  return <span className={TEXT_CLASS}>{visibleControls(text)}</span>;
}
