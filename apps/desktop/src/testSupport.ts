// Helpers shared by the 6g tests. Not a test file itself. No hostile text is written in this
// source; the hostile cell texts come from fixtures/adversarial-m5/questionnaire-cells.json.

import type { CallResult, RouteName } from "./ipc/proxy";

/** A sidecar reply as `callRoute` would give it (the shape is checked by proxy.test.ts). */
export function replyOf<R extends RouteName>(route: R, status: number, body: unknown): CallResult<R> {
  return { kind: "reply", route, reply: { status, body } } as unknown as CallResult<R>;
}

/** A typed sidecar error body at the given status. */
export function errorOf<R extends RouteName>(route: R, status: number, error: string, extra: object = {}): CallResult<R> {
  return replyOf(route, status, { error, ...extra });
}

/** HTML-escape the way React's server renderer does, so a test can look for escaped text. */
export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#x27;");
}

/** The visible text of rendered markup: tags removed, entities decoded, spaces collapsed. */
export function textOf(html: string): string {
  return html
    .replace(/<[^>]*>/g, " ")
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&")
    .replace(/\s+/g, " ")
    .trim();
}
