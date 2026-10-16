// The review queue as static markup (ADR-021, DESIGN.md 4 and 5; threat model F36-1 to F36-3 and
// F37-1 to F37-4). No jsdom: every state is built with the reducer and rendered to a string. Each
// fixed statement is asserted verbatim in the state or row that owns it and absent elsewhere.

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
// The 11 hostile cell texts (CLAUDE.md 8.6). Imported, never pasted: this file holds none of them.
import cellsFile from "../../../../fixtures/adversarial-m5/questionnaire-cells.json";
import {
  APPROVAL_LINE,
  DRAFT_UNREADABLE_TEXT,
  EDITED_ANSWER_LINE,
  EDITED_EVIDENCE_LINE,
  EDITED_STATUS_LINE,
  HEADERS_CHANGED_TEXT,
  NO_WORKBOOKS,
  QUESTION_MISMATCH_TEXT,
  READING_ONLY,
  RECORDED_BY_ALIBI,
  REVIEW_SENTENCE,
  RUN_RECORD_MISSING_TEXT,
  UNREVIEWED_DRAFT_BADGE,
} from "../fixedTexts";
import { visibleControls } from "../plainText";
import {
  INITIAL_REVIEW_QUEUE_STATE,
  reduceReviewQueue,
  type DraftRow,
  type ReviewQueueEvent,
  type ReviewQueueState,
} from "../reviewQueue";
import { errorOf, escapeHtml, replyOf, textOf } from "../testSupport";
import { DraftRowView } from "./DraftRowView";
import { COLLAPSE_AFTER, firstPart } from "./LongText";
import { ReviewQueueScreen } from "./ReviewQueueScreen";

// ----- helpers --------------------------------------------------------------------------------

const NOOP = () => undefined;

// The statements as the owner wrote them, spelled out here so a change to fixedTexts.ts is a
// visible test edit.
const WORDS = {
  review:
    "The quote is real company text that was in the context. ALIBI has not checked that it answers this question.",
  editedStatus:
    "The Status cell in the workbook was changed after drafting. The code's verdict is shown, not the cell.",
  recorded: "recorded by ALIBI",
  editedAnswer:
    "The Answer cell was changed after drafting. The citation belongs to the original answer.",
  mismatch:
    "A question in this file was changed after ALIBI drafted it, so ALIBI cannot match the rows to its record and shows none of them. Open the original draft instead, or run the questionnaire again. (Code: question_mismatch)",
  approval:
    'This queue is for reading. ALIBI does not record approval. To approve an answer, type "approved" in the Review column of the workbook, then import it into the ledger (the import screen comes in a later chunk).',
  recordMissing:
    "ALIBI has no record of this draft, so it cannot show its answers with verified citations. This happens when the run was made without a ledger folder, or the file was renamed. Set the ledger folder, restart ALIBI and run again.",
  headers:
    "The ALIBI columns in this file were moved or renamed, so ALIBI cannot match the rows to its record.",
  unreadable: "ALIBI could not read this file as a draft.",
} as const;

function row(over: Partial<DraftRow> = {}): DraftRow {
  return {
    row: 1,
    question_text: "Do you encrypt data at rest?",
    status: "SUPPORTED",
    reasons: [],
    answer_text: "Yes, AES-256.",
    citation: {
      source_path: "policies/encryption.md",
      start_line: 4,
      end_line: 6,
      quote: "Data at rest is encrypted with AES-256.",
    },
    status_edited: false,
    answer_edited: false,
    evidence_edited: false,
    ...over,
  };
}

function draftBody(rows: DraftRow[], over: object = {}) {
  return {
    model: "llama3.1:8b",
    file_name: "q-UNREVIEWED-DRAFT-abcd1234.xlsx",
    complete: true,
    question_count: rows.length,
    rows,
    ...over,
  };
}

function play(events: ReviewQueueEvent[], from: ReviewQueueState = INITIAL_REVIEW_QUEUE_STATE) {
  return events.reduce(reduceReviewQueue, from);
}

const FILE = "q-UNREVIEWED-DRAFT-abcd1234.xlsx";

function listing(files: string[]): ReviewQueueEvent {
  return { type: "files_result", call: { kind: "ok", value: { kind: "exports", files, truncated: false } } };
}

function opened(rows: DraftRow[], body: object = {}, file = FILE): ReviewQueueState {
  return play([
    listing([file]),
    { type: "file_selected", file },
    { type: "draft_requested" },
    { type: "draft_result", file, result: replyOf("draft_rows", 200, draftBody(rows, body)) },
  ]);
}

function refused(result: ReturnType<typeof replyOf<"draft_rows">> | { kind: "proxy_error"; error: "unreachable" }) {
  return play([
    listing([FILE]),
    { type: "file_selected", file: FILE },
    { type: "draft_requested" },
    { type: "draft_result", file: FILE, result: result as never },
  ]);
}

const render = (state: ReviewQueueState, enabled = true) =>
  renderToStaticMarkup(<ReviewQueueScreen state={state} enabled={enabled} onOpenFile={NOOP} />);

const renderRow = (r: DraftRow) => renderToStaticMarkup(<ul><DraftRowView row={r} /></ul>);

function occurrences(haystack: string, needle: string): number {
  return haystack.split(needle).length - 1;
}

/** Things the queue may never contain, in any state. */
function expectSafeMarkup(html: string, shownCopies: string[] = []): void {
  let rest = html;
  for (const copy of shownCopies) {
    rest = rest.split(copy).join("");
  }
  expect(rest).not.toMatch(/\sstyle=/i);
  expect(rest).not.toMatch(/<style|<script|<link|<img|<iframe|<a[\s>]|javascript:/i);
  expect(rest).not.toMatch(/\s(?:href|src|srcset|action|formaction|xlink:href)=/i);
  expect(rest).not.toMatch(/\son[a-z]+=/i);
  expect(rest).not.toMatch(/dangerouslySetInnerHTML|innerHTML/);
  expect(rest).not.toMatch(/<progress|role="progressbar"|<meter/i);
}

const CELLS: Array<{ id: string; text: string }> = (
  cellsFile.cells as Array<{ id: string; text: string; repeat_unit?: string; repeat_count?: number }>
).map((cell) => ({
  id: cell.id,
  text:
    cell.text !== "" || cell.repeat_unit === undefined
      ? cell.text
      : cell.repeat_unit.repeat(cell.repeat_count ?? 1),
}));

// ----- the fixed words ------------------------------------------------------------------------

describe("the fixed statements are exactly the owner's words", () => {
  it("matches each constant to its literal", () => {
    expect(REVIEW_SENTENCE).toBe(WORDS.review);
    expect(EDITED_STATUS_LINE).toBe(WORDS.editedStatus);
    expect(RECORDED_BY_ALIBI).toBe(WORDS.recorded);
    expect(EDITED_ANSWER_LINE).toBe(WORDS.editedAnswer);
    expect(QUESTION_MISMATCH_TEXT).toBe(WORDS.mismatch);
    expect(APPROVAL_LINE).toBe(WORDS.approval);
    expect(RUN_RECORD_MISSING_TEXT).toBe(WORDS.recordMissing);
    expect(HEADERS_CHANGED_TEXT).toBe(WORDS.headers);
    expect(DRAFT_UNREADABLE_TEXT).toBe(WORDS.unreadable);
    expect(EDITED_EVIDENCE_LINE).toBe(
      "The Evidence cell in the workbook was changed after drafting. The citation shown is ALIBI's record, not the cell.",
    );
  });
});

// ----- the screen's states --------------------------------------------------------------------

describe("the screen's file list states", () => {
  it("not configured names the exports folder and is not an empty list", () => {
    const state = play([
      { type: "files_requested" },
      { type: "files_result", call: { kind: "folder_error", error: "not_configured" } },
    ]);
    const text = textOf(render(state));
    expect(text).toContain("No exports folder is in use.");
    expect(text).toContain("export_root_not_configured");
    expect(text).not.toContain(NO_WORKBOOKS);
  });

  it("empty says what is empty", () => {
    const text = textOf(render(play([listing([])])));
    expect(text).toContain(NO_WORKBOOKS);
    expect(text).not.toContain("Open this draft");
  });

  it("loading says what is being read", () => {
    expect(textOf(render(play([{ type: "files_requested" }])))).toContain(
      "Reading the file names in the exports folder.",
    );
    expect(textOf(render(INITIAL_REVIEW_QUEUE_STATE))).toContain(
      "Reading the file names in the exports folder.",
    );
  });

  it("lists the names with an Open button each, disabled when not healthy", () => {
    const state = play([listing(["a.xlsx", "b.xlsm"])]);
    const html = render(state);
    expect(occurrences(html, "Open this draft")).toBe(2);
    expect(html).not.toMatch(/disabled/);
    expect(render(state, false)).toMatch(/<button[^>]*disabled/);
  });

  it("a host failure is a fixed message with its code", () => {
    const state = play([{ type: "files_result", call: { kind: "folder_error", error: "unavailable" } }]);
    expect(textOf(render(state))).toContain("That folder is not available");
  });
});

describe("the approval line (F37-3)", () => {
  it("is shown in every state, in full, and nowhere else", () => {
    const states = [
      INITIAL_REVIEW_QUEUE_STATE,
      play([listing([])]),
      play([{ type: "files_result", call: { kind: "folder_error", error: "not_configured" } }]),
      opened([row()]),
      refused(errorOf("draft_rows", 422, "draft_refused", { code: "headers_changed" })),
      play([listing([FILE]), { type: "file_selected", file: FILE }, { type: "draft_requested" }]),
    ];
    for (const state of states) {
      const text = textOf(render(state));
      expect(text).toContain(WORDS.approval);
      expect(occurrences(text, WORDS.approval)).toBe(1);
    }
    expect(textOf(renderRow(row()))).not.toContain(WORDS.approval);
  });

  it("has no approve control and no writing control", () => {
    for (const html of [render(opened([row(), row({ row: 2, status: "PARTIAL" })])), render(play([listing([FILE])]))]) {
      const buttons = (html.match(/<button[^>]*>[^<]*<\/button>/g) ?? []).map((b) => b.replace(/<[^>]*>/g, ""));
      for (const label of buttons) {
        expect(label).toMatch(/^(Open this draft|Show more|Show less)$/);
      }
      expect(html).not.toMatch(/<input|<select|<textarea|<form/i);
    }
  });
});

describe("a draft that is being read or was read", () => {
  it("says it is reading, without a bar, a percent or an ETA", () => {
    const html = render(play([listing([FILE]), { type: "file_selected", file: FILE }, { type: "draft_requested" }]));
    expect(textOf(html)).toContain(READING_ONLY);
    expect(textOf(html)).not.toMatch(/stuck|\bETA\b|remaining|estimated|%/i);
    expectSafeMarkup(html);
  });

  it("shows the header: badge, file, model, question count and whether the run was complete", () => {
    const html = render(opened([row(), row({ row: 2 })], { complete: false, question_count: 2 }));
    const text = textOf(html);
    expect(text).toContain(UNREVIEWED_DRAFT_BADGE);
    expect(text).toContain(`File: ${FILE}`);
    expect(text).toContain("Model: llama3.1:8b");
    expect(text).toContain("Questions: 2");
    expect(text).toContain("Run: incomplete (the run did not finish)");
    expect(textOf(render(opened([row()])))).toContain("Run: complete");
  });
});

describe("whole-queue refusals show their fixed message, the code, and no rows", () => {
  const cases: Array<[string, string, string, string, number]> = [
    ["question_mismatch", WORDS.mismatch, "draft_refused:question_mismatch", "", 422],
    ["run_record_missing", WORDS.recordMissing, "draft_refused:run_record_missing", "", 422],
    ["headers_changed", WORDS.headers, "draft_refused:headers_changed", "", 422],
    ["unreadable", WORDS.unreadable, "draft_refused:unreadable", "", 422],
    ["not_a_zip", WORDS.unreadable, "draft_refused:not_a_zip", "", 422],
    ["already_has_answer_columns", WORDS.unreadable, "draft_refused:already_has_answer_columns", "", 422],
  ];
  for (const [code, words, shownCode, , status] of cases) {
    it(`${code}`, () => {
      const html = render(refused(errorOf("draft_rows", status, "draft_refused", { code })));
      const text = textOf(html);
      expect(text).toContain(words);
      expect(html).toContain(`<code class="font-mono">${shownCode}</code>`);
      expect(html).not.toContain("data-row=");
      expect(text).not.toContain(WORDS.review);
      expect(text).toContain(WORDS.approval);
      expectSafeMarkup(html);
    });
  }

  it("question_mismatch shows integer counts when present, and only integers", () => {
    const html = render(
      refused(
        errorOf("draft_rows", 422, "draft_refused", {
          code: "question_mismatch",
          counts: { rows_record: 3, rows_file: 4, "Not A Code <b>": 7 },
        }),
      ),
    );
    const text = textOf(html);
    expect(text).toContain("rows_record : 3");
    expect(text).toContain("rows_file : 4");
    expect(html).toContain('<code class="font-mono">rows_record</code>: 3');
    expect(html).not.toContain("<b>");
    expect(text).not.toContain("Not A Code");
  });

  it("each of the other whole-queue refusals has its own fixed message", () => {
    const pairs: Array<[number, string, string]> = [
      [503, "ledger_root_not_configured", "No ledger folder is in use."],
      [503, "export_root_not_configured", "No exports folder is in use."],
      [404, "file_not_found", "That file was not found in its folder."],
      [400, "path_not_allowed", "That file name is not allowed."],
      [503, "ledger_unusable", "The ledger folder is in use but ALIBI cannot use it."],
    ];
    for (const [status, word, words] of pairs) {
      const html = render(refused(errorOf("draft_rows", status, word)));
      expect(textOf(html), word).toContain(words);
      expect(html, word).toContain(`<code class="font-mono">${word}</code>`);
      expect(html, word).not.toContain("data-row=");
    }
  });

  it("a host error is a fixed message with its code", () => {
    const html = render(refused({ kind: "proxy_error", error: "unreachable" }));
    expect(textOf(html)).toContain("ALIBI could not reach the sidecar.");
    expect(html).toContain('<code class="font-mono">unreachable</code>');
    expect(html).not.toContain("data-row=");
  });

  it("the three ledger statements appear only for their own code", () => {
    const html = render(refused(errorOf("draft_rows", 422, "draft_refused", { code: "headers_changed" })));
    const text = textOf(html);
    expect(text).toContain(WORDS.headers);
    expect(text).not.toContain(WORDS.mismatch);
    expect(text).not.toContain(WORDS.recordMissing);
    expect(textOf(render(opened([row()])))).not.toContain(WORDS.headers);
    expect(textOf(render(opened([row()])))).not.toContain(WORDS.mismatch);
  });
});

// ----- the rows -------------------------------------------------------------------------------

describe("a row (F37-1)", () => {
  it("SUPPORTED and PARTIAL rows always carry the review sentence, in body text", () => {
    for (const status of ["SUPPORTED", "PARTIAL"]) {
      const html = renderRow(row({ status }));
      expect(textOf(html), status).toContain(WORDS.review);
      expect(occurrences(textOf(html), WORDS.review), status).toBe(1);
      // Not in an attribute, a tooltip or a collapsed section.
      expect(html).not.toMatch(/title=|<details|<summary|hidden>|\shidden[\s=]/);
      // The sentence sits in a text node of a fixed-statement block, not in any attribute.
      expect(html).toMatch(/<p class="alibi-block[^"]*">The quote is real company text/);
    }
  });

  it("holds even for a SUPPORTED row with no citation or answer", () => {
    expect(textOf(renderRow(row({ citation: null, answer_text: null })))).toContain(WORDS.review);
  });

  it("a NEEDS_REVIEW row has it only when it carries a quote", () => {
    expect(textOf(renderRow(row({ status: "NEEDS_REVIEW", citation: null, answer_text: null })))).not.toContain(
      WORDS.review,
    );
    expect(textOf(renderRow(row({ status: "NEEDS_REVIEW" })))).toContain(WORDS.review);
  });

  it("shows every reason as a code word, none hidden, others as 'unrecognised reason'", () => {
    const reasons = ["review_overdue", "coverage_partial", "keyword_only", "Not A Code <b>", "no_evidence"];
    const html = renderRow(row({ status: "PARTIAL", reasons }));
    for (const code of ["review_overdue", "coverage_partial", "keyword_only", "no_evidence"]) {
      expect(html).toContain(`<code class="font-mono">${code}</code>`);
    }
    expect(textOf(html)).toContain("unrecognised reason");
    expect(html).not.toContain("<b>");
    expect(textOf(html)).not.toContain("Not A Code");
    expect(textOf(renderRow(row({ reasons: [] })))).toContain("Reasons: none recorded.");
  });

  it("shows the row number, question, labelled draft answer, source, lines and quote", () => {
    const text = textOf(renderRow(row({ row: 12 })));
    expect(text).toContain("Row 12");
    expect(text).toContain("Question: Do you encrypt data at rest?");
    expect(text).toContain("Draft answer (unreviewed draft text)");
    expect(text).toContain(UNREVIEWED_DRAFT_BADGE);
    expect(text).toContain("Yes, AES-256.");
    expect(text).toContain("Source: policies/encryption.md");
    expect(text).toContain("Lines 4 to 6");
    expect(text).toContain("Quote: Data at rest is encrypted with AES-256.");
  });

  it("says plainly when there is no answer or no citation", () => {
    const text = textOf(renderRow(row({ status: "NEEDS_REVIEW", answer_text: null, citation: null })));
    expect(text).toContain("No draft answer was written for this row.");
    expect(text).toContain("No citation was recorded for this row.");
  });

  it("an unknown status is the error block, never a label, and the value is not echoed", () => {
    const html = renderRow(row({ status: "APPROVED <b>x</b>" }));
    expect(textOf(html)).toContain("Unknown status");
    expect(html).not.toContain("APPROVED");
    expect(html).not.toContain("data-status");
    expect(html).toContain('role="alert"');
  });

  it("the status shown is the record's (data-status), for each known status", () => {
    for (const status of ["SUPPORTED", "PARTIAL", "NEEDS_REVIEW"]) {
      expect(renderRow(row({ status }))).toContain(`data-status="${status}"`);
    }
  });
});

describe("edited rows (F37-4)", () => {
  it("status_edited: the record's status, labelled, with the fixed line; no cell text", () => {
    // The Answer cell text is a decoy that looks like a verdict: it is shown only as an answer.
    const html = renderRow(row({ status: "NEEDS_REVIEW", status_edited: true, answer_text: "SUPPORTED" }));
    const text = textOf(html);
    expect(html).toContain('data-status="NEEDS_REVIEW"');
    expect(html).not.toContain('data-status="SUPPORTED"');
    expect(text).toContain(WORDS.recorded);
    expect(text).toContain(WORDS.editedStatus);
    expect(text).not.toContain(WORDS.editedAnswer);
  });

  it("the edited lines are absent on unedited rows", () => {
    const text = textOf(renderRow(row()));
    expect(text).not.toContain(WORDS.recorded);
    expect(text).not.toContain(WORDS.editedStatus);
    expect(text).not.toContain(WORDS.editedAnswer);
    expect(text).not.toContain(EDITED_EVIDENCE_LINE);
  });

  it("answer_edited: the answer, the fixed line, and the record's citation", () => {
    const text = textOf(renderRow(row({ answer_edited: true, answer_text: "Changed by hand" })));
    expect(text).toContain("Changed by hand");
    expect(text).toContain(WORDS.editedAnswer);
    expect(text).toContain("policies/encryption.md");
    expect(text).toContain("Data at rest is encrypted with AES-256.");
    expect(text).not.toContain(WORDS.editedStatus);
  });

  it("evidence_edited: the warning that the citation is ALIBI's record, not the cell", () => {
    const text = textOf(renderRow(row({ evidence_edited: true })));
    expect(text).toContain(EDITED_EVIDENCE_LINE);
    expect(text).toContain("policies/encryption.md");
    expect(textOf(renderRow(row()))).not.toContain(EDITED_EVIDENCE_LINE);
  });

  it("a whole queue: an edited SUPPORTED row sorts first and still carries the review sentence", () => {
    const html = render(
      opened([
        row({ row: 1, status: "SUPPORTED" }),
        row({ row: 2, status: "SUPPORTED", status_edited: true }),
      ]),
    );
    expect(html.indexOf('data-row="2"')).toBeLessThan(html.indexOf('data-row="1"'));
    expect(occurrences(textOf(html), WORDS.review)).toBe(2);
    expect(occurrences(textOf(html), WORDS.editedStatus)).toBe(1);
  });
});

describe("ordering (ADR-021 decision 7)", () => {
  const order = (html: string) => [...html.matchAll(/data-row="(\d+)"/g)].map((m) => Number(m[1]));

  it("edited-status and NEEDS_REVIEW first, then PARTIAL, then SUPPORTED, stable by row number", () => {
    const rows = [
      row({ row: 1, status: "SUPPORTED" }),
      row({ row: 2, status: "PARTIAL" }),
      row({ row: 3, status: "NEEDS_REVIEW" }),
      row({ row: 4, status: "SUPPORTED", status_edited: true }),
      row({ row: 5, status: "PARTIAL" }),
      row({ row: 6, status: "NEEDS_REVIEW" }),
      row({ row: 7, status: "SUPPORTED" }),
    ];
    expect(order(render(opened(rows)))).toEqual([3, 4, 6, 2, 5, 1, 7]);
    // The order does not depend on the order the sidecar sent.
    expect(order(render(opened([...rows].reverse())))).toEqual([3, 4, 6, 2, 5, 1, 7]);
  });

  it("an unknown status is read first, as an error block", () => {
    const html = render(opened([row({ row: 1 }), row({ row: 2, status: "WEIRD" })]));
    expect(order(html)).toEqual([2, 1]);
  });
});

// ----- long and hostile text ------------------------------------------------------------------

describe("long text (F36-3)", () => {
  const long = "word ".repeat(200);

  it("collapses a long question, answer and quote behind 'Show more'", () => {
    const html = renderRow(
      row({
        question_text: long,
        answer_text: long,
        citation: { source_path: "p.md", start_line: 1, end_line: 2, quote: long },
      }),
    );
    expect(occurrences(html, "Show more")).toBe(3);
    expect(html).toContain('aria-expanded="false"');
    expect(html).not.toContain(long);
  });

  it("never collapses the fixed statements, status words or reasons", () => {
    const html = renderRow(
      row({ status: "PARTIAL", status_edited: true, answer_edited: true, evidence_edited: true, reasons: ["review_overdue"] }),
    );
    expect(html).not.toContain("Show more");
    for (const words of [WORDS.review, WORDS.editedStatus, WORDS.editedAnswer, EDITED_EVIDENCE_LINE]) {
      expect(textOf(html)).toContain(words);
    }
    expect(textOf(html)).toContain("Partial");
    expect(html).toContain('<code class="font-mono">review_overdue</code>');
  });

  it("a short text has no control", () => {
    expect(renderRow(row())).not.toContain("Show more");
  });

  it("shows direction and control characters as visible marks, in isolated direction", () => {
    const bidi = `a${String.fromCodePoint(0x202e)}b${String.fromCodePoint(0x200b)}c${String.fromCodePoint(0x2066)}`;
    const html = renderRow(row({ question_text: bidi, answer_text: bidi }));
    expect(html).toContain("a[U+202E]b[U+200B]c[U+2066]");
    expect(html).not.toContain(String.fromCodePoint(0x202e));
    expect(html).not.toContain(String.fromCodePoint(0x200b));
    expect(html).toContain("unicode-bidi:isolate");
  });
});

describe("the 11 hostile cell texts as question, answer, quote and file name (F36-1)", () => {
  it("has the 11 texts", () => {
    expect(CELLS).toHaveLength(11);
    expect(CELLS.every((cell) => cell.text.length > 0)).toBe(true);
  });

  for (const cell of CELLS) {
    it(`${cell.id}: escaped text, no raw tags, no link`, () => {
      const shownText = firstPart(cell.text, COLLAPSE_AFTER);
      const shown = escapeHtml(visibleControls(shownText));
      const hostileRow = row({
        question_text: cell.text,
        answer_text: cell.text,
        citation: { source_path: cell.text, start_line: 1, end_line: 2, quote: cell.text },
      });
      const html = render(
        opened([hostileRow], { file_name: cell.text, model: cell.text }, cell.text),
      );
      const names = render(play([listing([cell.text])]));
      for (const out of [html, names, renderRow(hostileRow)]) {
        expectSafeMarkup(out, [shown, escapeHtml(visibleControls(cell.text))]);
        expect(out).toContain(shown.length > 0 ? shown : "");
        if (/[<>&"']/.test(shownText)) {
          expect(out).not.toContain(shownText);
        }
      }
      // A hostile reason code is replaced by a fixed phrase, and a hostile status is an error block.
      const asCode = renderRow(row({ status: cell.text, reasons: [cell.text] }));
      expect(textOf(asCode)).toContain("unrecognised reason");
      expect(textOf(asCode)).toContain("Unknown status");
      expect(asCode).not.toContain(escapeHtml(cell.text));
    });
  }
});

describe("the queue never shows progress furniture", () => {
  it("has no <progress>, bar, ETA word or percent in any state", () => {
    const states = [
      INITIAL_REVIEW_QUEUE_STATE,
      play([listing([FILE])]),
      play([listing([FILE]), { type: "file_selected", file: FILE }, { type: "draft_requested" }]),
      opened([row(), row({ row: 2, status: "PARTIAL" }), row({ row: 3, status: "NEEDS_REVIEW" })]),
      refused(errorOf("draft_rows", 422, "draft_refused", { code: "question_mismatch", counts: { rows_file: 2 } })),
    ];
    for (const state of states) {
      const html = render(state);
      expectSafeMarkup(html);
      expect(textOf(html)).not.toMatch(/stuck|\bETA\b|remaining|estimated|left to go|%/i);
    }
  });
});
