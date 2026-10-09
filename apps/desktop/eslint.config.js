// ESLint flat config (CLAUDE.md Sections 7 and 10). `pnpm lint` passes the files to check
// explicitly, so this file needs no ignore list: nothing is excluded from linting.
import js from "@eslint/js";
import { defineConfig } from "eslint/config";
import globals from "globals";
import tseslint from "typescript-eslint";

export default defineConfig(
  js.configs.recommended,
  tseslint.configs.recommended,
  {
    languageOptions: {
      globals: globals.browser,
    },
    rules: {
      // CLAUDE.md Section 7: never use `any`. Stated here as an error, so the rule does not
      // depend on what a shared preset happens to default to.
      "@typescript-eslint/no-explicit-any": "error",
    },
  },
  {
    // Build and CI scripts run in Node. Node globals are given to these files ONLY: the app in
    // src/ runs in a webview and must not see them (scripts/check-eslint-scope.mjs enforces it).
    files: ["scripts/**/*.mjs"],
    languageOptions: {
      globals: globals.node,
    },
  },
);
