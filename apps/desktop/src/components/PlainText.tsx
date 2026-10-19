// Untrusted text (a file name, a sheet name, a path, a model name) as a plain text node
// (docs/DESIGN.md 8). Monospace, wrapped, direction-isolated, control characters made visible.
// No markup, no link, no collapsing of fixed statements. Presentational: props only.

import { visibleControls } from "../plainText";

const TEXT_CLASS = "font-mono whitespace-pre-wrap break-words [unicode-bidi:isolate]";

/** `identifier` marks an id, a path or a code, which a page translator must leave alone. */
export function PlainText({ text, identifier = false }: { text: string; identifier?: boolean }) {
  return (
    <span className={TEXT_CLASS} translate={identifier ? "no" : undefined}>
      {visibleControls(text)}
    </span>
  );
}
