// The messages for the M6.5 refusal families the ledger and recall screens meet (chunk 65f).
// describeLedgerError is an exhaustive switch (no catch-all label); these tests give every code its
// own check, and show that no sidecar text reaches the words.

import { describe, expect, it } from "vitest";
import {
  ACKNOWLEDGE_STALE_STATEMENT,
  HEADERS_CHANGED_TEXT,
  PROMISES_PAGE_TOO_LARGE,
} from "./fixedTexts";
import {
  CHECK_REFUSAL_CODES,
  EXPORT_REFUSAL_CODES,
  FIXED_ERROR_WORDS,
  GRAPH_REFUSAL_CODES,
  IMPORT_REFUSAL_CODES,
  LEDGER_REFUSAL_CODES,
  M65_API_ERROR_WORDS,
  RECALL_REFUSAL_CODES,
  BLAST_RADIUS_REFUSAL_CODES,
} from "./ipc/proxy";
import {
  ACKNOWLEDGE_STALE_NEXT_STEP,
  CUSTOMER_EXISTS_TEXT,
  CUSTOMER_NAME_INVALID_TEXT,
  CUSTOMER_UNKNOWN_TEXT,
  LEDGER_REFUSED_OTHER_TEXT,
  OTHER_REFUSAL_TEXT,
  PROMISE_UNKNOWN_TEXT,
  PROMISE_VOIDED_TEXT,
  RECALL_TOO_MANY_TEXT,
} from "./ledgerText";
import {
  blastRadiusRefusalMessage,
  describeLedgerError,
  importRefusalMessage,
  ledgerRefusalMessage,
  proxyErrorMessage,
  recallRefusalMessage,
  type LedgerErrorBody,
} from "./messages";

function body(value: unknown): LedgerErrorBody {
  return value as LedgerErrorBody;
}

describe("describeLedgerError", () => {
  it("gives each ledger_refused code a non-empty message, and promise_unknown its own", () => {
    for (const code of LEDGER_REFUSAL_CODES) {
      expect(ledgerRefusalMessage(code).length, code).toBeGreaterThan(0);
    }
    expect(ledgerRefusalMessage("promise_unknown")).toBe(PROMISE_UNKNOWN_TEXT);
    expect(ledgerRefusalMessage("customer_exists")).not.toBe(PROMISE_UNKNOWN_TEXT);
  });

  it("gives each recall_refused code its own message", () => {
    const messages = RECALL_REFUSAL_CODES.map(recallRefusalMessage);
    expect(new Set(messages).size).toBe(RECALL_REFUSAL_CODES.length);
    expect(recallRefusalMessage("promise_voided")).toBe(PROMISE_VOIDED_TEXT);
    expect(recallRefusalMessage("too_many_promises")).toBe(RECALL_TOO_MANY_TEXT);
  });

  it("a stale acknowledgement says the approved sentence and then to run recall again", () => {
    const described = describeLedgerError(body({ error: "recall_refused", code: "acknowledge_stale" }));
    expect(described.text).toBe(`${ACKNOWLEDGE_STALE_STATEMENT} ${ACKNOWLEDGE_STALE_NEXT_STEP}`);
    expect(described.text).toContain("This promise's state changed after you acknowledged it.");
    expect(described.code).toBe("recall_refused:acknowledge_stale");
  });

  it("promise_voided and promise_unknown are told apart by words and by code", () => {
    const voided = describeLedgerError(body({ error: "recall_refused", code: "promise_voided" }));
    const unknown = describeLedgerError(body({ error: "ledger_refused", code: "promise_unknown" }));
    expect(voided.text).not.toBe(unknown.text);
    expect(voided.code).toBe("recall_refused:promise_voided");
    expect(unknown.code).toBe("ledger_refused:promise_unknown");
  });

  it("every fixed word has words, and the busy word is in the busy category", () => {
    for (const word of FIXED_ERROR_WORDS) {
      const described = describeLedgerError(body({ error: word }));
      expect(described.text.length, word).toBeGreaterThan(0);
      expect(described.code).toBe(word);
    }
    expect(describeLedgerError(body({ error: "run_in_progress" })).category).toBe("busy");
    expect(describeLedgerError(body({ error: "ledger_root_not_configured" })).text).toContain(
      "No ledger folder is in use.",
    );
  });

  it("a family no 65f, 65g or 65h screen shows still gets a typed case, with one shared draft sentence", () => {
    const cases: LedgerErrorBody[] = [
      body({ error: "export_refused", code: EXPORT_REFUSAL_CODES[0] }),
      body({ error: "check_refused", code: CHECK_REFUSAL_CODES[0] }),
      body({ error: "graph_refused", code: GRAPH_REFUSAL_CODES[0] }),
    ];
    for (const value of cases) {
      expect(describeLedgerError(value).text).toBe(OTHER_REFUSAL_TEXT);
    }
  });

  it("each blast_radius_refused code has its own words, none of them the shared sentence (65h)", () => {
    const texts = BLAST_RADIUS_REFUSAL_CODES.map(blastRadiusRefusalMessage);
    expect(new Set(texts).size).toBe(BLAST_RADIUS_REFUSAL_CODES.length);
    for (const code of BLAST_RADIUS_REFUSAL_CODES) {
      const described = describeLedgerError(body({ error: "blast_radius_refused", code }));
      expect(described.text, code).toBe(blastRadiusRefusalMessage(code));
      expect(described.text, code).not.toBe(OTHER_REFUSAL_TEXT);
      expect(described.text, code).not.toBe(RECALL_TOO_MANY_TEXT);
      expect(described.code).toBe(`blast_radius_refused:${code}`);
    }
    expect(texts).toEqual([
      "There are too many promises on this file to preview at once.",
      "The proposed text is larger than ALIBI accepts for one file.",
      "The proposed text contains a NUL character, which ALIBI refuses.",
      "The proposed text cannot be encoded as UTF-8.",
    ]);
  });

  it("each import_refused code has its own words, none of them the shared sentence (65g)", () => {
    const texts = IMPORT_REFUSAL_CODES.map(importRefusalMessage);
    expect(new Set(texts).size).toBe(IMPORT_REFUSAL_CODES.length);
    for (const code of IMPORT_REFUSAL_CODES) {
      const described = describeLedgerError(body({ error: "import_refused", code }));
      expect(described.text, code).toBe(importRefusalMessage(code));
      expect(described.text, code).not.toBe(OTHER_REFUSAL_TEXT);
      expect(described.code).toBe(`import_refused:${code}`);
    }
    expect(importRefusalMessage("headers_changed")).toBe(HEADERS_CHANGED_TEXT);
  });

  it("the three customer refusals an import can meet each have their own words (65g)", () => {
    const codes = ["customer_exists", "customer_unknown", "customer_name_invalid"] as const;
    const texts = codes.map(ledgerRefusalMessage);
    expect(new Set(texts).size).toBe(codes.length);
    for (const text of texts) {
      expect(text).not.toBe(LEDGER_REFUSED_OTHER_TEXT);
      expect(text).not.toBe(PROMISE_UNKNOWN_TEXT);
    }
    expect(ledgerRefusalMessage("customer_exists")).toBe(CUSTOMER_EXISTS_TEXT);
    expect(ledgerRefusalMessage("customer_unknown")).toBe(CUSTOMER_UNKNOWN_TEXT);
    expect(ledgerRefusalMessage("customer_name_invalid")).toBe(CUSTOMER_NAME_INVALID_TEXT);
  });

  it("covers every error word an M6.5 route can carry", () => {
    // Each word, with a valid code where it has one, gives words and a code of plain characters.
    const withCode: Record<string, string> = {
      workbook_refused: "unreadable",
      mapping_refused: "no_mapping",
      draft_refused: "unreadable",
      export_refused: "unreadable",
      check_refused: "no_users_in_scope",
      ledger_refused: "promise_unknown",
      import_refused: "run_record_missing",
      recall_refused: "too_many_promises",
      blast_radius_refused: "too_many_promises",
      graph_refused: "graph_too_large",
    };
    for (const word of M65_API_ERROR_WORDS) {
      const value =
        word === "models_unavailable"
          ? { error: word, models_missing: ["m"] }
          : word === "invalid_request"
            ? { error: word, fields: ["body.x"] }
            : word in withCode
              ? { error: word, code: withCode[word] }
              : { error: word };
      const described = describeLedgerError(body(value));
      expect(described.text.length, word).toBeGreaterThan(0);
      expect(described.code, word).toMatch(/^[a-z0-9_:]+$/);
    }
  });

  it("never carries sidecar text: an extra field's value is not in the words", () => {
    const described = describeLedgerError(
      body({ error: "recall_refused", code: "too_many_promises", counts: { promises: 2 }, note: "SIDECAR-TEXT" }),
    );
    expect(JSON.stringify(described)).not.toContain("SIDECAR-TEXT");
  });

  it("the too_large proxy words and the approved page-too-large sentence are different things", () => {
    expect(proxyErrorMessage("too_large")).not.toBe(PROMISES_PAGE_TOO_LARGE);
    expect(PROMISES_PAGE_TOO_LARGE).toBe(
      "This page of promises is too large to show. Choose a smaller page size.",
    );
  });
});
