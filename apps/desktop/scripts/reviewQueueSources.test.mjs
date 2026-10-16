// F37-3: the review queue reads and nothing else. This reads the queue's own source files and
// proves they can reach the sidecar and the host in exactly two ways, both read only:
// `draft_rows` through the proxy, and `folder_list_files` for the exports folder. There is no
// approve control and no call that could write a workbook (ADR-021 decision 7, CLAUDE.md 11).
//
// It lives in scripts/ (a Node context) because src/ is a webview context with no Node globals
// (scripts/check-eslint-scope.mjs). Run from apps/desktop by `pnpm test`.

import fs from "node:fs";
import { describe, expect, it } from "vitest";

const QUEUE_FILES = [
  "src/reviewQueue.ts",
  "src/hooks/useReviewQueue.ts",
  "src/components/ReviewQueueScreen.tsx",
  "src/components/DraftRowView.tsx",
  "src/components/LongText.tsx",
];

const sources = new Map(QUEUE_FILES.map((file) => [file, fs.readFileSync(file, "utf8")]));

/** The source with // and block comments removed, so the checks look at code only. */
function code(text) {
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

describe("the review queue's sources (F37-3)", () => {
  it("finds every queue file and they are not empty", () => {
    expect(sources.size).toBe(QUEUE_FILES.length);
    for (const [file, text] of sources) {
      expect(text.length, file).toBeGreaterThan(200);
    }
  });

  it("the only proxy route named anywhere in the queue is draft_rows", () => {
    const routes = [];
    for (const text of sources.values()) {
      for (const match of code(text).matchAll(/route:\s*"([a-z_]+)"/g)) {
        routes.push(match[1]);
      }
    }
    expect(routes).toEqual(["draft_rows"]);
  });

  it("the only host call is listFiles for the exports folder", () => {
    const hook = code(sources.get("src/hooks/useReviewQueue.ts"));
    const hostCalls = [...hook.matchAll(/\b(listFiles|getFolders|pickFolder)\(\s*invoke\s*,\s*("[a-z]+")?/g)];
    expect(hostCalls.map((m) => [m[1], m[2]])).toEqual([["listFiles", '"exports"']]);
    // The only import from the host client is listFiles; the only one from the proxy is callRoute.
    expect([...hook.matchAll(/import\s*\{([^}]*)\}\s*from\s*"\.\.\/ipc\/folders"/g)].map((m) => m[1].trim())).toEqual([
      "listFiles",
    ]);
    expect([...hook.matchAll(/import\s*\{([^}]*)\}\s*from\s*"\.\.\/ipc\/proxy"/g)].map((m) => m[1].trim())).toEqual([
      "callRoute",
    ]);
    // `invoke` is never called by the queue itself, only handed to the two clients.
    expect(hook).not.toMatch(/\binvoke\(/);
    expect(hook.match(/\bcallRoute</g)).toHaveLength(1);
  });

  it("no queue file imports a route or command that starts, indexes, picks or writes", () => {
    for (const [file, text] of sources) {
      const body = code(text);
      expect(body, file).not.toMatch(/questionnaire_start|questionnaire_status|questionnaire_inspect|vault_index|readiness/);
      expect(body, file).not.toMatch(/folder_pick|folder_get|pickFolder|getFolders/);
      expect(body, file).not.toMatch(/\b(save|write|upload|delete|remove)[A-Za-z]*\s*\(/i);
      expect(body, file).not.toMatch(/\bfetch\s*\(|XMLHttpRequest|WebSocket|navigator\.|localStorage|sessionStorage/);
    }
  });

  it("no approve control: no 'approve' word as code or label, no form field, no link", () => {
    for (const [file, text] of sources) {
      const body = code(text);
      expect(body, file).not.toMatch(/\bapprov(e|es|ed)\b/i);
      expect(body, file).not.toMatch(/<(input|select|textarea|form|a)[\s>]/);
      expect(body, file).not.toMatch(/\bhref\b|\bonSubmit\b/);
    }
  });

  it("there are exactly two buttons: Open this draft, and Show more / Show less", () => {
    const all = [...sources.values()].map(code).join("\n");
    expect(all.match(/<button\b/g)).toHaveLength(2);
    expect(all).toContain("Open this draft");
    expect(all).toContain('"Show less" : "Show more"');
  });

  it("a harmless-looking approve control would be caught (the scan is live)", () => {
    const planted = code('<button type="button" onClick={onApprove}>Approve</button>');
    expect(/\bapprov(e|es|ed)\b/i.test(planted)).toBe(true);
    const plantedRoute = code('callRoute(invoke, { route: "questionnaire_start" })');
    expect(plantedRoute).toMatch(/route:\s*"([a-z_]+)"/);
  });
});
