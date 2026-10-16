import { describe, expect, it } from "vitest";
// The JSON the Rust test `the_ipc_contract_fixture_matches_what_rust_serializes` keeps current.
import contract from "../../src-tauri/tests/fixtures/proxy-contract.json";
import {
  API_ERROR_WORDS,
  DRAFT_REFUSAL_CODES,
  ERROR_STATUSES,
  FIXED_ERROR_WORDS,
  FORWARDED_STATUSES,
  MAPPING_REFUSAL_CODES,
  ROUTE_NAMES,
  SUCCESS_STATUSES,
  WORKBOOK_REFUSAL_CODES,
  callRoute,
  parseRejection,
  proxyErrorSchema,
  routeReplySchemas,
  routeRequestSchema,
  type Invoke,
  type RouteName,
} from "./proxy";

// The names the schema accepts, read from the schema itself (not from a second hand-kept list).
const schemaErrors = proxyErrorSchema.options.map((option) => option.shape.error.value);

const sorted = <T extends string | number>(values: readonly T[]): T[] => [...values].sort();

describe("the contract fixture written by the Rust test", () => {
  it("is not empty, so the checks below cannot pass over nothing", () => {
    expect(contract.errors.length).toBe(8);
    expect(contract.route_names.length).toBe(7);
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
    };
    for (const route of ROUTE_NAMES) {
      expect(routeReplySchemas[route].safeParse(success[route]).success, route).toBe(true);
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
