// ADR-022 decision 3: the window never uses web storage. Anything written to localStorage,
// sessionStorage, IndexedDB, the Cache API or a cookie lands in the webview's profile folder on
// disk, where ALIBI neither controls nor logs it; the M6.5 screens hold personal data (up to 200
// logins, F26-7), pasted policy text and customer names. This reads every file under src/ (test
// files excluded) as plain text, comments included, and fails on any of the banned names. The
// ESLint rule `no-restricted-globals` (proved in scripts/eslint-rules.test.mjs) is the second
// layer; this scan also catches `window.localStorage`, `caches.`, `document.cookie` and
// `navigator.storage`, which that rule cannot see.
//
// It lives in scripts/ (a Node context) because src/ is a webview context with no Node globals
// (scripts/check-eslint-scope.mjs). Run from apps/desktop by `pnpm test`.

import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const SRC = "src";

/** The banned names, each as a pattern over the raw text. */
const BANNED = [
  ["localStorage", /\blocalStorage\b/],
  ["sessionStorage", /\bsessionStorage\b/],
  ["indexedDB", /\bindexedDB\b/],
  ["caches.", /\bcaches\s*\./],
  ["document.cookie", /\bdocument\s*\.\s*cookie\b/],
  ["navigator.storage", /\bnavigator\s*\.\s*storage\b/],
];

/** The names of the banned patterns that match `text`. */
function bannedIn(text) {
  return BANNED.filter(([, pattern]) => pattern.test(text)).map(([name]) => name);
}

/** Every file under src/ except tests, as forward-slash paths. */
function sourceFiles(dir) {
  const found = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.posix.join(dir, entry.name);
    if (entry.isDirectory()) {
      found.push(...sourceFiles(full));
    } else if (!/\.test\.[a-z]+$/.test(entry.name)) {
      found.push(full);
    }
  }
  return found;
}

describe("no web storage in the window (ADR-022 decision 3)", () => {
  const files = sourceFiles(SRC);

  it("finds the window's sources, and no test file among them", () => {
    expect(files.length).toBeGreaterThan(40);
    expect(files).toContain("src/ipc/proxy.ts");
    expect(files).toContain("src/App.tsx");
    expect(files.some((file) => file.includes(".test."))).toBe(false);
  });

  for (const file of files) {
    it(`${file} names no web storage`, () => {
      expect(bannedIn(fs.readFileSync(file, "utf8"))).toEqual([]);
    });
  }

  it("each banned name is caught when planted (the scan is live)", () => {
    const planted = {
      localStorage: 'localStorage.setItem("k", "v");',
      sessionStorage: 'window.sessionStorage.getItem("k");',
      indexedDB: 'indexedDB.open("alibi");',
      "caches.": 'caches.open("alibi");',
      "document.cookie": 'document.cookie = "a=b";',
      "navigator.storage": "navigator.storage.persist();",
    };
    for (const [name, code] of Object.entries(planted)) {
      expect(bannedIn(code), name).toEqual([name]);
    }
    expect(bannedIn('window["localStorage"]')).toEqual(["localStorage"]);
  });

  it("does not flag text that only resembles a banned name", () => {
    expect(bannedIn("const cachesSeen = 1; const cookieJar = 2; storage.length;")).toEqual([]);
  });
});
