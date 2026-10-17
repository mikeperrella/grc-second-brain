// WCAG contrast of the colour tokens against the backgrounds they are used on. It reads the REAL
// token source, src/styles.css (the `--alibi-*` values in the first `:root` block), so a value
// changed there is measured here; no colour is copied into this file.
//
// What it enforces, and nothing more: 4.5:1 for normal text, 3:1 for large text, and 3:1 for UI
// borders and the focus ring (WCAG 2.2 AA). The 7:1 target for status words and fixed statements
// in docs/DESIGN.md 3.1 is NOT enforced and not measured here. One colour scheme exists today; a
// dark scheme would need its own pairs.
//
// The pairs below follow how the components use the tokens (text-*, bg-*, border-* classes in
// src/components, and the body and focus rules in styles.css). A token that no pair covers fails
// the coverage test, so a new colour cannot slip in unchecked.
//
// It lives in scripts/ (a Node context) because src/ is a webview context with no Node globals
// (scripts/check-eslint-scope.mjs). Run from apps/desktop by `pnpm test`.

import fs from "node:fs";
import { describe, expect, it } from "vitest";

const STYLES = "src/styles.css";

/** The minimum ratio for each kind of pair (WCAG 2.2 AA). */
const MINIMUM = { text: 4.5, large: 3, ui: 3 };

/** The `--alibi-*` colours (#rrggbb) of the first `:root` block, by name without the prefix. */
function readTokens(css) {
  const root = /:root\s*\{([^}]*)\}/.exec(css);
  const tokens = new Map();
  if (root === null) {
    return tokens;
  }
  for (const match of (root[1] ?? "").matchAll(/--alibi-([a-z-]+)\s*:\s*(#[0-9a-fA-F]{6})\s*;/g)) {
    tokens.set(match[1], match[2]);
  }
  return tokens;
}

/** WCAG relative luminance of #rrggbb. */
function luminance(hex) {
  const channel = (offset) => {
    const value = parseInt(hex.slice(offset, offset + 2), 16) / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
}

/** WCAG contrast ratio of two colours, 1 to 21, whichever is lighter. */
function ratio(a, b) {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (light + 0.05) / (dark + 0.05);
}

const PAGE = ["surface", "surface-raised"];

/** [foreground token, background token, kind, where it is used] */
const PAIRS = [
  ...PAGE.flatMap((bg) =>
    ["text", "text-muted", "status-supported", "status-partial", "status-needs-review", "status-error"].map(
      (fg) => [fg, bg, "text", "body, secondary and status text"],
    ),
  ),
  ["statement-text", "statement-bg", "text", "fixed statements"],
  ["badge-text", "badge-bg", "text", "unreviewed-draft badge"],
  ["edited-text", "edited-bg", "text", "edited-cell lines"],
  ["text", "surface", "large", "screen and section headings (text-xl, text-lg)"],
  ...[...PAGE, "statement-bg", "badge-bg", "edited-bg"].map((bg) => ["border", bg, "ui", "borders"]),
  ...PAGE.map((bg) => ["focus-ring", bg, "ui", "keyboard focus outline"]),
];

const tokens = readTokens(fs.readFileSync(STYLES, "utf8"));

describe("contrast formula", () => {
  it("black on white is 21, equal colours 1, #767676 on white about 4.54, red on white about 4.0", () => {
    expect(ratio("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(ratio("#123456", "#123456")).toBeCloseTo(1, 5);
    expect(ratio("#767676", "#ffffff")).toBeCloseTo(4.54, 2);
    expect(ratio("#ff0000", "#ffffff")).toBeCloseTo(4.0, 1);
  });
});

describe("colour tokens (src/styles.css)", () => {
  it("reads the colour tokens from the real stylesheet", () => {
    expect(tokens.size).toBeGreaterThanOrEqual(15);
    expect(tokens.get("surface")).toMatch(/^#[0-9a-fA-F]{6}$/);
  });

  it("every pair meets its minimum contrast", () => {
    const rows = [];
    const failures = [];
    for (const [fg, bg, kind, use] of PAIRS) {
      const foreground = tokens.get(fg);
      const background = tokens.get(bg);
      if (foreground === undefined || background === undefined) {
        failures.push(`${fg} on ${bg}: a token is missing from ${STYLES} (${use})`);
        continue;
      }
      const measured = ratio(foreground, background);
      rows.push(`${kind.padEnd(5)} ${fg} ${foreground} on ${bg} ${background}: ${measured.toFixed(2)}:1 (min ${MINIMUM[kind]}:1)`);
      if (measured < MINIMUM[kind]) {
        failures.push(`${fg} ${foreground} on ${bg} ${background} is ${measured.toFixed(2)}:1, below ${MINIMUM[kind]}:1 (${kind}, ${use})`);
      }
    }
    console.log(`Measured contrast, ${rows.length} pairs:\n${rows.join("\n")}`);
    expect(PAIRS.length).toBeGreaterThan(20);
    expect(failures).toEqual([]);
  });

  it("every colour token is covered by a pair", () => {
    const covered = new Set(PAIRS.flatMap(([fg, bg]) => [fg, bg]));
    const uncovered = [...tokens.keys()].filter((name) => !covered.has(name));
    expect(uncovered).toEqual([]);
  });
});
