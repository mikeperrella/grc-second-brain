// The window's reply schemas against the sidecar's REAL replies (M6.5 plan section 10, chunk 65e).
// sidecar/tests/test_reply_contracts.py calls every route the window uses through the real app and
// records each reply in fixtures/contracts/replies/<route>.json. This test reads those files and
// requires: one file per route in the host's proxy-contract.json and no other file; every recorded
// case (status and body) to parse with that route's schema in src/ipc/proxy.ts; every error word
// and refusal code seen to be in the window's closed sets; and the sidecar's fixed sentences to be
// pinned, so a drifted sentence makes the reply invalid (ADR-022 decision 2).
//
// It lives in scripts/ (a Node context) because it reads files with node:fs, and src/ is a webview
// context with no Node globals (scripts/check-eslint-scope.mjs). Run from apps/desktop by
// `pnpm test`. The fixture files are read, never written.

import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import * as fixed from "../src/fixedTexts.ts";
import {
  API_ERROR_WORDS,
  BLAST_RADIUS_REFUSAL_CODES,
  CHECK_REFUSAL_CODES,
  DRAFT_REFUSAL_CODES,
  ERROR_STATUSES,
  EXPORT_REFUSAL_CODES,
  GRAPH_REFUSAL_CODES,
  IMPORT_REFUSAL_CODES,
  LEDGER_REFUSAL_CODES,
  M65_API_ERROR_WORDS,
  MAPPING_REFUSAL_CODES,
  RECALL_REFUSAL_CODES,
  SUCCESS_STATUSES,
  WORKBOOK_REFUSAL_CODES,
  routeReplySchemas,
} from "../src/ipc/proxy.ts";

const REPLIES = "../../fixtures/contracts/replies";
const PROXY_CONTRACT = "src-tauri/tests/fixtures/proxy-contract.json";

const routeNames = JSON.parse(fs.readFileSync(PROXY_CONTRACT, "utf8")).route_names;
const files = fs.readdirSync(REPLIES).sort();
const recorded = new Map(
  files
    .filter((name) => name.endsWith(".json"))
    .map((name) => [name.slice(0, -".json".length), JSON.parse(fs.readFileSync(path.join(REPLIES, name), "utf8"))]),
);

/** The routes of M6 core, which keep the M6 core error union. */
const M6_CORE = new Set([
  "readiness",
  "questionnaire_inspect",
  "questionnaire_start",
  "questionnaire_status",
  "vault_index_start",
  "vault_index_status",
  "draft_rows",
]);

/** Every refusal family with a code, and its closed set of codes. */
const CODES = {
  workbook_refused: WORKBOOK_REFUSAL_CODES,
  mapping_refused: MAPPING_REFUSAL_CODES,
  draft_refused: DRAFT_REFUSAL_CODES,
  export_refused: EXPORT_REFUSAL_CODES,
  check_refused: CHECK_REFUSAL_CODES,
  ledger_refused: LEDGER_REFUSAL_CODES,
  import_refused: IMPORT_REFUSAL_CODES,
  recall_refused: RECALL_REFUSAL_CODES,
  blast_radius_refused: BLAST_RADIUS_REFUSAL_CODES,
  graph_refused: GRAPH_REFUSAL_CODES,
};

const isSuccess = (status) => SUCCESS_STATUSES.includes(status);
const parses = (route, status, body) => routeReplySchemas[route].safeParse({ status, body }).success;
const clone = (value) => JSON.parse(JSON.stringify(value));

/** Every recorded case as `[route, case]`. */
const cases = [...recorded].flatMap(([route, file]) => file.replies.map((entry) => [route, entry]));

/** The first recorded body of `route` whose case is `name`. */
function bodyOf(route, name) {
  const entry = recorded.get(route)?.replies.find((reply) => reply.case === name);
  if (entry === undefined) {
    throw new Error(`no case ${name} in ${route}`);
  }
  return entry.body;
}

describe("the recorded replies cover exactly the host's routes", () => {
  it("has one file per route in proxy-contract.json and no other file", () => {
    expect(routeNames.length).toBe(21);
    expect(files.every((name) => name.endsWith(".json"))).toBe(true);
    expect([...recorded.keys()].sort()).toEqual([...routeNames].sort());
    expect(Object.keys(routeReplySchemas).sort()).toEqual([...routeNames].sort());
  });

  it("each file names its own route and holds at least one success and one error", () => {
    for (const [route, file] of recorded) {
      expect(file.route, route).toBe(route);
      expect(Object.keys(file).sort(), route).toEqual(["replies", "route"]);
      expect(file.replies.some((entry) => isSuccess(entry.status)), `${route}: a success`).toBe(true);
      expect(file.replies.some((entry) => ERROR_STATUSES.includes(entry.status)), `${route}: an error`).toBe(true);
    }
    expect(cases.length).toBeGreaterThan(100);
  });
});

describe("every recorded reply parses with its route's schema", () => {
  for (const [route, entry] of cases) {
    it(`${route} / ${entry.case} (${entry.status})`, () => {
      expect([...SUCCESS_STATUSES, ...ERROR_STATUSES]).toContain(entry.status);
      expect(parses(route, entry.status, entry.body)).toBe(true);
    });
  }
});

describe("the closed sets hold every word the sidecar was seen to send", () => {
  const errors = cases.filter(([, entry]) => !isSuccess(entry.status));

  it("every error word is a known word for its route", () => {
    for (const [route, entry] of errors) {
      const words = M6_CORE.has(route) ? API_ERROR_WORDS : M65_API_ERROR_WORDS;
      expect(words, `${route} / ${entry.case}`).toContain(entry.body.error);
    }
  });

  it("every refusal code is in its family's closed set", () => {
    for (const [route, entry] of errors) {
      const known = CODES[entry.body.error];
      if (known !== undefined) {
        expect(known, `${route} / ${entry.case}`).toContain(entry.body.code);
      } else {
        expect(entry.body.code, `${route} / ${entry.case}`).toBeUndefined();
      }
    }
  });

  it("every M6.5 refusal family appears at least once, so the parse above is not over nothing", () => {
    const seen = new Set(errors.map(([, entry]) => entry.body.error));
    for (const family of [
      "export_refused",
      "check_refused",
      "ledger_refused",
      "import_refused",
      "recall_refused",
      "blast_radius_refused",
      "graph_refused",
      "invalid_request",
      "run_in_progress",
      "run_not_found",
    ]) {
      expect(seen, family).toContain(family);
    }
  });
});

describe("an unknown key or word makes a recorded reply invalid", () => {
  /** Add `extra` to the body, or to its first element when the body is a list. */
  function withExtra(body) {
    const copy = clone(body);
    if (Array.isArray(copy)) {
      if (copy.length > 0) {
        copy[0].unexpected = 1;
      } else {
        copy.push({ unexpected: 1 });
      }
      return copy;
    }
    copy.unexpected = 1;
    return copy;
  }

  for (const [route, entry] of cases) {
    it(`${route} / ${entry.case}: an extra key is refused`, () => {
      expect(parses(route, entry.status, withExtra(entry.body))).toBe(false);
    });
  }

  it("an unknown error word or refusal code is refused on every route", () => {
    for (const route of routeNames) {
      expect(parses(route, 409, { error: "made_up_error" }), route).toBe(false);
      expect(parses(route, 422, { error: "recall_refused", code: "made_up_code" }), route).toBe(false);
      expect(parses(route, 422, { error: "graph_refused", code: "made_up_code" }), route).toBe(false);
    }
  });

  it("a word outside a closed success set is refused", () => {
    const recall = clone(bodyOf("ledger_recall", "changed"));
    recall.items[0].state = "unchanged";
    expect(parses("ledger_recall", 200, recall)).toBe(false);
    const blast = clone(bodyOf("ledger_blast_radius", "proposed_text"));
    blast.items[0].flags_now = ["made_up_flag"];
    expect(parses("ledger_blast_radius", 200, blast)).toBe(false);
    const run = clone(bodyOf("contradictions_status", "complete"));
    run.report.findings[0].status = "CONFIRMED";
    expect(parses("contradictions_status", 200, run)).toBe(false);
    const graph = clone(bodyOf("vault_graph", "with_ledger"));
    graph.edges[0].origin = "similarity";
    expect(parses("vault_graph", 200, graph)).toBe(false);
    const done = clone(bodyOf("ledger_void", "ok"));
    done.kind = "acknowledged";
    expect(parses("ledger_void", 200, done)).toBe(false);
  });

  it("a promise id or customer id of the wrong shape is refused", () => {
    const page = clone(bodyOf("ledger_promises", "page"));
    page.promises[0].promise_id = page.promises[0].promise_id.toUpperCase().replace("0", "A");
    expect(parses("ledger_promises", 200, page)).toBe(false);
    const customers = clone(bodyOf("ledger_customers", "one_customer"));
    customers[0].customer_id = "not-an-id";
    expect(parses("ledger_customers", 200, customers)).toBe(false);
  });
});

// Each fixed sentence: the route, the case, where in the body it is, and the window's constant.
const PINNED = [
  ["ledger_recall", "intact", ["statement"], "RECALL_STATEMENT"],
  ["ledger_recall", "intact", ["scope"], "RECALL_SCOPE"],
  ["ledger_recall", "intact", ["items", 0, "statement"], "RECALL_STATEMENT"],
  ["ledger_recall", "intact", ["items", 0, "scope"], "RECALL_SCOPE"],
  ["ledger_blast_radius", "proposed_text", ["statement"], "RECALL_STATEMENT"],
  ["ledger_blast_radius", "proposed_text", ["scope"], "RECALL_SCOPE"],
  ["ledger_blast_radius", "proposed_text", ["coverage"], "BLAST_COVERAGE"],
  ["ledger_blast_radius", "proposed_text", ["preview"], "BLAST_PREVIEW_LINE"],
  ["ledger_blast_radius", "proposed_text", ["items", 0, "statement"], "RECALL_STATEMENT"],
  ["ledger_blast_radius", "proposed_text", ["items", 0, "scope"], "RECALL_SCOPE"],
  ["ledger_blast_radius", "proposed_text", ["items", 0, "preview"], "BLAST_PREVIEW_LINE"],
  ["ledger_blast_radius", "no_match", ["note"], "BLAST_NO_MATCH_LINE"],
  ["reality_check", "ok", ["statement"], "REALITY_STATEMENT"],
  ["reality_check", "ok", ["staleness_note"], "REALITY_STALENESS_NOTE"],
  ["reality_check", "ok", ["duplicate_note"], "REALITY_DUPLICATE_NOTE"],
  ["contradictions_status", "complete", ["report", "says"], "CONTRADICTIONS_NUMERIC_ONLY_STATEMENT"],
  ["vault_graph", "with_ledger", ["says"], "GRAPH_SAYS"],
];

function readAt(body, keys) {
  return keys.reduce((value, key) => value[key], body);
}

function writeAt(body, keys, text) {
  const parent = readAt(body, keys.slice(0, -1));
  parent[keys[keys.length - 1]] = text;
}

describe("the sidecar's fixed sentences are pinned (ADR-022 decision 2)", () => {
  for (const [route, name, keys, constant] of PINNED) {
    const where = `${route} / ${name} / ${keys.join(".")}`;

    it(`${where} equals ${constant} in src/fixedTexts.ts`, () => {
      expect(typeof fixed[constant]).toBe("string");
      expect(readAt(bodyOf(route, name), keys)).toBe(fixed[constant]);
    });

    it(`${where}: a drifted sentence makes the reply invalid`, () => {
      const status = recorded.get(route).replies.find((entry) => entry.case === name).status;
      for (const drift of [`${fixed[constant]} `, fixed[constant].toUpperCase(), fixed[constant].slice(0, -1), ""]) {
        const body = clone(bodyOf(route, name));
        writeAt(body, keys, drift);
        expect(parses(route, status, body), JSON.stringify(drift)).toBe(false);
      }
    });
  }

  it("the no-match note may also be null, and nothing else", () => {
    const body = clone(bodyOf("ledger_blast_radius", "proposed_text"));
    expect(body.note).toBeNull();
    expect(parses("ledger_blast_radius", 200, body)).toBe(true);
    body.note = fixed.BLAST_PREVIEW_LINE;
    expect(parses("ledger_blast_radius", 200, body)).toBe(false);
  });
});
