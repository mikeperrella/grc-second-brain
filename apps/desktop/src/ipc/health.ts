// The contract for the `sidecar_health` command (CLAUDE.md 7: every payload crossing the Tauri IPC
// boundary is parsed with Zod before use).
//
// What Rust sends (apps/desktop/src-tauri/src/health.rs and sidecar/supervisor.rs):
//   * `invoke()` RESOLVES with a HealthReply:  {"status":"starting"} | {"status":"healthy"} |
//     {"status":"not_running"} | {"status":"failed","reason":<FailureKind>}
//   * `invoke()` REJECTS with a HealthError:   {"error":"unauthorized"} and four others. A command
//     the capability does not allow also rejects, with a plain string.
// Every object is `strictObject`: an unknown key (a token, a detail string) makes the whole payload
// invalid instead of being carried along. `src/ipc/health-contract.json` holds the JSON Rust
// serializes for every variant; a Rust test keeps it current and the Vitest suite parses it.
//
// Which channel a value arrived on decides how it is read, never what it contains: an unparseable
// value on the resolve channel is `invalid_response`, on the reject channel it is `rejected`.

import "../zodConfig";
import { z } from "zod";

const kind = <K extends string>(name: K) =>
  z.strictObject({ kind: z.literal(name) });

export const failureKindSchema = z.discriminatedUnion("kind", [
  kind("timeout"),
  kind("eof_before_ready"),
  kind("not_json"),
  kind("wrong_fields"),
  kind("port_out_of_range"),
  kind("line_too_long"),
  kind("read_failed"),
  // `code` is always present; null when Windows reported no exit code.
  z.strictObject({
    kind: z.literal("exited_before_ready"),
    code: z.int32().nullable(),
  }),
  kind("startup_rejected"),
  kind("exited_after_ready"),
  kind("protocol_violation"),
  kind("not_installed"),
  kind("not_configured"),
  kind("outside_sidecar_dir"),
  kind("spawn_failed"),
  kind("job_failed"),
  kind("job_assign_failed"),
  kind("unexpected_thread_state"),
  kind("log_dir_unavailable"),
  kind("index_dir_unavailable"),
  kind("system_root_unavailable"),
  kind("random_unavailable"),
  kind("env_rejected"),
  kind("http_client_failed"),
  kind("internal"),
]);

export const healthReplySchema = z.discriminatedUnion("status", [
  z.strictObject({ status: z.literal("starting") }),
  z.strictObject({ status: z.literal("healthy") }),
  z.strictObject({ status: z.literal("not_running") }),
  z.strictObject({ status: z.literal("failed"), reason: failureKindSchema }),
]);

const errorKind = <E extends string>(name: E) =>
  z.strictObject({ error: z.literal(name) });

export const healthErrorSchema = z.discriminatedUnion("error", [
  errorKind("unauthorized"),
  errorKind("unreachable"),
  errorKind("timeout"),
  errorKind("bad_response"),
  errorKind("internal"),
]);

export type FailureKind = z.infer<typeof failureKindSchema>;
export type HealthError = z.infer<typeof healthErrorSchema>;

/**
 * What the window shows. Only fixed codes: no text from the host or the sidecar ever reaches it.
 * `source` keeps apart failures that share a code (the supervisor's handshake `timeout` and a
 * health request `timeout`).
 */
export type HealthView =
  | { state: "starting" }
  | { state: "healthy" }
  | { state: "failed"; source: "sidecar"; code: FailureKind["kind"] }
  | { state: "failed"; source: "health_check"; code: HealthError["error"] }
  | { state: "failed"; source: "stopped"; code: "not_running" }
  | { state: "failed"; source: "ipc"; code: "invalid_response" | "rejected" };

const INVALID_RESPONSE: HealthView = {
  state: "failed",
  source: "ipc",
  code: "invalid_response",
};
const REJECTED: HealthView = { state: "failed", source: "ipc", code: "rejected" };

/**
 * Read what `invoke("sidecar_health")` RESOLVED with. Never throws; the Zod error is dropped
 * because it can echo the input.
 */
export function parseHealthResult(raw: unknown): HealthView {
  try {
    const parsed = healthReplySchema.safeParse(raw);
    if (!parsed.success) {
      return INVALID_RESPONSE;
    }
    const reply = parsed.data;
    switch (reply.status) {
      case "starting":
        return { state: "starting" };
      case "healthy":
        return { state: "healthy" };
      case "not_running":
        return { state: "failed", source: "stopped", code: "not_running" };
      case "failed":
        return { state: "failed", source: "sidecar", code: reply.reason.kind };
    }
  } catch {
    // A hostile value (a getter that throws) must not escape as an exception.
    return INVALID_RESPONSE;
  }
}

/**
 * Read what `invoke("sidecar_health")` REJECTED with. A HealthError is a failed health probe;
 * anything else, including a valid-looking reply and the string a refused command gives, is
 * `rejected`. Never throws.
 */
export function parseHealthRejection(raw: unknown): HealthView {
  try {
    const parsed = healthErrorSchema.safeParse(raw);
    if (!parsed.success) {
      return REJECTED;
    }
    return { state: "failed", source: "health_check", code: parsed.data.error };
  } catch {
    return REJECTED;
  }
}

/**
 * Run one health call and turn the outcome, whichever channel it used, into a view. `call` is
 * typed to give `unknown`, so nothing is trusted before parsing. Never rejects.
 */
export async function fetchHealth(
  call: () => Promise<unknown>,
): Promise<HealthView> {
  let raw: unknown;
  try {
    raw = await call();
  } catch (rejection: unknown) {
    return parseHealthRejection(rejection);
  }
  return parseHealthResult(raw);
}
