// The contract for the `sidecar_call` command (ADR-020 decisions 1 and 3; CLAUDE.md 7: every payload
// crossing the Tauri IPC boundary is parsed with Zod before use).
//
// What Rust does (apps/desktop/src-tauri/src/proxy/{route,error,call}.rs, commands.rs):
//   * The window sends ONE tagged object, `{"route": <name>, ...that route's fields}`. It never
//     sends a URL, header, method, port or path. Rust parses it with `deny_unknown_fields`.
//   * `invoke()` RESOLVES with `{status, body}` when the sidecar answered with a status in
//     FORWARDED_STATUSES and a JSON body within the route's cap. `body` is the sidecar's JSON.
//   * `invoke()` REJECTS with a ProxyError `{"error": <word>}` when no sidecar answer can be
//     given. A command the capability does not allow also rejects, with a plain string.
// `src-tauri/tests/fixtures/proxy-contract.json` holds the ProxyError words, the route names and
// the forwarded statuses Rust serializes; proxy.test.ts asserts this file names exactly the same
// ones, so a name added or removed on one side only fails a test.
//
// Every object is `strictObject`: an unknown key makes the whole payload invalid instead of being
// carried along. Nothing here ever throws, and a Zod error is dropped because it can echo the
// input (a file name, a model name).
//
// Scope: the bodies of the SEVEN M6 core routes. The refusal families of the M6.5 routes (ledger,
// recall, blast radius, contradictions, reality) are added with those screens.

import { z } from "zod";

// ----- the words Rust sends, which proxy-contract.json pins -----------------------------------

export const proxyErrorSchema = z.discriminatedUnion("error", [
  z.strictObject({ error: z.literal("not_ready") }),
  z.strictObject({ error: z.literal("unauthorized") }),
  z.strictObject({ error: z.literal("unreachable") }),
  z.strictObject({ error: z.literal("timeout") }),
  z.strictObject({ error: z.literal("too_large") }),
  z.strictObject({ error: z.literal("bad_request") }),
  z.strictObject({ error: z.literal("bad_response") }),
  z.strictObject({ error: z.literal("internal") }),
]);
export type ProxyError = z.infer<typeof proxyErrorSchema>["error"];

/** The statuses Rust passes on (`FORWARDED_STATUSES` in proxy/call.rs). */
export const FORWARDED_STATUSES = [200, 202, 400, 404, 409, 422, 503] as const;
export type ForwardedStatus = (typeof FORWARDED_STATUSES)[number];

// ----- what the window sends ------------------------------------------------------------------

/** A run or job id: the sidecar's `^[0-9a-f]{8,64}$`, repeated by Rust's `HexId`. */
const hexId = z.string().regex(/^[0-9a-f]{8,64}$/);
/** A file NAME in the questionnaire or export folder (1 to 260 characters, the sidecar's limit). */
const fileName = z.string().min(1).max(260);

const mappingSchema = z.strictObject({
  sheet: z.string().min(1).max(64),
  header_row: z.int().min(1).max(1_048_576),
  question_col: z.int().min(1).max(16_384),
  id_col: z.int().min(1).max(16_384).optional(),
});

export const routeRequestSchema = z.discriminatedUnion("route", [
  z.strictObject({ route: z.literal("readiness") }),
  z.strictObject({ route: z.literal("questionnaire_inspect"), file: fileName }),
  z.strictObject({
    route: z.literal("questionnaire_start"),
    file: fileName,
    mapping: mappingSchema,
  }),
  z.strictObject({ route: z.literal("questionnaire_status"), run_id: hexId }),
  z.strictObject({ route: z.literal("vault_index_start") }),
  z.strictObject({ route: z.literal("vault_index_status"), job_id: hexId }),
  z.strictObject({ route: z.literal("draft_rows"), file: fileName }),
]);
export type RouteRequest = z.infer<typeof routeRequestSchema>;
export type RouteName = RouteRequest["route"];

/** The route names the request schema accepts, read from the schema itself. */
export const ROUTE_NAMES: readonly RouteName[] = routeRequestSchema.options.map(
  (option) => option.shape.route.value,
);

// ----- what the sidecar answers: errors (a closed set) ----------------------------------------

/** Closed set of error words with no other field (api/errors.py `_FIXED`, ADR-021). */
export const FIXED_ERROR_WORDS = [
  "questionnaire_root_not_configured",
  "import_root_not_configured",
  "export_root_not_configured",
  "vault_root_not_configured",
  "ledger_root_not_configured",
  "ledger_unusable",
  "ledger_store_error",
  "questionnaire_root_unusable",
  "run_in_progress",
  "run_not_found",
  "vault_changed_reindex",
  "index_not_built",
  "retrieval_unavailable",
  "path_not_allowed",
  "file_not_found",
  "control_map_too_large",
  "auth_controls_invalid",
] as const;

/** `WorkbookRefusal` in sidecar errors.py. */
export const WORKBOOK_REFUSAL_CODES = [
  "file_too_large",
  "uncompressed_too_large",
  "too_many_parts",
  "ratio_too_high",
  "not_a_zip",
  "legacy_or_encrypted",
  "unsupported_type",
  "no_workbook_part",
  "unreadable",
  "doctype",
  "entity",
  "utf32",
  "encoding_not_allowed",
  "duplicate_part",
] as const;

/** `QuestionMappingRefusal` in sidecar errors.py. */
export const MAPPING_REFUSAL_CODES = [
  "no_mapping",
  "sheet_not_found",
  "bad_position",
  "sheet_too_large",
  "too_many_questions",
  "already_has_answer_columns",
] as const;

/**
 * `DraftRefusedError.code`: the three ledger words (ADR-021 decision 5) plus the loader's and the
 * question mapping's own codes. Unreadable is already one of the workbook words.
 */
export const DRAFT_REFUSAL_CODES = [
  "run_record_missing",
  "headers_changed",
  "question_mismatch",
  ...WORKBOOK_REFUSAL_CODES,
  ...MAPPING_REFUSAL_CODES,
] as const;

const fixedErrorSchema = z.strictObject({ error: z.enum(FIXED_ERROR_WORDS) });

const apiErrorSchema = z.union([
  fixedErrorSchema,
  z.strictObject({
    error: z.literal("models_unavailable"),
    models_missing: z.array(z.string()),
  }),
  z.strictObject({
    error: z.literal("workbook_refused"),
    code: z.enum(WORKBOOK_REFUSAL_CODES),
  }),
  z.strictObject({
    error: z.literal("mapping_refused"),
    code: z.enum(MAPPING_REFUSAL_CODES),
  }),
  z.strictObject({
    error: z.literal("draft_refused"),
    code: z.enum(DRAFT_REFUSAL_CODES),
    // Integers only (question_mismatch): never text from the workbook.
    counts: z.record(z.string(), z.int()).optional(),
  }),
  z.strictObject({
    error: z.literal("invalid_request"),
    fields: z.array(z.string()),
  }),
]);
export type ApiError = z.infer<typeof apiErrorSchema>;

/** Every `error` word an M6 core route can carry, for the tests and the screens' message tables. */
export const API_ERROR_WORDS: readonly string[] = [
  ...FIXED_ERROR_WORDS,
  "models_unavailable",
  "workbook_refused",
  "mapping_refused",
  "draft_refused",
  "invalid_request",
];

/** The statuses that carry an error body, and the ones that carry a success body. */
export const ERROR_STATUSES = [400, 404, 409, 422, 503] as const;
export const SUCCESS_STATUSES = [200, 202] as const;

const errorReply = z.strictObject({
  status: z.union([
    z.literal(400),
    z.literal(404),
    z.literal(409),
    z.literal(422),
    z.literal(503),
  ]),
  body: apiErrorSchema,
});

// ----- what the sidecar answers: success bodies, one strict schema per route ------------------

const readinessBody = z.strictObject({
  state: z.enum(["checking", "ready", "degraded"]),
  models_missing: z.array(z.string()),
  ollama: z.enum(["unknown", "reachable", "unreachable", "bad_response"]),
});

const inspectBody = z.strictObject({
  sheets: z.array(
    z.strictObject({ name: z.string(), rows: z.int(), columns: z.int() }),
  ),
  suggestion: z
    .strictObject({
      sheet: z.string(),
      header_row: z.int(),
      question_col: z.int(),
      id_col: z.int().nullable(),
    })
    .nullable(),
  macro_enabled: z.boolean(),
  parts: z.record(z.string(), z.int()),
  warnings: z.array(z.string()),
  // The sidecar's `InspectResponse.sha256` (SHA-256 of the workbook bytes, lowercase hex). Parsed
  // so the strict schema accepts the real reply; the window does not show it.
  sha256: z.string().regex(/^[0-9a-f]{64}$/),
});

const startBody = z.strictObject({ run_id: z.string(), total: z.int() });

const summaryBody = z.strictObject({
  model: z.string(),
  digest_before: z.string().nullable(),
  digest_after: z.string().nullable(),
  template_sha256: z.string(),
  input_sha256: z.string(),
  complete: z.boolean(),
  counts: z.record(z.string(), z.int()),
  reason_counts: z.record(z.string(), z.int()),
  file_name: z.string().nullable(),
  rows_written: z.int(),
  dropped_parts: z.array(z.tuple([z.string(), z.int(), z.int()])),
  warnings: z.array(z.string()),
  seconds: z.number(),
});

const runStatusBody = z.strictObject({
  run_id: z.string(),
  status: z.enum(["running", "complete", "stopped", "failed"]),
  total: z.int(),
  completed: z.int(),
  failure: z.string().nullable(),
  summary: summaryBody.nullable(),
});

const indexStartBody = z.strictObject({ job_id: z.string() });

const indexStatusBody = z.strictObject({
  job_id: z.string(),
  status: z.enum(["running", "complete", "incomplete", "failed"]),
  files_done: z.int(),
  files_total: z.int(),
  files_indexed: z.int(),
  files_unchanged: z.int(),
  files_removed: z.int(),
  files_failed: z.int(),
  chunks_embedded: z.int(),
  failure: z.string().nullable(),
});

const citationBody = z.strictObject({
  source_path: z.string(),
  start_line: z.int(),
  end_line: z.int(),
  quote: z.string(),
});

const draftRowBody = z.strictObject({
  row: z.int(),
  question_text: z.string(),
  // A plain string on purpose: an unknown value must reach the status switch (status.ts, F36-2)
  // and show as an error, not make the whole queue unparseable.
  status: z.string(),
  reasons: z.array(z.string()),
  answer_text: z.string().nullable(),
  citation: citationBody.nullable(),
  status_edited: z.boolean(),
  answer_edited: z.boolean(),
  evidence_edited: z.boolean(),
});

const draftBody = z.strictObject({
  model: z.string(),
  file_name: z.string(),
  complete: z.boolean(),
  question_count: z.int(),
  rows: z.array(draftRowBody),
});

const reply = <S extends 200 | 202, B extends z.ZodType>(status: S, body: B) =>
  z.union([z.strictObject({ status: z.literal(status), body }), errorReply]);

export const routeReplySchemas = {
  readiness: reply(200, readinessBody),
  questionnaire_inspect: reply(200, inspectBody),
  questionnaire_start: reply(202, startBody),
  questionnaire_status: reply(200, runStatusBody),
  vault_index_start: reply(202, indexStartBody),
  vault_index_status: reply(200, indexStatusBody),
  draft_rows: reply(200, draftBody),
} as const satisfies Record<RouteName, z.ZodType>;

export type RouteReply<R extends RouteName> = z.infer<(typeof routeReplySchemas)[R]>;
export type DraftRow = z.infer<typeof draftRowBody>;
export type ReadinessBody = z.infer<typeof readinessBody>;

// ----- the client -----------------------------------------------------------------------------

/**
 * What a call gives back. Whichever channel the outcome used, it is one of these; the caller never
 * sees an exception, a Zod error or text from the host.
 *   * `reply`: the sidecar answered and the body matches that route's schema (this includes typed
 *     refusals such as 409 `run_in_progress`).
 *   * `proxy_error`: the host could not give an answer (a ProxyError word).
 *   * `invalid_response`: the call resolved with something that matches no schema.
 *   * `rejected`: the call rejected with something that is not a ProxyError (for example the plain
 *     string a refused command gives).
 *   * `invalid_request`: the request failed its own schema, so nothing was sent.
 */
export type CallResult<R extends RouteName> =
  | { kind: "reply"; route: R; reply: RouteReply<R> }
  | { kind: "proxy_error"; error: ProxyError }
  | { kind: "invalid_response" }
  | { kind: "rejected" }
  | { kind: "invalid_request" };

export type Invoke = (command: string, args: Record<string, unknown>) => Promise<unknown>;

/**
 * Send one request through `sidecar_call`. `invoke` is typed to give `unknown`, so nothing is
 * trusted before parsing. Never throws and never rejects.
 */
export async function callRoute<R extends RouteName>(
  invoke: Invoke,
  request: Extract<RouteRequest, { route: R }>,
): Promise<CallResult<R>> {
  try {
    const checked = routeRequestSchema.safeParse(request);
    if (!checked.success) {
      return { kind: "invalid_request" };
    }
    let raw: unknown;
    try {
      raw = await invoke("sidecar_call", { request: checked.data });
    } catch (rejection: unknown) {
      return parseRejection(rejection);
    }
    const schema: z.ZodType = routeReplySchemas[checked.data.route];
    const parsed = schema.safeParse(raw);
    if (!parsed.success) {
      return { kind: "invalid_response" };
    }
    // The route was chosen from the validated request, and `schema` is that route's schema.
    return { kind: "reply", route: request.route, reply: parsed.data as RouteReply<R> };
  } catch {
    // A hostile value (a getter that throws) must not escape as an exception.
    return { kind: "invalid_response" };
  }
}

/** Read what `invoke("sidecar_call")` REJECTED with. Never throws. */
export function parseRejection(raw: unknown): { kind: "proxy_error"; error: ProxyError } | { kind: "rejected" } {
  try {
    const parsed = proxyErrorSchema.safeParse(raw);
    if (!parsed.success) {
      return { kind: "rejected" };
    }
    return { kind: "proxy_error", error: parsed.data.error };
  } catch {
    return { kind: "rejected" };
  }
}
