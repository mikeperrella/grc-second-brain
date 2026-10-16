// Untrusted text shown as visible plain text (docs/DESIGN.md 8, threat model F36-1).
//
// File names, sheet names, folder paths, model names and similar come from files or the host, so
// they are data. The window renders them as React text nodes only (never as markup, never as a
// link). This helper makes control and direction-control characters VISIBLE as `[U+XXXX]` marks
// instead of letting them act: a right-to-left override cannot reorder the line, and a zero-width
// character cannot hide. Newlines and tabs stay, because `white-space: pre-wrap` shows them.
//
// The ranges are written as numbers, not as characters or regular expressions, so no hostile
// character appears in this source file.

function isControlOrDirectional(code: number): boolean {
  return (
    (code <= 0x1f && code !== 0x09 && code !== 0x0a) || // C0 controls except tab and newline
    (code >= 0x7f && code <= 0x9f) || // DEL and C1 controls
    code === 0x061c || // Arabic letter mark
    (code >= 0x200b && code <= 0x200f) || // zero-width characters and LRM/RLM
    (code >= 0x2028 && code <= 0x202e) || // line/paragraph separators and bidi embeddings/overrides
    (code >= 0x2060 && code <= 0x2069) || // word joiner, invisible operators, bidi isolates
    code === 0xfeff // byte order mark / zero-width no-break space
  );
}

/** Replace each control or direction-control character by a visible `[U+XXXX]` mark. */
export function visibleControls(text: string): string {
  let out = "";
  for (const char of text) {
    const code = char.codePointAt(0) ?? 0;
    out += isControlOrDirectional(code)
      ? `[U+${code.toString(16).toUpperCase().padStart(4, "0")}]`
      : char;
  }
  return out;
}
