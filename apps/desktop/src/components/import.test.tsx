// The Import screen as static markup (chunk 65g; ADR-022 decisions 1 and 4; docs/DESIGN.md 7 and
// 11; threat model F36-1, F40, F44). No jsdom: every state is built with the reducer the hook
// drives (ledgerImport.ts) and rendered to a string. The write guarantee (nothing sent without the
// second step) is proved without a DOM in ledgerActions.test.ts; here the markup is checked.

import { renderToStaticMarkup } from "react-dom/server";
import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { ConfirmState } from "../confirm";
import {
  IMPORT_APPROVAL_LINE,
  IMPORT_DATE_STATEMENT,
  IMPORT_READS_ONLY,
  IMPORT_STEP_STATEMENT,
} from "../fixedTexts";
import { IMPORT_REFUSAL_CODES } from "../ipc/proxy";
import { reduceLedger, INITIAL_LEDGER_STATE, type CustomersView } from "../ledger";
import {
  INITIAL_IMPORT_STATE,
  currentFields,
  fieldsKey,
  importTarget,
  reduceImport,
  type ImportEvent,
  type ImportReport,
  type ImportState,
  type ImportTarget,
  type InputChange,
} from "../ledgerImport";
import { CANARY, customer, draftBody, importReport } from "../ledgerTestSupport";
import { importRefusalMessage, proxyErrorMessage } from "../messages";
import { visibleControls } from "../plainText";
import { errorOf, escapeHtml, replyOf, textOf } from "../testSupport";
import { ImportScreen } from "./ImportScreen";

const NOOP = () => undefined;
const DRAFT = draftBody();
const FILE = DRAFT.file_name;
const CUSTOMER_ID = "00000001000000000000000000000000";
const IDLE: ConfirmState<ImportTarget> = { phase: "idle" };

const WORDS = {
  approval:
    'Approval happens in the workbook: type "approved" in the Review column, save the file with Save (not Save As), then import it here.',
  readsOnly: "ALIBI only reads the workbook. It never saves it.",
  step: "Importing creates promises that cannot be deleted, only voided. A new customer cannot be removed.",
  date: "The date is your statement of when you sent these answers. ALIBI cannot check it.",
  preview: "this is a preview; nothing was saved or recorded",
} as const;

function run(events: ImportEvent[], from: ImportState = INITIAL_IMPORT_STATE): ImportState {
  return events.reduce(reduceImport, from);
}

function customers(list = [customer()]): CustomersView {
  const state = reduceLedger(INITIAL_LEDGER_STATE, {
    type: "customers_result",
    result: replyOf("ledger_customers", 200, list),
  });
  return state.customers;
}

function render(
  state: ImportState,
  confirm: ConfirmState<ImportTarget> = IDLE,
  customerView: CustomersView = customers(),
  enabled = true,
): string {
  return renderToStaticMarkup(
    <ImportScreen
      state={state}
      confirm={confirm}
      customers={customerView}
      enabled={enabled}
      onChooseFile={NOOP}
      onInputChange={NOOP}
      onPreview={NOOP}
      onOpenImport={NOOP}
      onConfirmImport={NOOP}
      onCancelImport={NOOP}
    />,
  );
}

function listed(files: string[] = [FILE]): ImportState {
  return run([{ type: "files_result", call: { kind: "ok", value: { kind: "exports", files, truncated: false } } }]);
}

const EXISTING: InputChange[] = [
  { kind: "existing_customer", customerId: CUSTOMER_ID },
  { kind: "sent_on", sentOn: "2026-10-10" },
];
const NEW_NAME = (name: string): InputChange[] => [
  { kind: "new_customer_name", name },
  { kind: "confirm_new", confirmNew: true },
  { kind: "sent_on", sentOn: "2026-10-10" },
];

function withDraft(changes: InputChange[] = EXISTING, from: ImportState = listed()): ImportState {
  return run(
    [
      { type: "file_selected", file: FILE },
      { type: "draft_requested" },
      { type: "draft_result", file: FILE, result: replyOf("draft_rows", 200, DRAFT) },
      ...changes.map((change): ImportEvent => ({ type: "input_changed", change })),
    ],
    from,
  );
}

function withPreview(state: ImportState, result = replyOf("ledger_import", 200, importReport("preview"))): ImportState {
  const fields = currentFields(state);
  if (fields === null) {
    throw new Error("inputs not valid");
  }
  const key = fieldsKey(fields);
  return run(
    [
      { type: "preview_requested", key },
      { type: "preview_result", key, fields, result },
    ],
    state,
  );
}

function previewed(changes: InputChange[] = EXISTING, report: ImportReport = importReport("preview")): ImportState {
  return withPreview(withDraft(changes), replyOf("ledger_import", 200, report));
}

function imported(report: ImportReport = importReport("created")): ImportState {
  return run(
    [{ type: "import_started" }, { type: "import_result", result: replyOf("ledger_import", 200, report) }],
    previewed(),
  );
}

function openConfirm(state: ImportState): ConfirmState<ImportTarget> {
  const target = importTarget(state);
  if (target === null) {
    throw new Error("no import target");
  }
  return { phase: "open", target };
}

function button(html: string, label: string): string {
  return new RegExp(`<button[^>]*>${label}</button>`).exec(html)?.[0] ?? "";
}

describe("the Import screen's fixed statements", () => {
  it("match the owner's approved DESIGN.md 11.1 words", () => {
    expect(IMPORT_APPROVAL_LINE).toBe(WORDS.approval);
    expect(IMPORT_READS_ONLY).toBe(WORDS.readsOnly);
    expect(IMPORT_STEP_STATEMENT).toBe(WORDS.step);
    expect(IMPORT_DATE_STATEMENT).toBe(WORDS.date);
  });

  it("the approval line, the reads-only line and the date line show in every state", () => {
    const states = [
      INITIAL_IMPORT_STATE,
      run([{ type: "files_result", call: { kind: "folder_error", error: "not_configured" } }]),
      listed([]),
      withDraft(),
      previewed(),
      imported(),
    ];
    for (const state of states) {
      const text = textOf(render(state));
      expect(text).toContain(WORDS.approval);
      expect(text).toContain(WORDS.readsOnly);
      expect(text).toContain(WORDS.date);
      // The irreversible-import statement belongs to the second step only.
      expect(text).not.toContain(WORDS.step);
    }
  });
});

describe("the import states", () => {
  it("loading names what is being read", () => {
    const text = textOf(render(run([{ type: "files_requested" }])));
    expect(text).toContain("Reading the file names in the exports folder.");
    const reading = run([{ type: "file_selected", file: FILE }, { type: "draft_requested" }], listed());
    expect(textOf(render(reading))).toContain("Reading the draft (reading only, the file is not changed).");
    expect(textOf(render(INITIAL_IMPORT_STATE, IDLE, { phase: "loading" }))).toContain("Reading the customers.");
  });

  it("no exports folder in use is a refusal with its code, never an empty list", () => {
    const html = render(run([{ type: "files_result", call: { kind: "folder_error", error: "not_configured" } }]));
    expect(textOf(html)).toContain("No exports folder is in use.");
    expect(html).toContain("export_root_not_configured");
    expect(textOf(html)).not.toContain("no .xlsx or .xlsm files in the folder");
    expect(html).not.toContain("Use this draft");
  });

  it("an empty folder says exactly what is empty", () => {
    expect(textOf(render(listed([])))).toContain("no .xlsx or .xlsm files in the folder");
  });

  it("lists the draft names as plain text with a choose button each", () => {
    const html = render(listed([FILE, "second.xlsx"]));
    expect(html.match(/>Use this draft<\/button>/g)).toHaveLength(2);
    expect(textOf(html)).toContain(FILE);
  });

  it("a loaded draft shows its file, its full run id and its question count", () => {
    const text = textOf(render(withDraft()));
    expect(text).toContain(`Draft: ${FILE}`);
    expect(text).toContain(`Run: ${DRAFT.run_id}`);
    expect(text).toContain(`Questions: ${DRAFT.question_count}`);
    expect(text).toContain("Selected");
  });

  it("ledger not configured shows the refusal in place of the customer list", () => {
    const view = reduceLedger(INITIAL_LEDGER_STATE, {
      type: "customers_result",
      result: errorOf("ledger_customers", 503, "ledger_root_not_configured"),
    }).customers;
    const html = render(withDraft([]), IDLE, view);
    expect(textOf(html)).toContain("No ledger folder is in use.");
    expect(html).toContain("ledger_root_not_configured");
    expect(html).not.toContain('id="import-existing-customer"');
  });

  it("a draft refusal shows its own words and integer counts, and no run id", () => {
    const cases: [object, string][] = [
      [{ code: "run_record_missing" }, "draft_refused:run_record_missing"],
      [{ code: "headers_changed" }, "draft_refused:headers_changed"],
      [{ code: "question_mismatch", counts: { mismatched_rows: 2 } }, "draft_refused:question_mismatch"],
    ];
    const seen = new Set<string>();
    for (const [extra, code] of cases) {
      const state = run(
        [
          { type: "file_selected", file: FILE },
          { type: "draft_requested" },
          { type: "draft_result", file: FILE, result: errorOf("draft_rows", 422, "draft_refused", extra) },
        ],
        listed(),
      );
      const html = render(state);
      expect(html, code).toContain(code);
      expect(textOf(html), code).not.toContain("Run: ");
      seen.add(textOf(html));
    }
    expect(seen.size).toBe(cases.length);
    expect(textOf(render(run(
      [
        { type: "file_selected", file: FILE },
        { type: "draft_requested" },
        { type: "draft_result", file: FILE, result: errorOf("draft_rows", 422, "draft_refused", { code: "question_mismatch", counts: { mismatched_rows: 2 } }) },
      ],
      listed(),
    )))).toContain("mismatched_rows : 2");
  });

  it("each input problem is said in its own words, and the preview stays off", () => {
    const cases: [InputChange[], string][] = [
      [[], "Choose an existing customer or type a new customer name, not both."],
      [[{ kind: "new_customer_name", name: "Acme" }, { kind: "sent_on", sentOn: "2026-10-10" }], "Tick the box to confirm the new customer."],
      [[{ kind: "new_customer_name", name: "a".repeat(1_001) }, { kind: "confirm_new", confirmNew: true }, { kind: "sent_on", sentOn: "2026-10-10" }], "The new customer name must be 1 to 1,000 characters."],
      [[{ kind: "existing_customer", customerId: CUSTOMER_ID }], "Enter the sent date as YYYY-MM-DD."],
      [[{ kind: "existing_customer", customerId: CUSTOMER_ID }, { kind: "sent_on", sentOn: "2026-02-30" }], "The sent date is not a real date in the form YYYY-MM-DD."],
    ];
    for (const [changes, words] of cases) {
      const html = render(withDraft(changes));
      expect(textOf(html), words).toContain(words);
      expect(button(html, "Preview the import"), words).toContain('disabled=""');
    }
    expect(textOf(render(listed()))).toContain("Choose a draft first.");
  });

  it("valid inputs turn the preview on and say the import needs a preview", () => {
    const html = render(withDraft());
    expect(button(html, "Preview the import")).not.toContain('disabled=""');
    expect(button(html, "Import")).toContain('disabled=""');
    expect(textOf(html)).toContain("Preview these exact choices first.");
  });

  it("the sent date is a text field showing exactly what is checked and sent, never cut short", () => {
    const html = render(withDraft([{ kind: "existing_customer", customerId: CUSTOMER_ID }, { kind: "sent_on", sentOn: "2026-10-101" }]));
    const field = /<input[^>]*id="import-sent-on"[^>]*>/.exec(html)?.[0] ?? "";
    expect(field).toContain('type="text"');
    expect(field).toContain('value="2026-10-101"');
    expect(field).not.toContain("maxlength");
    expect(field).toMatch(/autocomplete="off"/i);
    expect(textOf(html)).toContain("The sent date is not a real date in the form YYYY-MM-DD.");
  });

  it("file names and the run id are marked for a page translator to leave alone", () => {
    const state = previewed();
    const html = render(state, openConfirm(state));
    const escaped = escapeHtml(FILE);
    expect(html.split(`translate="no">${escaped}</span>`).length - 1).toBe(3);
    expect(html).toContain(`translate="no">${DRAFT.run_id}</span>`);
  });

  it("disables every control while the sidecar is not healthy", () => {
    const html = render(previewed(), IDLE, customers(), false);
    const buttons = html.match(/<button[^>]*>[^<]*<\/button>/g) ?? [];
    expect(buttons.length).toBeGreaterThan(0);
    for (const entry of buttons) {
      expect(entry, entry).toContain('disabled=""');
    }
    for (const field of html.match(/<(select|input)[^>]*>/g) ?? []) {
      expect(field, field).toContain('disabled=""');
    }
  });
});

describe("the import button and its two steps", () => {
  it("the import button is off before a preview and on after one for these inputs", () => {
    expect(button(render(withDraft()), "Import")).toContain('disabled=""');
    expect(button(render(previewed()), "Import")).not.toContain('disabled=""');
  });

  it("changing any input after a preview turns the import button off again", () => {
    const changes: InputChange[] = [
      { kind: "existing_customer", customerId: null },
      { kind: "new_customer_name", name: "Other" },
      { kind: "confirm_new", confirmNew: true },
      { kind: "sent_on", sentOn: "2026-10-11" },
    ];
    for (const change of changes) {
      const html = render(reduceImport(previewed(), { type: "input_changed", change }));
      expect(button(html, "Import"), change.kind).toContain('disabled=""');
      expect(textOf(html), change.kind).not.toContain("Preview result");
    }
    const otherFile = reduceImport(previewed(), { type: "file_selected", file: "second.xlsx" });
    expect(button(render(otherFile), "Import")).toContain('disabled=""');
  });

  it("the import button alone shows no confirm step and none of its words", () => {
    const html = render(previewed());
    expect(html).not.toContain("Confirm import");
    expect(textOf(html)).not.toContain(WORDS.step);
  });

  it("step one opens the in-page confirm with the section 11 words and the target as plain text", () => {
    const state = previewed(NEW_NAME("Acme Test Co"));
    const html = render(state, openConfirm(state));
    const text = textOf(html);
    expect(text).toContain(WORDS.step);
    expect(html).toContain(">Confirm import</button>");
    expect(text).toContain("Target:");
    expect(text).toContain(`Draft: ${FILE}`);
    expect(text).toContain(`Run: ${DRAFT.run_id}`);
    expect(text).toContain("Customer: Acme Test Co (new)");
    expect(text).toContain("Sent on (YYYY-MM-DD): 2026-10-10");
    expect(text).toContain("Promises to create (from the preview): 3");
    expect(html).not.toContain("<a ");
  });

  it("an existing customer in the confirm is shown by its name from the list", () => {
    const state = previewed();
    expect(textOf(render(state, openConfirm(state)))).toContain("Customer: Acme Test Co");
  });

  it("while the confirm is open every input, choice and step-one button is locked", () => {
    const state = previewed();
    const html = render(state, openConfirm(state));
    for (const field of html.match(/<(select|input)[^>]*>/g) ?? []) {
      expect(field, field).toContain('disabled=""');
    }
    for (const label of ["Use this draft", "Preview the import", "Import"]) {
      expect(button(html, label), label).toContain('disabled=""');
    }
    expect(button(html, "Confirm import")).not.toContain('disabled=""');
    expect(button(html, "Cancel")).not.toContain('disabled=""');
  });

  it("keeps a keyboard order: preview, import, confirm, cancel; all real buttons, no tabindex", () => {
    const state = previewed();
    const html = render(state, openConfirm(state));
    const order = [">Preview the import</button>", ">Import</button>", ">Confirm import</button>", ">Cancel</button>"].map(
      (part) => html.indexOf(part),
    );
    expect(order.every((index) => index > 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
    for (const entry of html.match(/<button[^>]*>/g) ?? []) {
      expect(entry, entry).toContain('type="button"');
    }
    expect(html).not.toContain("tabindex");
    expect(html).not.toContain("confirm(");
  });

  it("shows the sending line for an import and turns the buttons off", () => {
    const html = render(run([{ type: "import_started" }], previewed()));
    expect(textOf(html)).toContain("Importing.");
    expect(button(html, "Import")).toContain('disabled=""');
    expect(button(html, "Preview the import")).toContain('disabled=""');
  });
});

describe("a preview result is never shown as an import result", () => {
  it("a preview is headed as a preview and carries the nothing-was-saved line", () => {
    const html = render(previewed());
    const text = textOf(html);
    expect(text).toContain("Preview result");
    expect(text).toContain(WORDS.preview);
    expect(text).not.toContain("Import result");
    expect(html).toContain('data-report="preview"');
    expect(html).not.toContain('data-report="import"');
  });

  it("an import result is headed as an import and has no preview line", () => {
    const html = render(imported());
    const text = textOf(html);
    expect(text).toContain("Import result");
    expect(text).not.toContain("Preview result");
    expect(text).not.toContain(WORDS.preview);
    expect(html).toContain('data-report="import"');
  });

  it("a preview call answered with preview false shows an invalid answer, not a result", () => {
    const html = render(previewed(EXISTING, importReport("created")));
    expect(html).toContain("invalid_response");
    expect(textOf(html)).not.toContain("Import result");
    expect(textOf(html)).not.toContain("Preview result");
    expect(button(html, "Import")).toContain('disabled=""');
  });

  it("an import answered with preview true shows an invalid answer, not a result", () => {
    const html = render(imported(importReport("preview")));
    expect(html).toContain("invalid_response");
    expect(textOf(html)).not.toContain("Import result");
  });
});

describe("the counts, exactly as sent", () => {
  it("shows every count and code as the sidecar sent it, with no locale formatting", () => {
    const report = importReport("preview", {
      created: 1234567,
      created_edited: 2,
      skipped: { not_approved: 1, already_imported: 3 },
      review: { approved: 3, rejected: 1, unreviewed: 0, other: 0 },
      other_values: [{ text: "maybe later", truncated: true, count: 2 }],
      already_changed_since_draft: 5,
    });
    const text = textOf(render(previewed(EXISTING, report)));
    expect(text).toContain("Promises that would be created: 1234567");
    expect(text).not.toContain("1,234,567");
    expect(text).toContain("Of those, with an answer edited after drafting: 2");
    expect(text).toContain("not_approved : 1");
    expect(text).toContain("already_imported : 3");
    expect(text).toContain("approved : 3");
    expect(text).toContain("rejected : 1");
    expect(text).toContain("maybe later");
    expect(text).toContain("(Rows: 2)");
    expect(text).toContain("cut short by the sidecar");
    expect(text).toContain("Already changed since drafting: 5");
  });

  it("null already_changed_since_draft is not available, never 0", () => {
    const text = textOf(render(previewed(EXISTING, importReport("preview", { already_changed_since_draft: null }))));
    expect(text).toContain("Already changed since drafting: not available");
    expect(text).not.toContain("Already changed since drafting: 0");
    const zero = textOf(render(previewed(EXISTING, importReport("preview", { already_changed_since_draft: 0 }))));
    expect(zero).toContain("Already changed since drafting: 0");
  });

  it("a preview labels its counts as would-be and has no Customer created row", () => {
    const text = textOf(render(previewed()));
    expect(text).toContain("Promises that would be created: ");
    expect(text).toContain("Of those, with an answer edited after drafting: ");
    expect(text).not.toContain("Promises created");
    expect(text).not.toContain("Created with an answer edited after drafting");
    expect(text).not.toContain("Customer created");
  });

  it("a preview reply with customer_created false for a new customer never says Customer created: no", () => {
    const report = importReport("preview", { customer_created: false, customer_id: null });
    const text = textOf(render(previewed(EXISTING, report)));
    expect(text).not.toContain("Customer created: no");
    expect(text).not.toContain("Customer created");
    expect(text).toContain("Preview result");
  });

  it("an import result keeps the created labels and the Customer created row", () => {
    const text = textOf(render(imported()));
    expect(text).toContain("Promises created: ");
    expect(text).toContain("Created with an answer edited after drafting: ");
    expect(text).toContain("Customer created: ");
    expect(text).not.toContain("would be created");
    expect(text).toContain("Import result");
  });

  it("an import result shows the customer it created and its id", () => {
    const text = textOf(render(imported()));
    expect(text).toContain("Customer created: yes");
    expect(text).toContain(`Customer id: ${CUSTOMER_ID}`);
  });
});

describe("every typed refusal has its own words", () => {
  const cases: [ReturnType<typeof errorOf<"ledger_import">>, string][] = [
    ...IMPORT_REFUSAL_CODES.map(
      (code): [ReturnType<typeof errorOf<"ledger_import">>, string] => [
        errorOf("ledger_import", 422, "import_refused", { code }),
        `import_refused:${code}`,
      ],
    ),
    [errorOf("ledger_import", 422, "ledger_refused", { code: "customer_exists", customer_id: CUSTOMER_ID }), "ledger_refused:customer_exists"],
    [errorOf("ledger_import", 422, "ledger_refused", { code: "customer_unknown" }), "ledger_refused:customer_unknown"],
    [errorOf("ledger_import", 422, "ledger_refused", { code: "customer_name_invalid" }), "ledger_refused:customer_name_invalid"],
    [errorOf("ledger_import", 422, "workbook_refused", { code: "doctype" }), "workbook_refused:doctype"],
    [errorOf("ledger_import", 404, "file_not_found"), "file_not_found"],
    [errorOf("ledger_import", 400, "path_not_allowed"), "path_not_allowed"],
    [errorOf("ledger_import", 409, "run_in_progress"), "run_in_progress"],
    [errorOf("ledger_import", 503, "ledger_root_not_configured"), "ledger_root_not_configured"],
    [errorOf("ledger_import", 503, "export_root_not_configured"), "export_root_not_configured"],
    [errorOf("ledger_import", 503, "ledger_unusable"), "ledger_unusable"],
    [errorOf("ledger_import", 503, "ledger_store_error"), "ledger_store_error"],
    [{ kind: "invalid_response" }, "invalid_response"],
  ];

  it("a refused preview shows its code and words, and the import stays off", () => {
    const seen = new Set<string>();
    for (const [result, code] of cases) {
      const html = render(withPreview(withDraft(), result));
      expect(html, code).toContain(`<code class="font-mono">${code}</code>`);
      expect(button(html, "Import"), code).toContain('disabled=""');
      expect(textOf(html), code).not.toContain("Preview result");
      seen.add(textOf(html));
    }
    expect(seen.size).toBe(cases.length);
  });

  it("each import_refused code shows its message", () => {
    for (const code of IMPORT_REFUSAL_CODES) {
      const html = render(withPreview(withDraft(), errorOf("ledger_import", 422, "import_refused", { code })));
      expect(textOf(html), code).toContain(importRefusalMessage(code));
    }
  });

  it("a refused import shows the same words as a refused preview, as the outcome", () => {
    const state = run(
      [{ type: "import_started" }, { type: "import_result", result: errorOf("ledger_import", 422, "import_refused", { code: "sent_on_in_future" }) }],
      previewed(),
    );
    const html = render(state);
    expect(html).toContain("import_refused:sent_on_in_future");
    expect(textOf(html)).toContain("The sent date is after today.");
    expect(textOf(html)).not.toContain("Import result");
  });

  it("question mismatch shows integer counts only", () => {
    const html = render(
      withPreview(
        withDraft(),
        errorOf("ledger_import", 422, "import_refused", { code: "question_mismatch", counts: { mismatched_rows: 1, workbook_rows: 4 } }),
      ),
    );
    expect(textOf(html)).toContain("mismatched_rows : 1");
    expect(textOf(html)).toContain("workbook_rows : 4");
  });

  it("customer exists shows the id from the refusal as plain text, and never a name", () => {
    const name = "Acme Unique Name";
    const view = customers([customer({ customer_id: CUSTOMER_ID, display_name: name })]);
    const base = withDraft(NEW_NAME("acme unique name"));
    const before = render(base, IDLE, view);
    const html = render(
      withPreview(base, errorOf("ledger_import", 422, "ledger_refused", { code: "customer_exists", customer_id: CUSTOMER_ID })),
      IDLE,
      view,
    );
    expect(textOf(html)).toContain(`Existing customer id: ${CUSTOMER_ID}`);
    expect(textOf(html)).toContain("A customer with this name is already in the ledger.");
    // The refusal adds no customer name: the name appears only where it did before (the select).
    expect(html.split(name).length).toBe(before.split(name).length);
  });

  it("busy is 'another job is running'", () => {
    const html = render(withPreview(withDraft(), errorOf("ledger_import", 409, "run_in_progress")));
    expect(textOf(html)).toContain("another job is running");
  });

  it("every proxy error word has its words", () => {
    for (const error of ["not_ready", "unauthorized", "unreachable", "timeout", "too_large", "bad_request", "bad_response", "internal"] as const) {
      const html = render(
        withPreview(withDraft(), { kind: "proxy_error", error } as unknown as ReturnType<typeof errorOf<"ledger_import">>),
      );
      expect(textOf(html), error).toContain(proxyErrorMessage(error));
    }
  });
});

describe("personal data and hostile text (F36-1, F40)", () => {
  const METHODS = ["log", "info", "warn", "error", "debug", "trace", "dir", "table", "group"] as const;
  const spies = METHODS.map((method) => vi.spyOn(console, method).mockImplementation(() => undefined));
  beforeEach(() => {
    for (const spy of spies) {
      spy.mockClear();
    }
  });
  afterAll(() => {
    for (const spy of spies) {
      spy.mockRestore();
    }
  });

  it("a canary in a customer name, a file name and an other_values text is plain text, never a link, never logged", () => {
    const view = customers([customer({ display_name: CANARY })]);
    const fromFiles = listed([CANARY]);
    const chosen = run(
      [
        { type: "file_selected", file: CANARY },
        { type: "draft_requested" },
        { type: "draft_result", file: CANARY, result: replyOf("draft_rows", 200, DRAFT) },
        ...NEW_NAME(CANARY).map((change): ImportEvent => ({ type: "input_changed", change })),
      ],
      fromFiles,
    );
    const report = importReport("preview", { other_values: [{ text: CANARY, truncated: false, count: 1 }] });
    const state = withPreview(chosen, replyOf("ledger_import", 200, report));
    const existing = withPreview(
      run([{ type: "input_changed", change: { kind: "new_customer_name", name: "" } }, { type: "input_changed", change: { kind: "existing_customer", customerId: CUSTOMER_ID } }], chosen),
      replyOf("ledger_import", 200, report),
    );
    for (const [current, confirm] of [
      [fromFiles, IDLE],
      [state, IDLE],
      [state, openConfirm(state)],
      [existing, openConfirm(existing)],
    ] as const) {
      const html = render(current, confirm, view);
      expect(html).toContain(escapeHtml(visibleControls(CANARY)));
      expect(html).not.toContain("<a ");
      expect(html).not.toContain('href="');
      expect(html).not.toContain(CANARY);
    }
    for (const [index, spy] of spies.entries()) {
      expect(spy, METHODS[index]).not.toHaveBeenCalled();
    }
  });

  it("control and direction characters in a file name and a value are made visible", () => {
    // The characters are built from their code points, so none is written literally in this file.
    const rlo = String.fromCodePoint(0x202e);
    const lri = String.fromCodePoint(0x2066);
    const nul = String.fromCodePoint(0x0000);
    const name = `draft${rlo}test${nul}.xlsx`;
    const html = render(previewed(EXISTING, importReport("preview", { other_values: [{ text: `ok${lri}x`, truncated: false, count: 1 }] })), IDLE);
    expect(html).toContain("ok[U+2066]x");
    expect(html).not.toContain(lri);
    const files = render(listed([name]));
    expect(files).toContain("draft[U+202E]test[U+0000].xlsx");
    expect(files).not.toContain(rlo);
  });

  it("renders no style attribute, link, truncation or locale formatting in any state", () => {
    const states = [INITIAL_IMPORT_STATE, listed([]), withDraft(), previewed(), imported()];
    for (const state of states) {
      const html = render(state);
      expect(html).not.toMatch(/\sstyle=/);
      expect(html).not.toContain("<style");
      expect(html).not.toContain("<a ");
      expect(html).not.toMatch(/truncate|line-clamp/);
    }
    const state = previewed();
    expect(render(state, openConfirm(state))).not.toMatch(/\sstyle=|<a |truncate|line-clamp/);
    for (const spy of spies) {
      expect(spy).not.toHaveBeenCalled();
    }
  });
});
