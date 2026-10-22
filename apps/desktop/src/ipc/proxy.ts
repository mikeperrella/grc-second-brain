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
// Scope: the seven M6 core routes and the fourteen M6.5 routes (chunk 65e). The request schemas
// mirror the host's own checks in src-tauri/src/proxy/route.rs (types, bounds, required fields),
// which are stricter than the sidecar's. The reply schemas follow the sidecar's real replies
// recorded in fixtures/contracts/replies/<route>.json (scripts/replyContracts.test.mjs parses
// every recorded case). The M6 core routes keep their own error union (ApiError, which
// messages.ts switches over exhaustively); the M6.5 routes add their refusal families to it
// (M65ApiError). No screen uses an M6.5 route yet.

import "../zodConfig";
import { z } from "zod";
import {
  BLAST_COVERAGE,
  BLAST_NO_MATCH_LINE,
  BLAST_PREVIEW_LINE,
  CONTRADICTIONS_NUMERIC_ONLY_STATEMENT,
  GRAPH_SAYS,
  REALITY_DUPLICATE_NOTE,
  REALITY_STALENESS_NOTE,
  REALITY_STATEMENT,
  RECALL_SCOPE,
  RECALL_STATEMENT,
} from "../fixedTexts";

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

// ----- the M6.5 request fields, each mirroring a check in route.rs ---------------------------
//
// Rust counts characters with `chars().count()` (Unicode scalar values); a JS string's `length`
// counts UTF-16 units, so the new text bounds count code points instead. `fileName` above is kept
// for every file field (UTF-16 units: stricter than Rust only for characters outside the BMP).

/** `u32` in route.rs: a whole number from 0 to 4,294,967,295. */
const U32_MAX = 4_294_967_295;
const u32 = z.int().min(0).max(U32_MAX);

/** The number of code points, as Rust's `chars().count()` counts them. */
export function codePointCount(text: string): number {
  return Array.from(text).length;
}

/** A string of `min` to `max` code points (route.rs `chars_within`). */
const codePoints = (min: number, max: number) =>
  z.string().refine((text) => {
    const count = codePointCount(text);
    return count >= min && count <= max;
  });

/** `PromiseId` in route.rs: a dashed lowercase UUID `8-4-4-4-12`. It is NOT a HexId. */
export const PROMISE_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const promiseId = z.string().regex(PROMISE_ID_PATTERN);

/**
 * `IsoDate` in route.rs: `YYYY-MM-DD` in ASCII digits, a year from 0001 to 9999, and a day that
 * exists (29 February only in a leap year).
 */
export function isCalendarDate(text: string): boolean {
  const match = /^([0-9]{4})-([0-9]{2})-([0-9]{2})$/.exec(text);
  if (match === null) {
    return false;
  }
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1];
  return year >= 1 && year <= 9999 && days !== undefined && day >= 1 && day <= days;
}
const isoDate = z.string().refine(isCalendarDate);

/** route.rs `MAX_PAGE` and `MAX_PAGE_SIZE` (the sidecar's own limits). */
export const MAX_PAGE = 100_000;
export const MAX_PAGE_SIZE = 100;

/** The sidecar's `VoidReason`, which route.rs repeats: a void carries no free text. */
export const VOID_REASONS = ["wrong_customer", "imported_in_error", "not_actually_sent", "other"] as const;

/**
 * route.rs `MAX_PROPOSED_TEXT_BYTES` (the sidecar's `MAX_FILE_BYTES`): the proposed text is
 * measured in UTF-8 BYTES, not characters; one over is `bad_request` in the host.
 */
export const MAX_PROPOSED_TEXT_BYTES = 2_097_152;

/** The UTF-8 byte length of `text` (what Rust's `str::len` gives). */
export function utf8ByteLength(text: string): number {
  return new TextEncoder().encode(text).length;
}

/** route.rs `blast_path_is_valid`: a vault-relative POSIX path of 1 to 1,000 characters. */
const blastPath = codePoints(1, 1_000).refine(
  (path) =>
    !path.startsWith("/") &&
    !/^[A-Za-z]:/.test(path) &&
    !path.includes("\\") &&
    !path.includes("\0") &&
    !path.split("/").includes(".."),
);

/** route.rs `scope_is_valid`: a vault-relative prefix of 1 to 260 characters. */
export const contradictionScope = codePoints(1, 260).refine(
  (scope) =>
    !scope.includes("\\") &&
    !scope.includes(":") &&
    !scope.includes("\0") &&
    !scope.startsWith("/") &&
    !scope.split("/").includes(".."),
);

/**
 * The 25 characters of Unicode `White_Space`, which Rust's `str::trim` removes (route.rs
 * `trim_type_item`). Not JS `\s`: that one leaves out U+0085 and adds U+FEFF.
 */
const RUST_WHITE_SPACE = "\\t\\n\\u000b\\f\\r \\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const RUST_TRIM = new RegExp(`^[${RUST_WHITE_SPACE}]+|[${RUST_WHITE_SPACE}]+$`, "gu");
/** U+001C to U+001F: Python's `str.strip` removes them and Rust's `trim` does not. */
const pythonOnlyStrips = (point: number | undefined): boolean =>
  point !== undefined && point >= 0x1c && point <= 0x1f;

/**
 * route.rs `trim_type_item`: after Rust's trim the item is 1 to 64 characters and does not begin
 * or end with a character only Python strips. The host sends the trimmed value; the window sends
 * the item as typed.
 */
export function isTypeItem(item: string): boolean {
  const points = Array.from(item.replace(RUST_TRIM, ""), (char) => char.codePointAt(0));
  return (
    points.length >= 1 &&
    points.length <= 64 &&
    !pythonOnlyStrips(points[0]) &&
    !pythonOnlyStrips(points[points.length - 1])
  );
}

/** route.rs `MAX_TYPE_ITEMS`. */
export const MAX_TYPE_ITEMS = 50;
const typeList = (min: number) => z.array(z.string().refine(isTypeItem)).min(min).max(MAX_TYPE_ITEMS);

/** route.rs `ClaimArgs` and `claim_is_valid`. */
const claimArgs = z
  .strictObject({
    path: codePoints(1, 512),
    start_line: u32.min(1),
    end_line: u32,
    quote: codePoints(1, 2_000),
  })
  .refine((claim) => claim.end_line >= claim.start_line);

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
  // ----- M6.5 (route.rs `RouteRequest`, in its order) -----
  z.strictObject({ route: z.literal("ledger_customers") }),
  // The host needs every query field except customer_id (route.rs `PromisesQuery`).
  z.strictObject({
    route: z.literal("ledger_promises"),
    customer_id: hexId.optional(),
    include_voided: z.boolean(),
    page: u32.min(1).max(MAX_PAGE),
    page_size: u32.min(1).max(MAX_PAGE_SIZE),
  }),
  z.strictObject({ route: z.literal("ledger_cited_paths") }),
  z.strictObject({
    route: z.literal("ledger_import"),
    run_id: hexId,
    file: fileName,
    existing_customer_id: hexId.optional(),
    new_customer_name: codePoints(1, 1_000).optional(),
    confirm_new: z.boolean(),
    sent_on: isoDate,
    preview: z.boolean(),
  }),
  z.strictObject({ route: z.literal("ledger_recall"), customer_id: hexId.optional() }),
  z.strictObject({
    route: z.literal("ledger_void"),
    promise_id: promiseId,
    reason: z.enum(VOID_REASONS),
  }),
  z.strictObject({
    route: z.literal("ledger_acknowledge"),
    promise_id: promiseId,
    // Rust allows 1 to i64::MAX; a JS number is exact only up to 2^53 - 1, which z.int() enforces.
    seen_recall_event_id: z.int().min(1),
  }),
  // Exactly one of proposed_text or delete_file (route.rs `blast_is_valid`).
  z
    .strictObject({
      route: z.literal("ledger_blast_radius"),
      path: blastPath,
      proposed_text: z
        .string()
        .refine((text) => utf8ByteLength(text) <= MAX_PROPOSED_TEXT_BYTES)
        .optional(),
      delete_file: z.boolean(),
      customer_id: hexId.optional(),
    })
    .refine((args) => (args.proposed_text !== undefined) !== args.delete_file),
  z.strictObject({ route: z.literal("reality_inspect"), file: fileName }),
  z.strictObject({ route: z.literal("reality_claims") }),
  // All three lists are required by the host (the sidecar has defaults for two of them).
  z.strictObject({
    route: z.literal("reality_check"),
    file: fileName,
    claim: claimArgs,
    mfa_types: typeList(1),
    phone_types: typeList(0),
    active_statuses: typeList(1),
    exported_at: isoDate,
  }),
  z.strictObject({
    route: z.literal("contradictions_start"),
    scope: contradictionScope.optional(),
    cap: z.int().min(1).max(100).optional(),
  }),
  z.strictObject({ route: z.literal("contradictions_status"), run_id: hexId }),
  z.strictObject({ route: z.literal("vault_graph") }),
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

// ----- the M6.5 refusal families (sidecar errors.py Literals; api/errors.py handlers) ----------

/** `ExportRefusal` (reality check, ADR-016). */
export const EXPORT_REFUSAL_CODES = [
  "file_too_large",
  "too_many_rows",
  "field_too_long",
  "encoding_not_allowed",
  "nul_character",
  "missing_column",
  "duplicate_column",
  "unreadable",
] as const;

/** `CheckRefusal` (reality check, ADR-016). */
export const CHECK_REFUSAL_CODES = ["exported_at_in_future", "claim_not_verbatim", "no_users_in_scope"] as const;

/** `LedgerRefusal` (ADR-017). */
export const LEDGER_REFUSAL_CODES = [
  "customer_name_invalid",
  "customer_exists",
  "customer_unknown",
  "run_exists",
  "run_unknown",
  "promise_exists",
  "promise_unknown",
  "event_invalid",
] as const;

/** `ImportRefusal` (ADR-017 decision 5). */
export const IMPORT_REFUSAL_CODES = [
  "run_record_missing",
  "headers_changed",
  "question_mismatch",
  "customer_choice_invalid",
  "new_customer_not_confirmed",
  "sent_on_invalid",
  "sent_on_in_future",
  "sent_before_drafted",
] as const;

/** `RecallRefusal` (recall, void, acknowledge and cited paths). */
export const RECALL_REFUSAL_CODES = [
  "too_many_promises",
  "promise_voided",
  "acknowledge_stale",
  "too_many_paths",
] as const;

/** `BlastRadiusRefusal` (ADR-018). */
export const BLAST_RADIUS_REFUSAL_CODES = [
  "too_many_promises",
  "proposed_text_too_large",
  "proposed_text_has_nul",
  "proposed_text_not_utf8",
] as const;

/** `GraphRefusal` (ADR-023). */
export const GRAPH_REFUSAL_CODES = ["graph_too_large"] as const;

/** Integers only, never text from a file, the ledger or the vault. */
const refusalCounts = z.record(z.string(), z.int()).optional();

/** Every error an M6.5 route can carry: the M6 core union plus the seven refusal families. */
const m65ApiErrorSchema = z.union([
  apiErrorSchema,
  z.strictObject({
    error: z.literal("export_refused"),
    code: z.enum(EXPORT_REFUSAL_CODES),
    // One of ALIBI's own required column names, never a value from the file.
    column: z.string().optional(),
  }),
  z.strictObject({ error: z.literal("check_refused"), code: z.enum(CHECK_REFUSAL_CODES) }),
  z.strictObject({
    error: z.literal("ledger_refused"),
    code: z.enum(LEDGER_REFUSAL_CODES),
    // Only on `customer_exists`: the id of the customer the name collided with.
    customer_id: hexId.optional(),
  }),
  z.strictObject({ error: z.literal("import_refused"), code: z.enum(IMPORT_REFUSAL_CODES), counts: refusalCounts }),
  z.strictObject({ error: z.literal("recall_refused"), code: z.enum(RECALL_REFUSAL_CODES), counts: refusalCounts }),
  z.strictObject({
    error: z.literal("blast_radius_refused"),
    code: z.enum(BLAST_RADIUS_REFUSAL_CODES),
    counts: refusalCounts,
  }),
  z.strictObject({ error: z.literal("graph_refused"), code: z.enum(GRAPH_REFUSAL_CODES), counts: refusalCounts }),
]);
export type M65ApiError = z.infer<typeof m65ApiErrorSchema>;

/** Every `error` word an M6.5 route can carry (the M6 core words plus seven). */
export const M65_API_ERROR_WORDS: readonly string[] = [
  ...API_ERROR_WORDS,
  "export_refused",
  "check_refused",
  "ledger_refused",
  "import_refused",
  "recall_refused",
  "blast_radius_refused",
  "graph_refused",
];

/** The statuses that carry an error body, and the ones that carry a success body. */
export const ERROR_STATUSES = [400, 404, 409, 422, 503] as const;
export const SUCCESS_STATUSES = [200, 202] as const;

const errorStatus = z.union([
  z.literal(400),
  z.literal(404),
  z.literal(409),
  z.literal(422),
  z.literal(503),
]);

const errorReply = z.strictObject({ status: errorStatus, body: apiErrorSchema });
const m65ErrorReply = z.strictObject({ status: errorStatus, body: m65ApiErrorSchema });

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
  // The run record's full id (ADR-022 decision 7, sent since chunk 65b); never the 8-hex prefix.
  run_id: hexId,
  model: z.string(),
  file_name: z.string(),
  complete: z.boolean(),
  question_count: z.int(),
  rows: z.array(draftRowBody),
});

// ----- the M6.5 success bodies (sidecar api/ledger.py, api/reality.py, api/contradictions.py,
// api/vault_graph.py). Words the sidecar types as a closed Literal are a closed enum here; a fixed
// sentence is a `z.literal` of its fixedTexts.ts constant (ADR-022 decision 2). A promise's
// answer `status` stays a plain string, as on the draft rows (F36-2).

/** `RecallState` (repositories/base.py). */
export const RECALL_STATES = ["intact", "moved", "changed", "missing"] as const;
const recallState = z.enum(RECALL_STATES);

/** Recall details: `changed` (ledger/recall.py) and `missing` (`ReadReason`, vault/scan.py). */
export const RECALL_DETAILS = [
  "cited_lines_changed",
  "quote_not_found",
  "not_found",
  "unsafe_path",
  "too_large",
  "not_utf8",
] as const;
const recallDetail = z.enum(RECALL_DETAILS);

/** Recall flags (ledger/recall.py `FLAG_ORDER`): information, not states. */
export const RECALL_FLAGS = ["document_changed_elsewhere", "review_overdue", "frontmatter_invalid"] as const;
const recallFlag = z.enum(RECALL_FLAGS);

/** Python `date` as JSON. */
const replyDate = isoDate;

const customerBody = z.strictObject({ customer_id: hexId, display_name: z.string() });

const promiseBody = z.strictObject({
  promise_id: promiseId,
  customer_id: hexId,
  run_id: hexId,
  run_row_index: z.int(),
  question_text: z.string(),
  answer_text: z.string(),
  status: z.string(),
  source_path: z.string(),
  doc_id: z.string(),
  start_line: z.int(),
  end_line: z.int(),
  quote: z.string(),
  edited_after_draft: z.boolean(),
  sent_on: replyDate,
  created_on: replyDate,
  voided: z.boolean(),
  recall_event_id: z.int().nullable(),
  acknowledged_event_id: z.int().nullable(),
  acknowledged_state: recallState.nullable(),
  acknowledgement_current: z.boolean(),
});

const promisesBody = z.strictObject({
  page: z.int(),
  page_size: z.int(),
  total: z.int(),
  promises: z.array(promiseBody),
});

const citedPathsBody = z.strictObject({
  paths: z.array(z.strictObject({ source_path: z.string(), promise_count: z.int() })),
});

const importBody = z.strictObject({
  created: z.int(),
  created_edited: z.int(),
  customer_id: hexId.nullable(),
  customer_created: z.boolean(),
  skipped: z.record(z.string(), z.int()),
  review: z.record(z.string(), z.int()),
  other_values: z.array(z.strictObject({ text: z.string(), truncated: z.boolean(), count: z.int() })),
  promise_ids: z.array(promiseId),
  already_changed_since_draft: z.int().nullable(),
  preview: z.boolean(),
});

const locationBody = z.strictObject({ start_line: z.int(), end_line: z.int() });

const recallItemBody = z.strictObject({
  promise_id: promiseId,
  customer_id: hexId,
  source_path: z.string(),
  start_line: z.int(),
  end_line: z.int(),
  state: recallState,
  detail: recallDetail.nullable(),
  locations: z.array(locationBody),
  locations_not_listed: z.int(),
  flags: z.array(recallFlag),
  previous_state: recallState.nullable(),
  event_recorded: z.boolean(),
  recall_event_id: z.int().nullable(),
  acknowledged_event_id: z.int().nullable(),
  acknowledged_state: recallState.nullable(),
  acknowledgement_current: z.boolean(),
  statement: z.literal(RECALL_STATEMENT),
  scope: z.literal(RECALL_SCOPE),
});

const recallBody = z.strictObject({
  checked: z.int(),
  events_recorded: z.int(),
  counts: z.record(z.string(), z.int()),
  items: z.array(recallItemBody),
  statement: z.literal(RECALL_STATEMENT),
  scope: z.literal(RECALL_SCOPE),
});

const voidBody = z.strictObject({ promise_id: promiseId, event_id: z.int(), kind: z.literal("voided") });
const acknowledgeBody = z.strictObject({
  promise_id: promiseId,
  event_id: z.int(),
  kind: z.literal("acknowledged"),
});

const blastItemBody = z.strictObject({
  promise_id: promiseId,
  customer_id: hexId,
  source_path: z.string(),
  start_line: z.int(),
  end_line: z.int(),
  state_now: recallState,
  state_proposed: recallState,
  would_change: z.boolean(),
  proposed_detail: recallDetail.nullable(),
  locations: z.array(locationBody),
  locations_not_listed: z.int(),
  flags: z.array(recallFlag),
  flags_now: z.array(recallFlag),
  statement: z.literal(RECALL_STATEMENT),
  scope: z.literal(RECALL_SCOPE),
  preview: z.literal(BLAST_PREVIEW_LINE),
});

const blastBody = z.strictObject({
  checked: z.int(),
  would_change: z.int(),
  counts: z.record(z.string(), z.int()),
  items: z.array(blastItemBody),
  statement: z.literal(RECALL_STATEMENT),
  scope: z.literal(RECALL_SCOPE),
  coverage: z.literal(BLAST_COVERAGE),
  preview: z.literal(BLAST_PREVIEW_LINE),
  // The no-match sentence when no live promise cites the path, else null.
  note: z.literal(BLAST_NO_MATCH_LINE).nullable(),
});

/** A value from an IT export with its count: untrusted text, shortened by the sidecar. */
const valueCountBody = z.strictObject({ value: z.string(), count: z.int(), truncated: z.boolean() });

const sha256Hex = z.string().regex(/^[0-9a-f]{64}$/);

const realityInspectBody = z.strictObject({
  sha256: sha256Hex,
  size_bytes: z.int(),
  data_rows: z.int(),
  malformed_rows: z.int(),
  statuses: z.array(valueCountBody),
  statuses_not_listed: z.int(),
  authenticator_types: z.array(valueCountBody),
  authenticator_types_not_listed: z.int(),
  duplicate_logins: z.int(),
});

const claimCandidateBody = z.strictObject({
  path: z.string(),
  doc_id: z.string(),
  start_line: z.int(),
  end_line: z.int(),
  quote: z.string(),
  qualifier_words: z.array(z.string()),
  mapped_to_authentication_control: z.boolean(),
  matched_control_ids: z.array(z.string()),
  // The sidecar has two notes (mapped and unmapped); only the mapped one is in a recorded reply,
  // so it is not pinned here.
  control_note: z.string(),
});

const realityClaimsBody = z.strictObject({
  candidates: z.array(claimCandidateBody),
  candidates_not_listed: z.int(),
  too_long: z.int(),
  scanned_documents: z.int(),
  excluded_framework: z.int(),
});

const realityCheckBody = z.strictObject({
  claim: claimCandidateBody,
  sha256: sha256Hex,
  exported_at: replyDate,
  finding: z.enum(["exceptions_found", "no_exceptions_in_export"]),
  statement: z.literal(REALITY_STATEMENT),
  staleness_note: z.literal(REALITY_STALENESS_NOTE),
  duplicate_note: z.literal(REALITY_DUPLICATE_NOTE),
  in_scope: z.int(),
  with_mfa: z.int(),
  without_mfa: z.int(),
  admins_without_mfa: z.int(),
  admin_unknown: z.int(),
  phone_only: z.int(),
  phone_types_named: z.boolean(),
  skipped: z.array(valueCountBody),
  skipped_not_listed: z.int(),
  malformed_rows: z.int(),
  duplicate_logins: z.int(),
  mfa_types_not_present: z.array(z.string()),
  // Personal data (F26-7): shown as plain text only, never logged or stored.
  exceptions: z.array(z.strictObject({ login: z.string(), truncated: z.boolean() })),
  exceptions_not_listed: z.int(),
});

const contradictionsStartBody = z.strictObject({ run_id: hexId });

/** contradictions/models.py `FindingStatus`, `Flag`, `CandidateReason`, `Relation`, `ModelUse`. */
export const FINDING_STATUSES = ["VALUE_CONFLICT", "CANDIDATE"] as const;
export const FINDING_FLAGS = ["model_not_consulted"] as const;
export const CANDIDATE_REASONS = [
  "days_vs_months",
  "weak_subject_overlap",
  "no_model_verdict",
  "model_lowered",
  "model_quote_not_verified",
] as const;
export const RELATIONS = ["same_subject_value_mismatch", "scope_difference", "unrelated", "unclear"] as const;
export const MODEL_USES = ["available", "unavailable", "model_missing", "non_local_refused", "partial"] as const;
/** contradictions/run_jobs.py `RunState` and `failure_code`. */
export const CONTRADICTION_RUN_STATES = ["running", "complete", "failed", "cancelled"] as const;
export const CONTRADICTION_FAILURES = ["index_not_built", "detector_error", "internal_error"] as const;

const findingBody = z.strictObject({
  finding_id: z.string(),
  status: z.enum(FINDING_STATUSES),
  quote_a: z.string(),
  source_a: z.string(),
  quote_b: z.string(),
  source_b: z.string(),
  subject: z.string(),
  numbers: z.array(z.string()),
  flags: z.array(z.enum(FINDING_FLAGS)),
  reasons: z.array(z.enum(CANDIDATE_REASONS)),
  verdict: z
    .strictObject({
      conflict: z.boolean(),
      relation: z.enum(RELATIONS),
      // The model's words: untrusted, capped by the sidecar, never logged.
      explanation: z.string(),
      model_confidence: z.number(),
    })
    .nullable(),
});

const contradictionReportBody = z.strictObject({
  findings: z.array(findingBody),
  model: z.string(),
  model_status: z.enum(MODEL_USES),
  template_sha256: z.string(),
  says: z.literal(CONTRADICTIONS_NUMERIC_ONLY_STATEMENT),
  over_cap: z.int(),
  dropped: z.record(z.string(), z.int()),
  verdict_counts: z.record(z.string(), z.record(z.string(), z.int())),
  no_verdict: z.int(),
  model_calls: z.int(),
  chunks_read: z.int(),
  excluded_chunks: z.int(),
  claims_found: z.int(),
  pairs_generated: z.int(),
  withheld_stale: z.int(),
});

const contradictionsStatusBody = z.strictObject({
  run_id: hexId,
  status: z.enum(CONTRADICTION_RUN_STATES),
  pairs_total: z.int().nullable(),
  pairs_done: z.int(),
  failure: z.enum(CONTRADICTION_FAILURES).nullable(),
  report: contradictionReportBody.nullable(),
});

/** vault/models.py `LinkOrigin`. */
export const LINK_ORIGINS = ["body", "frontmatter"] as const;

const graphBody = z.strictObject({
  nodes: z.array(
    z.strictObject({
      index: z.int(),
      path: z.string(),
      doc_id: z.string(),
      type: z.string(),
      // null when no ledger folder is in use, never 0 (ADR-023 decision 5).
      live_promises: z.int().nullable(),
    }),
  ),
  edges: z.array(
    z.strictObject({ from: z.int(), to: z.int(), origin: z.enum(LINK_ORIGINS), count: z.int() }),
  ),
  unresolved_links: z.int(),
  ambiguous_links: z.int(),
  rejected_links: z.int(),
  links_not_drawn: z.int(),
  files_refused: z.int(),
  says: z.literal(GRAPH_SAYS),
});

const reply = <S extends 200 | 202, B extends z.ZodType>(status: S, body: B) =>
  z.union([z.strictObject({ status: z.literal(status), body }), errorReply]);

/** An M6.5 route's reply: its success body, or any M6 core or M6.5 error. */
const reply65 = <S extends 200 | 202, B extends z.ZodType>(status: S, body: B) =>
  z.union([z.strictObject({ status: z.literal(status), body }), m65ErrorReply]);

export const routeReplySchemas = {
  readiness: reply(200, readinessBody),
  questionnaire_inspect: reply(200, inspectBody),
  questionnaire_start: reply(202, startBody),
  questionnaire_status: reply(200, runStatusBody),
  vault_index_start: reply(202, indexStartBody),
  vault_index_status: reply(200, indexStatusBody),
  draft_rows: reply(200, draftBody),
  ledger_customers: reply65(200, z.array(customerBody)),
  ledger_promises: reply65(200, promisesBody),
  ledger_cited_paths: reply65(200, citedPathsBody),
  ledger_import: reply65(200, importBody),
  ledger_recall: reply65(200, recallBody),
  ledger_void: reply65(200, voidBody),
  ledger_acknowledge: reply65(200, acknowledgeBody),
  ledger_blast_radius: reply65(200, blastBody),
  reality_inspect: reply65(200, realityInspectBody),
  reality_claims: reply65(200, realityClaimsBody),
  reality_check: reply65(200, realityCheckBody),
  contradictions_start: reply65(202, contradictionsStartBody),
  contradictions_status: reply65(200, contradictionsStatusBody),
  vault_graph: reply65(200, graphBody),
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
