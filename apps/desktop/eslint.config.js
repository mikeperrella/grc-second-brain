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
    // Threat model F36-1 and F36-4, DESIGN.md 8: the window never logs (document, question and
    // answer text must not reach a console), and model and file text is rendered as React text
    // nodes only. No raw HTML sink may appear in src: the JSX prop, or any `.innerHTML` /
    // `.outerHTML` / `.insertAdjacentHTML` access. Scoped to src: the build scripts in scripts/
    // print their results to the terminal on purpose.
    files: ["src/**/*.{ts,tsx}"],
    rules: {
      "no-console": "error",
      // ADR-022 decision 3: nothing the window holds (logins, policy text, customer names) may
      // land in the webview's profile folder. scripts/noWebStorage.test.mjs also scans every src
      // file as text, which catches `window.localStorage` and the names this rule cannot see.
      "no-restricted-globals": [
        "error",
        { name: "localStorage", message: "Web storage is banned in the window (ADR-022 decision 3)." },
        { name: "sessionStorage", message: "Web storage is banned in the window (ADR-022 decision 3)." },
        { name: "indexedDB", message: "Web storage is banned in the window (ADR-022 decision 3)." },
      ],
      "no-restricted-syntax": [
        "error",
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message: "dangerouslySetInnerHTML is banned: show text as React text nodes (F36-1).",
        },
        {
          selector:
            "MemberExpression[property.name=/^(innerHTML|outerHTML|insertAdjacentHTML)$/]",
          message: "Raw HTML sinks are banned: show text as React text nodes (F36-1).",
        },
        {
          selector: "Property[key.name='dangerouslySetInnerHTML']",
          message: "dangerouslySetInnerHTML is banned: show text as React text nodes (F36-1).",
        },
      ],
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
