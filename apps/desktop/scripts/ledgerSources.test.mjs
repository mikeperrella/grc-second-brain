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
  "src/ledgerImport.ts",
  "src/ledgerText.ts",
  "src/recall.ts",
  "src/hooks/useConfirm.ts",
  "src/hooks/useImport.ts",
  "src/hooks/useLedger.ts",
  "src/hooks/useRecall.ts",
  "src/components/ConfirmStep.tsx",
  "src/components/ImportScreen.tsx",
  "src/components/LedgerScreen.tsx",
  "src/components/RecallScreen.tsx",
  "src/components/RecallStateChip.tsx",
];

const sources = new Map(FILES.map((file) => [file, fs.readFileSync(file, "utf8")]));

/** Every window source file under src/ (tests and test helpers excluded). */
function allSources(dir = "src") {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const path = `${dir}/${entry.name}`;
    if (entry.isDirectory()) {
      out.push(...allSources(path));
    } else if (/\.(ts|tsx)$/.test(entry.name) && !/\.test\.|TestSupport\.ts$|testSupport\.ts$/.test(entry.name)) {
      out.push(path);
    }
  }
  return out;
}

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

  it("the only other routes named are the read routes and the import (65g)", () => {
    const routes = new Set();
    for (const text of sources.values()) {
      for (const match of code(text).matchAll(/route:\s*"([a-z_]+)"/g)) {
        routes.add(match[1]);
      }
    }
    expect([...routes].sort()).toEqual(
      [
        "draft_rows",
        "ledger_acknowledge",
        "ledger_customers",
        "ledger_import",
        "ledger_promises",
        "ledger_recall",
        "ledger_void",
      ].sort(),
    );
  });

  it("ledger_import with preview false is built in exactly one window file, the action module (65g)", () => {
    const withFalse = allSources().filter((file) => /preview:\s*false/.test(code(fs.readFileSync(file, "utf8"))));
    expect(withFalse).toEqual(["src/ledgerActions.ts"]);
    const actions = code(sources.get("src/ledgerActions.ts") ?? "");
    expect(actions).toMatch(/route:\s*"ledger_import",\s*\.\.\.confirmed\.target\.fields,\s*preview:\s*false/);
  });

  it("the only other ledger_import request is the preview, with preview true, in ledgerImport.ts (65g)", () => {
    const withImport = allSources().filter((file) => /route:\s*"ledger_import"/.test(code(fs.readFileSync(file, "utf8"))));
    expect(withImport.sort()).toEqual(["src/ledgerActions.ts", "src/ledgerImport.ts"]);
    const preview = code(sources.get("src/ledgerImport.ts") ?? "");
    expect(preview).toMatch(/route:\s*"ledger_import",\s*\.\.\.fields,\s*preview:\s*true/);
    // Every ledger_import request object sets `preview` to a literal, never a variable.
    let requests = 0;
    for (const file of allSources()) {
      // A request object has more fields after the route (a type position like Extract<...> does not).
      for (const match of code(fs.readFileSync(file, "utf8")).matchAll(/route:\s*"ledger_import",[^}]*\}/g)) {
        requests += 1;
        expect(match[0], file).toMatch(/preview:\s*(true|false)\s*\}$/);
      }
    }
    expect(requests).toBe(2);
  });

  it("callRoute is made with ledger_import nowhere but the action module and the import hook (65g)", () => {
    for (const [file, text] of sources) {
      if (file !== "src/ledgerActions.ts" && file !== "src/hooks/useImport.ts") {
        expect(code(text), file).not.toMatch(/callRoute<"ledger_import"/);
      }
    }
    // The hook's own call is the preview: built by previewRequest, never an inline object.
    expect(code(sources.get("src/hooks/useImport.ts") ?? "")).toMatch(
      /callRoute<"ledger_import">\(invoke, previewRequest\(fields\)\)/,
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
    // 65g: the import hook reaches the real import the same way, and opens only from importTarget.
    const importHook = code(sources.get("src/hooks/useImport.ts") ?? "");
    expect(importHook).toMatch(/confirmDispatch\(\{ type: "confirm" \}\)/);
    expect(importHook).toMatch(/importPromises\(invoke, confirmed\)/);
    expect(importHook).toMatch(/const target = importTarget\(state\);/);
    expect(importHook).toMatch(/useConfirm/);
    expect(filesWith(/\bimportPromises\(/).sort()).toEqual(["src/hooks/useImport.ts", "src/ledgerActions.ts"]);
  });

  it("screens.tsx cancels each screen's confirm when another screen is shown (65f tidy-up a)", () => {
    const screens = code(fs.readFileSync("src/screens.tsx", "utf8"));
    for (const [owner, cancel] of [
      ["ledger", "cancelVoid"],
      ["recall", "cancelAcknowledge"],
      ["import", "cancelImport"],
    ]) {
      expect(screens, owner).toMatch(
        new RegExp(`if \\(confirmEventOnScreen<ScreenId>\\("${owner}", screen\\) !== null\\) \\{\\s*${cancel}\\(\\);`),
      );
    }
    expect(screens).toMatch(/\}, \[screen, cancelVoid, cancelAcknowledge, cancelImport\]\);/);
  });

  it("the recall hook keeps one copy of the customer: the store's (65f tidy-up c)", () => {
    const hook = code(sources.get("src/hooks/useRecall.ts") ?? "");
    expect(hook).toMatch(/useState\(createRecallStore\)/);
    expect(hook).toMatch(/const request = store\.request\(\);/);
    expect(hook).toMatch(/callRoute<"ledger_recall">\(invoke, request\)/);
    // No ref or other variable holds a customer beside the store.
    expect(hook).not.toMatch(/useRef|customerRef|useReducer/);
  });

  it("ledgerText.ts says which constants are approved and keeps every 65g one in its DRAFT block (65f tidy-up d)", () => {
    const text = (sources.get("src/ledgerText.ts") ?? "").replace(/\r\n/g, "\n");
    const header = text.split("\n").slice(0, 12).join("\n");
    expect(header).toContain("The 65f constants in this file were approved by the owner on 2026-10-19");
    expect(header).toContain("any constant added\n// in 65g is a DRAFT until the owner approves it");
    expect(text).not.toContain("EVERY constant in this file is a DRAFT");
    const start = text.indexOf("// ===== 65g DRAFT ");
    const end = text.indexOf("// ===== end of 65g DRAFT ");
    expect(start).toBeGreaterThan(0);
    expect(end).toBeGreaterThan(start);
    expect(text.indexOf("// ===== 65g DRAFT ", start + 1)).toBe(-1);
    // Nothing is exported after the block, and every 65g name is inside it.
    expect(text.slice(end)).not.toMatch(/export const/);
    const newNames = /^(IMPORT_|NAV_LABEL_IMPORT$|CUSTOMER_(EXISTS|UNKNOWN|NAME_INVALID)_TEXT$)/;
    let inside = 0;
    for (const match of text.matchAll(/^export const ([A-Z0-9_]+)/gm)) {
      const name = match[1];
      const at = match.index ?? 0;
      if (newNames.test(name)) {
        expect(at > start && at < end, name).toBe(true);
        inside += 1;
      } else {
        expect(at < start, `${name} is a 65f constant and must stay above the 65g block`).toBe(true);
      }
    }
    expect(inside).toBeGreaterThan(40);
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
