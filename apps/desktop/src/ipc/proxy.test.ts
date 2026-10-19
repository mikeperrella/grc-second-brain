import { describe, expect, it } from "vitest";
// The JSON the Rust test `the_ipc_contract_fixture_matches_what_rust_serializes` keeps current.
import contract from "../../src-tauri/tests/fixtures/proxy-contract.json";
import {
  API_ERROR_WORDS,
  DRAFT_REFUSAL_CODES,
  ERROR_STATUSES,
  FIXED_ERROR_WORDS,
  FORWARDED_STATUSES,
  M65_API_ERROR_WORDS,
  MAPPING_REFUSAL_CODES,
  MAX_PROPOSED_TEXT_BYTES,
  ROUTE_NAMES,
  SUCCESS_STATUSES,
  WORKBOOK_REFUSAL_CODES,
  callRoute,
  codePointCount,
  isCalendarDate,
  isTypeItem,
  parseRejection,
  proxyErrorSchema,
  routeReplySchemas,
  routeRequestSchema,
  utf8ByteLength,
  type Invoke,
  type RouteName,
} from "./proxy";

// The sidecar's recorded replies (written by sidecar/tests/test_reply_contracts.py). The full
// parse of every case is scripts/replyContracts.test.mjs; here only the first success of each.
const RECORDED = import.meta.glob<{ route: string; replies: { case: string; status: number; body: unknown }[] }>(
  "../../../../fixtures/contracts/replies/*.json",
  { eager: true, import: "default" },
);

function firstSuccess(route: string): { status: number; body: unknown } {
  const file = Object.entries(RECORDED).find(([path]) => path.endsWith(`/${route}.json`))?.[1];
  const found = file?.replies.find((entry) => entry.status === 200 || entry.status === 202);
  if (found === undefined) {
    throw new Error(`no recorded success for ${route}`);
  }
  return { status: found.status, body: found.body };
}

// The names the schema accepts, read from the schema itself (not from a second hand-kept list).
const schemaErrors = proxyErrorSchema.options.map((option) => option.shape.error.value);

const sorted = <T extends string | number>(values: readonly T[]): T[] => [...values].sort();

describe("the contract fixture written by the Rust test", () => {
  it("is not empty, so the checks below cannot pass over nothing", () => {
    expect(contract.errors.length).toBe(8);
    expect(contract.route_names.length).toBe(21);
    expect(contract.forwarded_statuses.length).toBe(7);
  });

  it("names exactly the proxy error words the schema names", () => {
    const fixtureErrors = contract.errors.map((entry) => entry.error);
    expect(sorted(fixtureErrors)).toEqual(sorted(schemaErrors));
  });

  it("parses every error Rust can send, none as rejected", () => {
    for (const entry of contract.errors) {
      expect(parseRejection(entry), JSON.stringify(entry)).toEqual({
        kind: "proxy_error",
        error: entry.error,
      });
    }
  });

  it("names exactly the routes the request schema names", () => {
    expect(sorted(contract.route_names)).toEqual(sorted(ROUTE_NAMES));
    expect(sorted(Object.keys(routeReplySchemas))).toEqual(sorted(contract.route_names));
  });

  it("forwards exactly the statuses the reply schemas can carry", () => {
    expect(sorted(contract.forwarded_statuses)).toEqual(sorted(FORWARDED_STATUSES));
    expect(sorted([...SUCCESS_STATUSES, ...ERROR_STATUSES])).toEqual(
      sorted(contract.forwarded_statuses),
    );
  });
});

const HEX = "0123456789abcdef";
const SHA = HEX.repeat(4);
const PROMISE = "00000000-0000-4000-8000-000000000001";

const CLAIM = { path: "access-control-policy.md", start_line: 23, end_line: 23, quote: "MFA is required." };

const REQUESTS = {
  readiness: { route: "readiness" },
  questionnaire_inspect: { route: "questionnaire_inspect", file: "caiq.xlsx" },
  questionnaire_start: {
    route: "questionnaire_start",
    file: "caiq.xlsx",
    mapping: { sheet: "Sheet1", header_row: 1, question_col: 2, id_col: 1 },
  },
  questionnaire_status: { route: "questionnaire_status", run_id: HEX },
  vault_index_start: { route: "vault_index_start" },
  vault_index_status: { route: "vault_index_status", job_id: HEX },
  draft_rows: { route: "draft_rows", file: "caiq-UNREVIEWED-DRAFT-0123abcd.xlsx" },
  ledger_customers: { route: "ledger_customers" },
  ledger_promises: { route: "ledger_promises", include_voided: false, page: 1, page_size: 100 },
  ledger_cited_paths: { route: "ledger_cited_paths" },
  ledger_import: {
    route: "ledger_import",
    run_id: HEX,
    file: "caiq-UNREVIEWED-DRAFT-0123abcd.xlsx",
    new_customer_name: "Acme Test Co",
    confirm_new: true,
    sent_on: "2026-10-10",
    preview: true,
  },
  ledger_recall: { route: "ledger_recall" },
  ledger_void: { route: "ledger_void", promise_id: PROMISE, reason: "other" },
  ledger_acknowledge: { route: "ledger_acknowledge", promise_id: PROMISE, seen_recall_event_id: 5 },
  ledger_blast_radius: { route: "ledger_blast_radius", path: "policies/enc.md", proposed_text: "new", delete_file: false },
  reality_inspect: { route: "reality_inspect", file: "okta.csv" },
  reality_claims: { route: "reality_claims" },
  reality_check: {
    route: "reality_check",
    file: "okta.csv",
    claim: CLAIM,
    mfa_types: ["Okta Verify"],
    phone_types: [],
    active_statuses: ["Active"],
    exported_at: "2026-10-01",
  },
  contradictions_start: { route: "contradictions_start" },
  contradictions_status: { route: "contradictions_status", run_id: HEX },
  vault_graph: { route: "vault_graph" },
} as const satisfies Record<RouteName, unknown>;

describe("the request schema", () => {
  it("accepts one valid request for every route", () => {
    for (const route of ROUTE_NAMES) {
      expect(routeRequestSchema.safeParse(REQUESTS[route]).success, route).toBe(true);
    }
  });

  it("rejects an unknown route, an extra key, a missing key and a non-hex id", () => {
    const bad: unknown[] = [
      { route: "ledger_list" },
      { route: "readiness", url: "http://example.test/" },
      { route: "readiness", path: "C:\\Windows" },
      { route: "questionnaire_inspect" },
      { route: "questionnaire_inspect", file: "" },
      { route: "questionnaire_inspect", file: "x".repeat(261) },
      { route: "questionnaire_status", run_id: "../etc" },
      { route: "questionnaire_status", run_id: "ABCDEF12" },
      { route: "questionnaire_status", run_id: "abc" },
      { route: "vault_index_status", job_id: `${HEX}${HEX}${HEX}${HEX}${HEX}` },
      { route: "questionnaire_start", file: "a.xlsx", mapping: { sheet: "s", header_row: 0, question_col: 1 } },
      { route: "questionnaire_start", file: "a.xlsx", mapping: { sheet: "s", header_row: 1, question_col: 1, extra: 1 } },
      "readiness",
      null,
    ];
    for (const request of bad) {
      expect(routeRequestSchema.safeParse(request).success, JSON.stringify(request)).toBe(false);
    }
  });
});

// The host's checks in src-tauri/src/proxy/route.rs, repeated in the window (chunk 65e). Each
// case is one route.rs rule; `ok` mutates a valid request, `refused` must fail.
describe("the M6.5 request schemas mirror the host's route.rs", () => {
  const accepts = (request: unknown): boolean => routeRequestSchema.safeParse(request).success;
  const without = (request: object, key: string): Record<string, unknown> => {
    const copy: Record<string, unknown> = { ...request };
    delete copy[key];
    return copy;
  };

  it("ledger_promises needs include_voided, page and page_size every time, each in range", () => {
    const base = REQUESTS.ledger_promises;
    expect(accepts(base)).toBe(true);
    expect(accepts({ ...base, customer_id: HEX })).toBe(true);
    expect(accepts({ ...base, page: 100_000, page_size: 1 })).toBe(true);
    for (const key of ["include_voided", "page", "page_size"]) {
      expect(accepts(without(base, key)), `missing ${key}`).toBe(false);
    }
    for (const bad of [
      { page: 0 },
      { page: 100_001 },
      { page: 1.5 },
      { page: "1" },
      { page_size: 0 },
      { page_size: 101 },
      { page_size: -1 },
      { include_voided: "true" },
      { customer_id: "ABCDEF12" },
      { customer_id: PROMISE },
      { query: "page=1" },
    ]) {
      expect(accepts({ ...base, ...bad }), JSON.stringify(bad)).toBe(false);
    }
  });

  it("a promise id is a dashed lowercase UUID, never a hex id or anything with other characters", () => {
    const voidOf = (promise_id: unknown) => ({ ...REQUESTS.ledger_void, promise_id });
    const lettered = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d";
    expect(accepts(voidOf(PROMISE))).toBe(true);
    expect(accepts(voidOf(lettered))).toBe(true);
    for (const bad of [
      lettered.toUpperCase(),
      "0A1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
      "00000000-0000-4000-8000-00000000000A",
      `{${PROMISE}}`,
      PROMISE.split("-").join(""),
      "00000000-00004-000-8000-000000000001",
      `${PROMISE}0`,
      PROMISE.slice(1),
      "00000000-0000-4000-8000-0000000000.1",
      "00000000-0000-4000-8000-00000000/../",
      "00000000-0000-4000-8000-000000000?01",
      HEX,
      "",
    ]) {
      expect(accepts(voidOf(bad)), bad).toBe(false);
      expect(accepts({ ...REQUESTS.ledger_acknowledge, promise_id: bad }), bad).toBe(false);
    }
  });

  it("void takes one of the four reasons and nothing else; acknowledge a whole event id from 1", () => {
    for (const reason of ["wrong_customer", "imported_in_error", "not_actually_sent", "other"]) {
      expect(accepts({ ...REQUESTS.ledger_void, reason }), reason).toBe(true);
    }
    for (const reason of ["Other", "typo", "", "wrong customer"]) {
      expect(accepts({ ...REQUESTS.ledger_void, reason }), reason).toBe(false);
    }
    expect(accepts(without(REQUESTS.ledger_void, "reason"))).toBe(false);
    expect(accepts({ ...REQUESTS.ledger_void, suffix: "/acknowledge" })).toBe(false);
    for (const id of [0, -1, 1.5, "5", null, Number.MAX_SAFE_INTEGER + 1]) {
      expect(accepts({ ...REQUESTS.ledger_acknowledge, seen_recall_event_id: id }), String(id)).toBe(false);
    }
    expect(accepts(without(REQUESTS.ledger_acknowledge, "seen_recall_event_id"))).toBe(false);
  });

  it("a date is ASCII YYYY-MM-DD with a day that exists", () => {
    const importOn = (sent_on: unknown) => ({ ...REQUESTS.ledger_import, sent_on });
    const checkOn = (exported_at: unknown) => ({ ...REQUESTS.reality_check, exported_at });
    for (const good of ["2024-02-29", "2026-12-31", "0001-01-01", "9999-12-31", "2000-02-29"]) {
      expect(accepts(importOn(good)), good).toBe(true);
      expect(accepts(checkOn(good)), good).toBe(true);
    }
    for (const bad of [
      "2026-02-30",
      "2025-02-29",
      "1900-02-29",
      "2026-04-31",
      "2026-13-01",
      "2026-00-10",
      "2026-10-00",
      "0000-01-01",
      "2026-1-01",
      "26-10-10",
      "2026/10/10",
      "２０２６-10-10",
      "2026-10-10T00:00",
      " 2026-10-10",
      "",
    ]) {
      expect(accepts(importOn(bad)), bad).toBe(false);
      expect(accepts(checkOn(bad)), bad).toBe(false);
    }
  });

  it("ledger_import needs every field but the customer choice, each within its bound", () => {
    const base = REQUESTS.ledger_import;
    for (const key of ["run_id", "file", "confirm_new", "sent_on", "preview"]) {
      expect(accepts(without(base, key)), `missing ${key}`).toBe(false);
    }
    expect(accepts(without(base, "new_customer_name"))).toBe(true);
    expect(accepts({ ...without(base, "new_customer_name"), existing_customer_id: HEX })).toBe(true);
    expect(accepts({ ...base, new_customer_name: "x".repeat(1_000) })).toBe(true);
    // 1,000 characters outside the BMP (2,000 UTF-16 units): Rust counts characters.
    expect(accepts({ ...base, new_customer_name: "\u{1F600}".repeat(1_000) })).toBe(true);
    for (const bad of [
      { new_customer_name: "" },
      { new_customer_name: "x".repeat(1_001) },
      { existing_customer_id: "ABCDEF12" },
      { run_id: PROMISE },
      { preview: "false" },
      { confirm_new: 1 },
      { file: "" },
    ]) {
      expect(accepts({ ...base, ...bad }), JSON.stringify(bad)).toBe(false);
    }
  });

  it("blast radius takes exactly one of the text or delete_file, and a vault-relative path", () => {
    const base = REQUESTS.ledger_blast_radius;
    expect(accepts({ ...without(base, "proposed_text"), delete_file: true })).toBe(true);
    expect(accepts({ ...base, delete_file: true })).toBe(false);
    expect(accepts(without(base, "proposed_text"))).toBe(false);
    expect(accepts(without(base, "delete_file"))).toBe(false);
    expect(accepts({ ...base, customer_id: HEX })).toBe(true);
    expect(accepts({ ...base, path: "x".repeat(1_000) })).toBe(true);
    expect(accepts({ ...base, path: "a..b/c..d.md" })).toBe(true);
    for (const path of ["", "/etc/passwd", "C:secret.md", "c:/x.md", "a\\b.md", "a/../b.md", "..", "a/\0.md", "x".repeat(1_001)]) {
      expect(accepts({ ...base, path }), JSON.stringify(path)).toBe(false);
    }
  });

  it("blast radius counts the proposed text in UTF-8 bytes, not characters", () => {
    const base = REQUESTS.ledger_blast_radius;
    const withText = (proposed_text: string) => ({ ...base, proposed_text });
    expect(MAX_PROPOSED_TEXT_BYTES).toBe(2_097_152);
    expect(accepts(withText("a".repeat(MAX_PROPOSED_TEXT_BYTES)))).toBe(true);
    expect(accepts(withText("a".repeat(MAX_PROPOSED_TEXT_BYTES + 1)))).toBe(false);
    // 1,048,577 characters of two bytes each: 2,097,154 bytes, under the cap in characters.
    const twoByte = "\u00e9".repeat(1_048_577);
    expect(utf8ByteLength(twoByte)).toBe(2_097_154);
    expect(accepts(withText(twoByte))).toBe(false);
    // 1,048,576 of them is exactly the cap.
    expect(accepts(withText("\u00e9".repeat(1_048_576)))).toBe(true);
    // 699,051 three-byte characters: 2,097,153 bytes.
    expect(accepts(withText("\u20ac".repeat(699_051)))).toBe(false);
    expect(utf8ByteLength("\u{1F600}")).toBe(4);
  });

  it("reality_check needs all three lists, trims each item as Rust does and keeps the host's bounds", () => {
    const base = REQUESTS.reality_check;
    for (const key of ["file", "claim", "mfa_types", "phone_types", "active_statuses", "exported_at"]) {
      expect(accepts(without(base, key)), `missing ${key}`).toBe(false);
    }
    const lists = (over: object) => ({ ...base, ...over });
    expect(accepts(lists({ mfa_types: ["  Okta Verify  "], active_statuses: ["\u3000Active\u0085"] }))).toBe(true);
    expect(accepts(lists({ phone_types: Array.from({ length: 50 }, (_, i) => `Phone ${i}`) }))).toBe(true);
    expect(accepts(lists({ mfa_types: ["x".repeat(64)] }))).toBe(true);
    expect(accepts(lists({ mfa_types: [` ${"x".repeat(64)} `] }))).toBe(true);
    for (const bad of [
      { mfa_types: [] },
      { active_statuses: [] },
      { phone_types: Array.from({ length: 51 }, (_, i) => `Phone ${i}`) },
      { mfa_types: ["x".repeat(65)] },
      { mfa_types: [" "] },
      { mfa_types: [""] },
      { mfa_types: ["\u001cOkta"] },
      { mfa_types: ["Okta\u001f"] },
      { mfa_types: [" \u001dOkta "] },
      // U+FEFF is not White_Space: Rust leaves it, so the item is 65 characters.
      { mfa_types: [`\ufeff${"x".repeat(64)}`] },
      { mfa_types: [7] },
    ]) {
      expect(accepts(lists(bad)), JSON.stringify(bad)).toBe(false);
    }
    expect(isTypeItem("Okta\u001cVerify")).toBe(true);
  });

  it("a claim has lines in order from 1, a path of 1 to 512 and a quote of 1 to 2,000 characters", () => {
    const claimed = (over: object) => ({ ...REQUESTS.reality_check, claim: { ...CLAIM, ...over } });
    expect(accepts(claimed({ start_line: 1, end_line: 1 }))).toBe(true);
    expect(accepts(claimed({ quote: "q".repeat(2_000), path: "p".repeat(512) }))).toBe(true);
    for (const bad of [
      { start_line: 0, end_line: 1 },
      { start_line: 5, end_line: 4 },
      { start_line: 1.5 },
      { end_line: 4_294_967_296 },
      { quote: "" },
      { quote: "q".repeat(2_001) },
      { path: "" },
      { path: "p".repeat(513) },
      { doc_id: "MQ-POL-001" },
    ]) {
      expect(accepts(claimed(bad)), JSON.stringify(bad)).toBe(false);
    }
  });

  it("contradictions_start takes an optional cap of 1 to 100 and a vault-relative scope", () => {
    const base = REQUESTS.contradictions_start;
    expect(accepts({ ...base, cap: 1, scope: "policies/" })).toBe(true);
    expect(accepts({ ...base, cap: 100 })).toBe(true);
    for (const bad of [
      { cap: 0 },
      { cap: 101 },
      { cap: 2.5 },
      { scope: "" },
      { scope: "/abs" },
      { scope: "C:x" },
      { scope: "a\\b" },
      { scope: "a/../b" },
      { scope: "x".repeat(261) },
    ]) {
      expect(accepts({ ...base, ...bad }), JSON.stringify(bad)).toBe(false);
    }
    expect(accepts({ route: "contradictions_status", run_id: "ABCDEF12" })).toBe(false);
  });

  it("no new route takes a key route.rs does not name (a query, suffix or path key included)", () => {
    for (const route of [
      "ledger_customers",
      "ledger_cited_paths",
      "reality_claims",
      "vault_graph",
      "ledger_recall",
      "contradictions_start",
    ] as const) {
      for (const extra of [{ query: "x=1" }, { suffix: "/void" }, { path: "/ledger/customers" }, { url: "http://x.test/" }]) {
        expect(accepts({ ...REQUESTS[route], ...extra }), `${route} ${JSON.stringify(extra)}`).toBe(false);
      }
    }
    expect(accepts({ ...REQUESTS.ledger_recall, customer_id: HEX })).toBe(true);
    expect(accepts({ ...REQUESTS.ledger_recall, customer_id: null })).toBe(false);
    expect(accepts({ ...REQUESTS.reality_inspect, file: "" })).toBe(false);
  });

  it("counts characters as Rust does (code points, not UTF-16 units)", () => {
    expect(codePointCount("\u{1F600}a")).toBe(2);
    expect(codePointCount("")).toBe(0);
    expect(isCalendarDate("2024-02-29")).toBe(true);
    expect(isCalendarDate("2023-02-29")).toBe(false);
  });
});

describe("the reply schemas", () => {
  const readyBody = { state: "degraded", models_missing: ["nomic-embed-text"], ollama: "reachable" };

  it("parses a success body for every route", () => {
    const success: Record<RouteName, unknown> = {
      readiness: { status: 200, body: readyBody },
      questionnaire_inspect: {
        status: 200,
        body: {
          sheets: [{ name: "Sheet1", rows: 10, columns: 3 }],
          suggestion: { sheet: "Sheet1", header_row: 1, question_col: 2, id_col: null },
          macro_enabled: false,
          parts: { "xl/media/image1.png": 1 },
          warnings: [],
          sha256: SHA,
        },
      },
      questionnaire_start: { status: 202, body: { run_id: HEX, total: 3 } },
      questionnaire_status: {
        status: 200,
        body: { run_id: HEX, status: "running", total: 3, completed: 1, failure: null, summary: null },
      },
      vault_index_start: { status: 202, body: { job_id: HEX } },
      vault_index_status: {
        status: 200,
        body: {
          job_id: HEX,
          status: "running",
          files_done: 1,
          files_total: 4,
          files_indexed: 1,
          files_unchanged: 0,
          files_removed: 0,
          files_failed: 0,
          chunks_embedded: 12,
          failure: null,
        },
      },
      draft_rows: {
        status: 200,
        body: {
          run_id: HEX,
          model: "llama3.1:8b",
          file_name: "x-UNREVIEWED-DRAFT-0123abcd.xlsx",
          complete: true,
          question_count: 1,
          rows: [
            {
              row: 2,
              question_text: "Do you encrypt data at rest?",
              status: "SUPPORTED",
              reasons: [],
              answer_text: "Yes.",
              citation: { source_path: "policies/enc.md", start_line: 3, end_line: 4, quote: "AES-256" },
              status_edited: false,
              answer_edited: false,
              evidence_edited: false,
            },
          ],
        },
      },
      ledger_customers: firstSuccess("ledger_customers"),
      ledger_promises: firstSuccess("ledger_promises"),
      ledger_cited_paths: firstSuccess("ledger_cited_paths"),
      ledger_import: firstSuccess("ledger_import"),
      ledger_recall: firstSuccess("ledger_recall"),
      ledger_void: firstSuccess("ledger_void"),
      ledger_acknowledge: firstSuccess("ledger_acknowledge"),
      ledger_blast_radius: firstSuccess("ledger_blast_radius"),
      reality_inspect: firstSuccess("reality_inspect"),
      reality_claims: firstSuccess("reality_claims"),
      reality_check: firstSuccess("reality_check"),
      contradictions_start: firstSuccess("contradictions_start"),
      contradictions_status: firstSuccess("contradictions_status"),
      vault_graph: firstSuccess("vault_graph"),
    };
    for (const route of ROUTE_NAMES) {
      expect(routeReplySchemas[route].safeParse(success[route]).success, route).toBe(true);
    }
  });

  it("an M6.5 route accepts its seven refusal families and every M6 core error; an M6 core route does not take the new ones", () => {
    const samples = [
      { error: "export_refused", code: "missing_column", column: "user.login" },
      { error: "check_refused", code: "no_users_in_scope" },
      { error: "ledger_refused", code: "customer_exists", customer_id: HEX },
      { error: "import_refused", code: "question_mismatch", counts: { mismatched_rows: 1 } },
      { error: "recall_refused", code: "acknowledge_stale" },
      { error: "blast_radius_refused", code: "proposed_text_too_large", counts: { bytes: 3, cap: 2 } },
      { error: "graph_refused", code: "graph_too_large", counts: { nodes: 501 } },
    ];
    expect(M65_API_ERROR_WORDS.length).toBe(API_ERROR_WORDS.length + 7);
    for (const body of samples) {
      expect(routeReplySchemas.ledger_void.safeParse({ status: 422, body }).success, body.error).toBe(true);
      expect(M65_API_ERROR_WORDS).toContain(body.error);
      expect(routeReplySchemas.draft_rows.safeParse({ status: 422, body }).success, body.error).toBe(false);
    }
    expect(routeReplySchemas.vault_graph.safeParse({ status: 503, body: { error: "ledger_unusable" } }).success).toBe(true);
    for (const bad of [
      { error: "graph_refused", code: "graph_too_big" },
      { error: "recall_refused", code: "too_many_promises", counts: { promises: "2" } },
      { error: "ledger_refused", code: "customer_exists", customer_id: "Acme Test Co" },
      { error: "ledger_refused", code: "customer_exists", name: "Acme Test Co" },
      { error: "check_refused", code: "claim_not_verbatim", counts: { a: 1 } },
      { error: "export_refused", code: "missing_column", value: "row text" },
      { error: "contradiction_refused", code: "x" },
    ]) {
      expect(routeReplySchemas.ledger_void.safeParse({ status: 422, body: bad }).success, JSON.stringify(bad)).toBe(false);
    }
  });

  // The exact body the first live run got from the sidecar for the synthetic demo workbook.
  const LIVE_INSPECT_BODY = {
    macro_enabled: false,
    parts: {},
    sha256: "48157fb0ddd95f90e8a2c404085bffaa48fb86e3da1a0453b2551a01927a6402",
    sheets: [
      { columns: 3, name: "Questions", rows: 19 },
      { columns: 1, name: "About", rows: 1 },
    ],
    suggestion: { header_row: 1, id_col: 1, question_col: 2, sheet: "Questions" },
    warnings: [],
  };

  it("parses the real inspect reply from the first live run", () => {
    const schema = routeReplySchemas.questionnaire_inspect;
    expect(schema.safeParse({ status: 200, body: LIVE_INSPECT_BODY }).success).toBe(true);
  });

  it("still refuses an inspect body with an unknown key, a bad sha256 or no sha256", () => {
    const schema = routeReplySchemas.questionnaire_inspect;
    const parse = (body: unknown) => schema.safeParse({ status: 200, body }).success;
    expect(parse({ ...LIVE_INSPECT_BODY, extra: 1 })).toBe(false);
    expect(parse({ ...LIVE_INSPECT_BODY, sha256: "48157FB0".padEnd(64, "A") })).toBe(false);
    expect(parse({ ...LIVE_INSPECT_BODY, sha256: SHA.slice(1) })).toBe(false);
    expect(parse({ ...LIVE_INSPECT_BODY, sha256: `${SHA}0` })).toBe(false);
    const withoutSha: Record<string, unknown> = { ...LIVE_INSPECT_BODY };
    delete withoutSha["sha256"];
    expect(parse(withoutSha)).toBe(false);
  });

  it("parses a finished run status with its summary in the sidecar's shape", () => {
    const body = {
      run_id: HEX,
      status: "complete",
      total: 3,
      completed: 3,
      failure: null,
      summary: {
        model: "llama3.1:8b",
        digest_before: "sha256:aa",
        digest_after: null,
        template_sha256: SHA,
        input_sha256: SHA,
        complete: true,
        counts: { SUPPORTED: 1, NEEDS_REVIEW: 2 },
        reason_counts: { no_evidence: 2 },
        file_name: "x-UNREVIEWED-DRAFT-0123abcd.xlsx",
        rows_written: 3,
        dropped_parts: [["xl/media/image1.png", 1, 0]],
        warnings: [],
        seconds: 12.5,
      },
    };
    const schema = routeReplySchemas.questionnaire_status;
    expect(schema.safeParse({ status: 200, body }).success).toBe(true);
    expect(schema.safeParse({ status: 200, body: { ...body, summary: { ...body.summary, extra: 1 } } }).success).toBe(
      false,
    );
  });

  it("carries a status the draft route does not know through as a string (F36-2)", () => {
    const reply = {
      status: 200,
      body: {
        run_id: HEX,
        model: "m",
        file_name: "f.xlsx",
        complete: true,
        question_count: 1,
        rows: [
          {
            row: 2,
            question_text: "q",
            status: "APPROVED",
            reasons: [],
            answer_text: null,
            citation: null,
            status_edited: false,
            answer_edited: false,
            evidence_edited: false,
          },
        ],
      },
    };
    expect(routeReplySchemas.draft_rows.safeParse(reply).success).toBe(true);
  });

  it("parses every fixed error word on every route, at each error status", () => {
    expect(FIXED_ERROR_WORDS).toHaveLength(17);
    for (const route of ROUTE_NAMES) {
      for (const word of FIXED_ERROR_WORDS) {
        const reply = { status: 409, body: { error: word } };
        expect(routeReplySchemas[route].safeParse(reply).success, `${route}/${word}`).toBe(true);
      }
      for (const status of ERROR_STATUSES) {
        const reply = { status, body: { error: "run_in_progress" } };
        expect(routeReplySchemas[route].safeParse(reply).success, `${route}/${status}`).toBe(true);
      }
    }
  });

  it("parses each coded refusal and rejects a code outside its closed set", () => {
    const schema = routeReplySchemas.questionnaire_inspect;
    for (const code of WORKBOOK_REFUSAL_CODES) {
      expect(schema.safeParse({ status: 422, body: { error: "workbook_refused", code } }).success, code).toBe(true);
    }
    for (const code of MAPPING_REFUSAL_CODES) {
      expect(schema.safeParse({ status: 422, body: { error: "mapping_refused", code } }).success, code).toBe(true);
    }
    for (const code of DRAFT_REFUSAL_CODES) {
      expect(
        routeReplySchemas.draft_rows.safeParse({ status: 422, body: { error: "draft_refused", code } }).success,
        code,
      ).toBe(true);
    }
    expect(
      routeReplySchemas.draft_rows.safeParse({
        status: 422,
        body: { error: "draft_refused", code: "question_mismatch", counts: { differing: 1, rows_record: 3, rows_file: 3 } },
      }).success,
    ).toBe(true);
    expect(schema.safeParse({ status: 422, body: { error: "workbook_refused", code: "made_up" } }).success).toBe(false);
    expect(schema.safeParse({ status: 422, body: { error: "made_up_error" } }).success).toBe(false);
  });

  it("parses models_unavailable with its model names and invalid_request with its field names", () => {
    expect(
      routeReplySchemas.questionnaire_start.safeParse({
        status: 503,
        body: { error: "models_unavailable", models_missing: ["llama3.1:8b"] },
      }).success,
    ).toBe(true);
    expect(
      routeReplySchemas.questionnaire_start.safeParse({
        status: 422,
        body: { error: "invalid_request", fields: ["body.file"] },
      }).success,
    ).toBe(true);
  });

  it("refuses a success body on an error status, an error status on the wrong success, and extra keys", () => {
    const schema = routeReplySchemas.readiness;
    expect(schema.safeParse({ status: 409, body: readyBody }).success).toBe(false);
    expect(schema.safeParse({ status: 202, body: readyBody }).success).toBe(false);
    expect(schema.safeParse({ status: 200, body: { ...readyBody, detail: "x" } }).success).toBe(false);
    expect(schema.safeParse({ status: 200, body: readyBody, extra: 1 }).success).toBe(false);
    expect(schema.safeParse({ status: 200, body: { ...readyBody, ollama: "maybe" } }).success).toBe(false);
    expect(schema.safeParse({ status: 500, body: { error: "run_in_progress" } }).success).toBe(false);
  });

  it("lists every error word it can carry, and no word twice", () => {
    expect(new Set(API_ERROR_WORDS).size).toBe(API_ERROR_WORDS.length);
    expect(API_ERROR_WORDS).toContain("run_in_progress");
    expect(API_ERROR_WORDS).toContain("models_unavailable");
    expect(API_ERROR_WORDS).toContain("draft_refused");
  });
});

describe("callRoute", () => {
  const ok = (value: unknown): Invoke => () => Promise.resolve(value);
  const reject = (value: unknown): Invoke => () => Promise.reject(value);

  it("sends the validated request under the single `request` argument", async () => {
    let seen: { command: string; args: Record<string, unknown> } | undefined;
    const invoke: Invoke = (command, args) => {
      seen = { command, args };
      return Promise.resolve({ status: 202, body: { job_id: HEX } });
    };
    const result = await callRoute<"vault_index_start">(invoke, { route: "vault_index_start" });
    expect(seen).toEqual({ command: "sidecar_call", args: { request: { route: "vault_index_start" } } });
    expect(result).toEqual({
      kind: "reply",
      route: "vault_index_start",
      reply: { status: 202, body: { job_id: HEX } },
    });
  });

  it("passes a typed refusal on as a reply, not as an error", async () => {
    const result = await callRoute<"vault_index_start">(
      ok({ status: 409, body: { error: "run_in_progress" } }),
      { route: "vault_index_start" },
    );
    expect(result).toEqual({
      kind: "reply",
      route: "vault_index_start",
      reply: { status: 409, body: { error: "run_in_progress" } },
    });
  });

  it("maps a rejection with a proxy error word to proxy_error", async () => {
    const result = await callRoute<"readiness">(reject({ error: "not_ready" }), { route: "readiness" });
    expect(result).toEqual({ kind: "proxy_error", error: "not_ready" });
  });

  it("maps any other rejection to rejected, including a plain string and an unknown word", async () => {
    for (const rejection of ["command sidecar_call not allowed", { error: "new_word" }, null, 7]) {
      const result = await callRoute<"readiness">(reject(rejection), { route: "readiness" });
      expect(result, JSON.stringify(rejection)).toEqual({ kind: "rejected" });
    }
  });

  it("maps a reply that fits no schema to invalid_response, without echoing it", async () => {
    for (const reply of [null, "ok", { status: 200 }, { status: 200, body: { state: "ready" } }, { status: 200, body: { secret: "SECRET-TEXT" } }]) {
      const result = await callRoute<"readiness">(ok(reply), { route: "readiness" });
      expect(result).toEqual({ kind: "invalid_response" });
      expect(JSON.stringify(result)).not.toContain("SECRET-TEXT");
    }
  });

  it("does not send a request that fails its own schema", async () => {
    let called = false;
    const invoke: Invoke = () => {
      called = true;
      return Promise.resolve({});
    };
    const bad = { route: "questionnaire_status", run_id: "../x" } as unknown as { route: "questionnaire_status"; run_id: string };
    const result = await callRoute<"questionnaire_status">(invoke, bad);
    expect(result).toEqual({ kind: "invalid_request" });
    expect(called).toBe(false);
  });

  it("never throws, even for a hostile value whose getter throws", async () => {
    const hostile = {
      get status(): number {
        throw new Error("boom");
      },
    };
    expect(await callRoute<"readiness">(ok(hostile), { route: "readiness" })).toEqual({ kind: "invalid_response" });
    expect(await callRoute<"readiness">(reject(hostile), { route: "readiness" })).toEqual({ kind: "rejected" });
    const throwsSync: Invoke = () => {
      throw new Error("sync");
    };
    await expect(callRoute<"readiness">(throwsSync, { route: "readiness" })).resolves.toEqual({
      kind: "rejected",
    });
  });
});
