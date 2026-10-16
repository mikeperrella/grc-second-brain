// Every closed code the screens can meet has its own fixed message (DESIGN.md 7). The code lists
// are READ from ipc/proxy.ts and ipc/folders.ts, not copied here, so a code added to a contract
// without a message makes `describe*` throw (assertNever) and a test below fails.

import { describe, expect, it } from "vitest";
import { FOLDER_ERRORS, FOLDER_PROBLEMS } from "./ipc/folders";
import {
  API_ERROR_WORDS,
  DRAFT_REFUSAL_CODES,
  FIXED_ERROR_WORDS,
  MAPPING_REFUSAL_CODES,
  WORKBOOK_REFUSAL_CODES,
  proxyErrorSchema,
  routeReplySchemas,
  type ApiError,
} from "./ipc/proxy";
import {
  DRAFT_REFUSED_TEXT,
  describeApiError,
  describeFolderFailure,
  describeNonReply,
  fixedErrorMessage,
  folderErrorMessage,
  folderProblemMessage,
  mappingRefusalMessage,
  proxyErrorMessage,
  safeCode,
  workbookRefusalMessage,
} from "./messages";

const proxyErrors = proxyErrorSchema.options.map((option) => option.shape.error.value);

function distinct(texts: readonly string[]): void {
  expect(new Set(texts).size).toBe(texts.length);
  for (const text of texts) {
    expect(text.trim().length).toBeGreaterThan(10);
  }
}

describe("every code in the contracts maps to a fixed message", () => {
  it("covers all 8 proxy error words, each with its own text", () => {
    expect(proxyErrors).toHaveLength(8);
    distinct(proxyErrors.map(proxyErrorMessage));
  });

  it("covers all fixed sidecar error words", () => {
    expect(FIXED_ERROR_WORDS.length).toBeGreaterThan(10);
    distinct(FIXED_ERROR_WORDS.map(fixedErrorMessage));
  });

  it("covers every workbook and mapping refusal code", () => {
    distinct(WORKBOOK_REFUSAL_CODES.map(workbookRefusalMessage));
    distinct(MAPPING_REFUSAL_CODES.map(mappingRefusalMessage));
  });

  it("covers every folder error word and folder problem word", () => {
    distinct(FOLDER_ERRORS.map(folderErrorMessage));
    distinct(FOLDER_PROBLEMS.map(folderProblemMessage));
  });

  it("describes every error word in API_ERROR_WORDS, using a body the real schema accepts", () => {
    const sample = (word: string): ApiError => {
      switch (word) {
        case "models_unavailable":
          return { error: "models_unavailable", models_missing: ["m"] };
        case "workbook_refused":
          return { error: "workbook_refused", code: WORKBOOK_REFUSAL_CODES[0] };
        case "mapping_refused":
          return { error: "mapping_refused", code: MAPPING_REFUSAL_CODES[0] };
        case "draft_refused":
          return { error: "draft_refused", code: DRAFT_REFUSAL_CODES[0] };
        case "invalid_request":
          return { error: "invalid_request", fields: [] };
        default:
          return { error: word } as ApiError;
      }
    };
    expect(API_ERROR_WORDS.length).toBe(FIXED_ERROR_WORDS.length + 5);
    for (const word of API_ERROR_WORDS) {
      const body = sample(word);
      // The sample is a body the contract really carries.
      expect(routeReplySchemas.questionnaire_start.safeParse({ status: 409, body }).success, word).toBe(true);
      const described = describeApiError(body);
      expect(described.text.length, word).toBeGreaterThan(10);
      expect(described.code.startsWith(word), word).toBe(true);
      expect(described.code).toMatch(/^[a-z0-9_:]+$/);
    }
  });

  it("describes every workbook, mapping and draft code under its own code", () => {
    for (const code of WORKBOOK_REFUSAL_CODES) {
      const d = describeApiError({ error: "workbook_refused", code });
      expect(d.code).toBe(`workbook_refused:${code}`);
      expect(d.text).toBe(workbookRefusalMessage(code));
    }
    for (const code of MAPPING_REFUSAL_CODES) {
      expect(describeApiError({ error: "mapping_refused", code }).text).toBe(mappingRefusalMessage(code));
    }
    for (const code of DRAFT_REFUSAL_CODES) {
      const d = describeApiError({ error: "draft_refused", code, counts: { rows: 3 } });
      expect(d.code).toBe(`draft_refused:${code}`);
      // The queue's wording is not set yet (DESIGN.md 4.1): one neutral text, no per-code wording.
      expect(d.text).toBe(DRAFT_REFUSED_TEXT);
    }
  });
});

describe("what a described failure may contain", () => {
  it("marks 409 run_in_progress as busy and says 'another job is running'", () => {
    const d = describeApiError({ error: "run_in_progress" });
    expect(d.category).toBe("busy");
    expect(d.text).toContain("another job is running");
    expect(d.text).toContain("does not retry");
  });

  it("carries model names and the no-download flag only for models_unavailable", () => {
    const d = describeApiError({ error: "models_unavailable", models_missing: ["a:1", "b"] });
    expect(d.category).toBe("models");
    expect(d.models).toEqual(["a:1", "b"]);
    expect(d.showNoDownload).toBe(true);
    for (const word of FIXED_ERROR_WORDS) {
      const other = describeApiError({ error: word });
      expect(other.models, word).toEqual([]);
      expect(other.showNoDownload, word).toBe(false);
    }
  });

  it("never prints a link, an ETA, a percent or the word 'stuck'", () => {
    const all = [
      ...proxyErrors.map(proxyErrorMessage),
      ...FIXED_ERROR_WORDS.map(fixedErrorMessage),
      ...WORKBOOK_REFUSAL_CODES.map(workbookRefusalMessage),
      ...MAPPING_REFUSAL_CODES.map(mappingRefusalMessage),
      ...FOLDER_ERRORS.map(folderErrorMessage),
      ...FOLDER_PROBLEMS.map(folderProblemMessage),
      DRAFT_REFUSED_TEXT,
    ];
    for (const text of all) {
      expect(text).not.toMatch(/https?:|stuck|\bETA\b|%/i);
    }
  });

  it("describes a call that gave no reply by a fixed word", () => {
    expect(describeNonReply({ kind: "proxy_error", error: "timeout" }).code).toBe("timeout");
    expect(describeNonReply({ kind: "invalid_response" }).code).toBe("invalid_response");
    expect(describeNonReply({ kind: "rejected" }).code).toBe("rejected");
    expect(describeNonReply({ kind: "invalid_request" }).code).toBe("invalid_request");
    expect(describeFolderFailure({ kind: "folder_error", error: "overlap" }).code).toBe("overlap");
    expect(describeFolderFailure({ kind: "rejected" }).code).toBe("rejected");
    expect(describeFolderFailure({ kind: "invalid_response" }).code).toBe("invalid_response");
  });
});

describe("safeCode", () => {
  it("passes plain code words and refuses everything else", () => {
    expect(safeCode("run_record_not_kept")).toBe("run_record_not_kept");
    for (const bad of ["", "Has Space", "<b>x</b>", "a".repeat(65), "x-y", "ünï", "a\nb"]) {
      expect(safeCode(bad), bad).toBeNull();
    }
  });
});
