// Chunk 65f (ADR-022 decision 1, threat model F40): the Ledger and Recall screens' sources. This reads
// their own files and proves three things about them: each writing route is named in exactly one
// action module and only through the confirm path, the window's code never builds a dialog or a
// link, and no locale formatting or truncation touches what the sidecar sent.
//
// It lives in scripts/ (a Node context) because src/ is a webview context with no Node globals
// (scripts/check-eslint-scope.mjs). Run from apps/desktop by `pnpm test`.

import fs from "node:fs";
import { describe, expect, it } from "vitest";

const FILES = [
  "src/confirm.ts",
  "src/ledger.ts",
  "src/ledgerActions.ts",
  "src/ledgerText.ts",
  "src/recall.ts",
  "src/hooks/useConfirm.ts",
  "src/hooks/useLedger.ts",
  "src/hooks/useRecall.ts",
  "src/components/ConfirmStep.tsx",
  "src/components/LedgerScreen.tsx",
  "src/components/RecallScreen.tsx",
  "src/components/RecallStateChip.tsx",
];

const sources = new Map(FILES.map((file) => [file, fs.readFileSync(file, "utf8")]));

/** The source with // and block comments removed, so the checks look at code only. */
function code(text) {
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

/** The files whose code contains `pattern`. */
function filesWith(pattern) {
  return [...sources].filter(([, text]) => pattern.test(code(text))).map(([file]) => file);
}

describe("the ledger and recall sources (F40)", () => {
  it("finds every file and they are not empty", () => {
    expect(sources.size).toBe(FILES.length);
    for (const [file, text] of sources) {
      expect(text.length, file).toBeGreaterThan(200);
    }
  });

  // A request is `route: "<name>"`; the names also appear in TYPE positions (CallResult<"...">) in
  // ledger.ts and recall.ts, which cannot send anything.
  it("a ledger_void request is built in exactly one file, the action module", () => {
    expect(filesWith(/route:\s*"ledger_void"/)).toEqual(["src/ledgerActions.ts"]);
  });

  it("a ledger_acknowledge request is built in exactly one file, the action module", () => {
    expect(filesWith(/route:\s*"ledger_acknowledge"/)).toEqual(["src/ledgerActions.ts"]);
  });

  it("callRoute is made with a write route name nowhere but the action module", () => {
    for (const [file, text] of sources) {
      if (file !== "src/ledgerActions.ts") {
        expect(code(text), file).not.toMatch(/callRoute<"ledger_(void|acknowledge)"/);
      }
    }
  });

  it("the only other routes named are the three read routes", () => {
    const routes = new Set();
    for (const text of sources.values()) {
      for (const match of code(text).matchAll(/route:\s*"([a-z_]+)"/g)) {
        routes.add(match[1]);
      }
    }
    expect([...routes].sort()).toEqual(
      ["ledger_acknowledge", "ledger_customers", "ledger_promises", "ledger_recall", "ledger_void"].sort(),
    );
  });

  it("each hook reaches a write only through the confirm event and the Confirmed value", () => {
    const ledgerHook = code(sources.get("src/hooks/useLedger.ts") ?? "");
    const recallHook = code(sources.get("src/hooks/useRecall.ts") ?? "");
    expect(ledgerHook).toMatch(/confirmDispatch\(\{ type: "confirm" \}\)/);
    expect(ledgerHook).toMatch(/voidPromise\(invoke, confirmed\)/);
    expect(recallHook).toMatch(/confirmDispatch\(\{ type: "confirm" \}\)/);
    expect(recallHook).toMatch(/acknowledgePromise\(invoke, confirmed\)/);
    // Nothing else calls a write function, and the write functions are named in no component.
    expect(filesWith(/\bvoidPromise\(/).sort()).toEqual(["src/hooks/useLedger.ts", "src/ledgerActions.ts"]);
    expect(filesWith(/\backnowledgePromise\(/).sort()).toEqual(["src/hooks/useRecall.ts", "src/ledgerActions.ts"]);
    // The hooks use the same confirm controller the confirm tests drive.
    expect(code(sources.get("src/hooks/useConfirm.ts") ?? "")).toMatch(/createConfirmController/);
    expect(ledgerHook).toMatch(/useConfirm/);
    expect(recallHook).toMatch(/useConfirm/);
  });

  it("only the confirm module makes a Confirmed value", () => {
    expect(filesWith(/as Confirmed</)).toEqual(["src/confirm.ts"]);
  });

  it("no component calls a write, builds a dialog, a link or a style", () => {
    for (const file of FILES.filter((name) => name.startsWith("src/components/"))) {
      const text = code(sources.get(file) ?? "");
      expect(text, file).not.toMatch(/callRoute|invoke\(/);
      expect(text, file).not.toMatch(/\bconfirm\(|window\.confirm|\balert\(|\bprompt\(/);
      expect(text, file).not.toMatch(/<a[\s>]|href=|\bstyle=|<style|dangerouslySetInnerHTML/);
      expect(text, file).not.toMatch(/\bonPaste\b|\bautoFocus\b/);
    }
  });

  it("no source formats a sidecar number or date by locale, or truncates text", () => {
    for (const [file, text] of sources) {
      const body = code(text);
      expect(body, file).not.toMatch(/Intl\.|toLocale/);
      expect(body, file).not.toMatch(/\btruncate\b|line-clamp/);
      expect(body, file).not.toMatch(/console\./);
      expect(body, file).not.toMatch(/localStorage|sessionStorage|indexedDB|document\.cookie/);
    }
  });

  it("no source builds a shell, a URL or a network call", () => {
    for (const [file, text] of sources) {
      const body = code(text);
      expect(body, file).not.toMatch(/\bfetch\(|XMLHttpRequest|WebSocket|https?:\/\//);
    }
  });
});
