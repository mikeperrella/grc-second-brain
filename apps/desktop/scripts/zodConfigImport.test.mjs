// Every source file that builds a zod schema must load src/zodConfig.ts first, so `jitless` is set
// before zod's eval probe runs (it is cached on the first object schema). This reads the sources
// and checks the FIRST import statement of each file that imports zod at runtime.
//
// It lives in scripts/ (a Node context) because src/ is a webview context with no Node globals
// (scripts/check-eslint-scope.mjs). Run from apps/desktop by `pnpm test`.

import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const SRC = "src";

/** Every non-test .ts and .tsx file under src/, as forward-slash paths. */
function sourceFiles(dir) {
  const found = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.posix.join(dir, entry.name);
    if (entry.isDirectory()) {
      found.push(...sourceFiles(full));
    } else if (/\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name) && !entry.name.endsWith(".d.ts")) {
      found.push(full);
    }
  }
  return found;
}

/** The source with block and whole-line // comments removed, so the checks look at code only. */
function code(text) {
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

/** Every import statement in order: `import ... from "x"`, `import "x"`, and `import type ...`. */
function imports(text) {
  const found = [];
  for (const match of code(text).matchAll(/^import\s+(type\s+)?([^;]*?)\s*;?\s*$/gm)) {
    const statement = match[0];
    const spec = /["']([^"']+)["']\s*;?\s*$/.exec(statement);
    if (spec) {
      found.push({ typeOnly: match[1] !== undefined, module: spec[1] });
    }
  }
  return found;
}

describe("zodConfig is loaded first (jitless before any schema)", () => {
  it("src/zodConfig.ts exists", () => {
    expect(fs.existsSync(path.posix.join(SRC, "zodConfig.ts"))).toBe(true);
  });

  const files = sourceFiles(SRC);
  const zodFiles = files.filter((file) => {
    // zodConfig.ts is the one file that imports zod without importing itself.
    if (file === path.posix.join(SRC, "zodConfig.ts")) return false;
    return imports(fs.readFileSync(file, "utf8")).some((i) => i.module === "zod" && !i.typeOnly);
  });

  it("finds the files that import zod at runtime", () => {
    expect(files.length).toBeGreaterThan(10);
    expect(zodFiles.length).toBeGreaterThan(0);
  });

  for (const file of zodFiles) {
    it(`${file}: the first import is zodConfig`, () => {
      const first = imports(fs.readFileSync(file, "utf8"))[0];
      expect(first).toBeDefined();
      const expected = path.posix.relative(path.posix.dirname(file), path.posix.join(SRC, "zodConfig"));
      const withDot = expected.startsWith(".") ? expected : `./${expected}`;
      expect(first?.module).toBe(withDot);
      expect(first?.typeOnly).toBe(false);
    });
  }
});
