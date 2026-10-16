// Proves the lint bans are LIVE in the real config (threat model F36-1 and F36-4): it asks ESLint,
// with eslint.config.js as it is on disk, to lint a snippet that breaks each rule, and requires the
// rule to fire. If a rule is removed or weakened in the config, a test here fails.
//
// It lives in scripts/ (a Node context) because src/ is a webview context with no Node globals
// (scripts/check-eslint-scope.mjs). Run from apps/desktop by `pnpm test`.

import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

const eslint = new ESLint();

/** Lint `code` as if it were the given file under src/. Returns the rule ids that fired. */
async function firedRules(code, filePath) {
  const results = await eslint.lintText(code, { filePath });
  return results.flatMap((result) => result.messages.map((message) => message.ruleId));
}

describe("the console ban (F36-4)", () => {
  it("rejects every console method in src", async () => {
    for (const method of ["log", "error", "warn", "info", "debug", "trace"]) {
      const fired = await firedRules(`console.${method}("x");\n`, "src/probe.ts");
      expect(fired, method).toContain("no-console");
    }
  });
});

describe("the raw HTML ban (F36-1)", () => {
  it("rejects dangerouslySetInnerHTML as a JSX prop", async () => {
    const code = 'export const A = () => <div dangerouslySetInnerHTML={{ __html: "x" }} />;\n';
    expect(await firedRules(code, "src/probe.tsx")).toContain("no-restricted-syntax");
  });

  it("rejects dangerouslySetInnerHTML passed in a props object", async () => {
    const code = 'export const props = { dangerouslySetInnerHTML: { __html: "x" } };\n';
    expect(await firedRules(code, "src/probe.ts")).toContain("no-restricted-syntax");
  });

  it("rejects innerHTML, outerHTML and insertAdjacentHTML", async () => {
    for (const code of [
      'document.body.innerHTML = "x";\n',
      'document.body.outerHTML = "x";\n',
      'document.body.insertAdjacentHTML("beforeend", "x");\n',
      "const html = document.body.innerHTML;\n",
    ]) {
      expect(await firedRules(code, "src/probe.ts"), code).toContain("no-restricted-syntax");
    }
  });

  it("does not flag ordinary text rendering", async () => {
    const code = "export const A = ({ name }: { name: string }) => <p>{name}</p>;\n";
    const fired = await firedRules(code, "src/probe.tsx");
    expect(fired).not.toContain("no-restricted-syntax");
    expect(fired).not.toContain("no-console");
  });
});
