// Lint guard: Node globals (process, require, ...) must be known only to the build scripts in
// scripts/, never to the app code in src/. The app runs in a webview, where they do not exist.
// ESLint's `no-undef` cannot enforce this: typescript-eslint's recommended config switches it off
// for TypeScript files, so a leaked Node global would not show up as a lint error. This asks ESLint
// which globals it applies to each file and fails if the scopes are wrong.
// Runs from apps/desktop (it is the last step of `pnpm lint`).

import fs from "node:fs";
import process from "node:process";
import { ESLint } from "eslint";

const NODE_ONLY = ["process", "require", "__dirname", "Buffer"];

const eslint = new ESLint();

async function globalsOf(file) {
  const config = await eslint.calculateConfigForFile(file);
  return config?.languageOptions?.globals ?? {};
}

const problems = [];

const sourceFiles = fs
  .readdirSync("src", { recursive: true, encoding: "utf8" })
  .filter((name) => /\.(ts|tsx)$/.test(name))
  .map((name) => `src/${name.replaceAll("\\", "/")}`);
if (sourceFiles.length === 0) {
  problems.push("found no files under src/ to check (the guard would check nothing)");
}
for (const file of sourceFiles) {
  const known = await globalsOf(file);
  for (const name of NODE_ONLY) {
    if (name in known) {
      problems.push(`${file} is given the Node global "${name}"`);
    }
  }
}

const scriptFiles = fs
  .readdirSync("scripts", { encoding: "utf8" })
  .filter((name) => name.endsWith(".mjs"))
  .map((name) => `scripts/${name}`);
if (scriptFiles.length === 0) {
  problems.push("found no files under scripts/ to check");
}
for (const file of scriptFiles) {
  const known = await globalsOf(file);
  for (const name of NODE_ONLY) {
    if (!(name in known)) {
      problems.push(`${file} is NOT given the Node global "${name}"`);
    }
  }
}

if (problems.length > 0) {
  console.error("ESLint global scope check FAILED:");
  for (const problem of problems) {
    console.error(`  - ${problem}`);
  }
  process.exitCode = 1;
} else {
  console.log(
    `ESLint global scope ok: ${sourceFiles.length} src files have no Node globals, ` +
      `${scriptFiles.length} scripts have them.`,
  );
}
