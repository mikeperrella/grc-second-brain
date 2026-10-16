// What the window shows about the models (M6_PLAN B.4, G11; ADR-011; DESIGN.md 7 "Model
// unavailable"). Built only from closed enums and the configured model NAMES the sidecar lists.
// Model names are shown as plain text; they are never a verdict.

import type { CallResult, ReadinessBody } from "./ipc/proxy";

/** Fixed statement, always visible when models are missing (DESIGN.md 7). */
export const NO_DOWNLOAD_STATEMENT =
  "ALIBI never downloads models; install them with Ollama yourself";

export type OllamaState = ReadinessBody["ollama"];

export type ReadinessView =
  /** Not asked yet, or health is not `healthy`. */
  | { state: "unknown" }
  | { state: "checking" }
  | { state: "ready" }
  | { state: "degraded"; models: readonly string[]; ollama: OllamaState }
  /** The readiness call itself failed; `code` is a fixed word. */
  | { state: "unavailable"; code: string };

function bodyView(body: ReadinessBody): ReadinessView {
  switch (body.state) {
    case "checking":
      return { state: "checking" };
    case "ready":
      return { state: "ready" };
    case "degraded":
      return { state: "degraded", models: body.models_missing, ollama: body.ollama };
  }
}

/** Turn one `readiness` call into a view. Total: every result maps to a view, nothing throws. */
export function readinessView(result: CallResult<"readiness">): ReadinessView {
  switch (result.kind) {
    case "reply": {
      const reply = result.reply;
      if (reply.status !== 200) {
        // A typed refusal (not expected for this route, but the schema allows it): a fixed word.
        return { state: "unavailable", code: reply.body.error };
      }
      return bodyView(reply.body);
    }
    case "proxy_error":
      return { state: "unavailable", code: result.error };
    case "invalid_response":
      return { state: "unavailable", code: "invalid_response" };
    case "rejected":
      return { state: "unavailable", code: "rejected" };
    case "invalid_request":
      return { state: "unavailable", code: "invalid_request" };
  }
}

/** Plain words for the Ollama state, from the closed enum. */
export function describeOllama(state: OllamaState): string {
  switch (state) {
    case "unknown":
      return "Ollama: not checked yet";
    case "reachable":
      return "Ollama: reachable";
    case "unreachable":
      return "Ollama: not reachable";
    case "bad_response":
      return "Ollama: gave an answer ALIBI could not read";
  }
}
