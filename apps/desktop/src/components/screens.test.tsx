// F6-1b, F35-6, F36-1, F37-5: what the three 6g screens show for each state, rendered to static
// markup (no jsdom, no browser). Every fixed statement is asserted verbatim in the state that owns
// it and absent in the states that do not.

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
// The 11 hostile cell texts (CLAUDE.md 8.6). Imported, never pasted: this file holds none of them.
import cellsFile from "../../../../fixtures/adversarial-m5/questionnaire-cells.json";
import {
  BUSY_STATEMENT,
  LEDGER_FOLDER_WARNING,
  NO_LEDGER_BEFORE_START,
  NO_RECORD_AFTER_RUN,
  NO_WORKBOOKS,
  READING_ONLY,
  RUN_SPEED_LINE,
  SAVED_STATEMENT,
  UNKNOWN_INDEX_JOB,
  UNKNOWN_RUN,
  UNREVIEWED_DRAFT_BADGE,
  WRITING_FILE,
} from "../fixedTexts";
import { foldersScreenView, type FoldersScreenView, type PickState } from "../foldersScreen";
import {
  INITIAL_INDEX_STATE,
  reduceIndex,
  type IndexEvent,
  type IndexState,
} from "../indexJob";
import { FOLDER_KINDS, type FolderCall, type FolderView, type FoldersReply } from "../ipc/folders";
import { NO_DOWNLOAD_STATEMENT, type ReadinessView } from "../readiness";
import { visibleControls } from "../plainText";
import {
  INITIAL_QUESTIONNAIRE_STATE,
  reduceQuestionnaire,
  type QuestionnaireEvent,
  type QuestionnaireState,
} from "../questionnaireRun";
import { errorOf, escapeHtml, replyOf, textOf } from "../testSupport";
import { FoldersScreen } from "./FoldersScreen";
import { IndexScreen } from "./IndexScreen";
import { QuestionnaireScreen } from "./QuestionnaireScreen";

// ----- shared helpers -------------------------------------------------------------------------

const NOOP = () => undefined;
const READY: ReadinessView = { state: "ready" };
const DEGRADED: ReadinessView = {
  state: "degraded",
  models: ["llama3.1:8b", "nomic-embed-text"],
  ollama: "reachable",
};

/** Things no 6g screen may ever contain, in any state. */
function expectSafeMarkup(html: string): void {
  expect(html).not.toMatch(/\sstyle=/i);
  expect(html).not.toMatch(/<style|<script|<link|<img|<iframe|<a[\s>]|href=|javascript:/i);
  // No attribute can carry a URL. (Hostile TEXT may contain one; it is shown, not followed.)
  expect(html).not.toMatch(/\s(?:href|src|srcset|action|formaction|xlink:href)=/i);
  expect(html).not.toMatch(/\son[a-z]+=/i);
  expect(html).not.toMatch(/dangerouslySetInnerHTML|innerHTML/);
}

/** For states built from benign data: no remote URL anywhere in the markup. */
function expectNoRemoteUrl(html: string): void {
  expect(html).not.toMatch(/https?:\/\//i);
}

/** The words a progress display must never use (DESIGN.md 4.5). */
function expectNoEtaOrPercent(html: string): void {
  const text = textOf(html);
  expect(text).not.toMatch(/stuck/i);
  expect(text).not.toMatch(/\bETA\b|remaining|estimated|left to go/i);
  expect(text).not.toMatch(/%/);
  expect(html).not.toMatch(/<progress|role="progressbar"|<meter/i);
}

interface Cell {
  id: string;
  text: string;
  repeat_unit?: string;
  repeat_count?: number;
}
const CELLS: Array<{ id: string; text: string }> = (cellsFile.cells as Cell[]).map((cell) => ({
  id: cell.id,
  text:
    cell.text !== "" || cell.repeat_unit === undefined
      ? cell.text
      : cell.repeat_unit.repeat(cell.repeat_count ?? 1),
}));

function folderView(kind: FolderView["kind"], over: Partial<FolderView> = {}): FolderView {
  return { kind, in_use: null, saved: null, problem: null, start_problem: null, ...over };
}
function foldersCall(folders: FolderView[], over: Partial<FoldersReply> = {}): FolderCall<FoldersReply> {
  return { kind: "ok", value: { ready: true, saved_file: "ok", saved_file_at_start: "ok", folders, ...over } };
}
const IDLE: PickState = { phase: "idle" };

function renderFolders(view: FoldersScreenView, pick: PickState = IDLE, enabled = true): string {
  return renderToStaticMarkup(<FoldersScreen view={view} pick={pick} enabled={enabled} onChoose={NOOP} />);
}

// ----- Folders --------------------------------------------------------------------------------

describe("Folders screen", () => {
  const allSet = foldersCall(FOLDER_KINDS.map((k) => folderView(k, { in_use: `/data/${k}`, saved: `/data/${k}` })));

  it("shows five rows in the order vault, ledger, questionnaires, exports, imports", () => {
    const html = renderFolders(foldersScreenView(allSet));
    const positions = ["Vault", "Ledger", "Questionnaires", "Exports", "Imports"].map((label) =>
      html.indexOf(`<h3 class="text-lg font-semibold">${label}`),
    );
    expect(positions.every((p) => p > 0)).toBe(true);
    expect([...positions].sort((a, b) => a - b)).toEqual(positions);
    expect(html.match(/<h3/g)).toHaveLength(5);
  });

  it("marks the ledger row 'needed for the review queue' and no other row", () => {
    const text = textOf(renderFolders(foldersScreenView(allSet)));
    expect(text).toContain("Ledger (needed for the review queue)");
    expect(text.match(/needed for the review queue/g)).toHaveLength(1);
  });

  it("shows the ledger warning verbatim, once, in body text, unless the ledger folder is in use now", () => {
    // Nothing set: shown once, in body text.
    const none = renderFolders(foldersScreenView(foldersCall([])));
    expect(textOf(none)).toContain(LEDGER_FOLDER_WARNING);
    expect(textOf(none).split(LEDGER_FOLDER_WARNING)).toHaveLength(2);
    expect(none).not.toMatch(/title=|aria-describedby|<details|hidden/i);
    // Only saved for the next launch: still shown, because a run now keeps no record.
    const saved = renderFolders(foldersScreenView(foldersCall([folderView("ledger", { saved: "/l" })])));
    expect(textOf(saved)).toContain(LEDGER_FOLDER_WARNING);
    expect(textOf(saved).split(LEDGER_FOLDER_WARNING)).toHaveLength(2);
    expect(textOf(saved)).toContain("applies the next time you start ALIBI");
    // In use now: the host has said so, and the warning is gone.
    const inUse = renderFolders(foldersScreenView(allSet));
    expect(textOf(inUse)).not.toContain(LEDGER_FOLDER_WARNING);
    expect(textOf(inUse)).toContain("Ledger (needed for the review queue)");
  });

  it("shows the ledger warning while the folders are loading, not ready, or could not be read", () => {
    const states: Array<[string, FolderCall<FoldersReply> | undefined]> = [
      ["loading", undefined],
      ["not ready", foldersCall([folderView("ledger", { in_use: "/l" })], { ready: false })],
      ["rejected", { kind: "rejected" }],
      ["invalid response", { kind: "invalid_response" }],
      ["folder error", { kind: "folder_error", error: "unavailable" }],
    ];
    for (const [name, call] of states) {
      const text = textOf(renderFolders(foldersScreenView(call)));
      expect(text, name).toContain(LEDGER_FOLDER_WARNING);
      expect(text.split(LEDGER_FOLDER_WARNING), name).toHaveLength(2);
    }
  });

  it("brings the ledger warning back when the ledger folder is no longer in use", () => {
    const inUse = foldersCall([folderView("ledger", { in_use: "/l", saved: "/l" })]);
    const cleared = foldersCall([folderView("ledger")]);
    expect(textOf(renderFolders(foldersScreenView(inUse)))).not.toContain(LEDGER_FOLDER_WARNING);
    expect(textOf(renderFolders(foldersScreenView(cleared)))).toContain(LEDGER_FOLDER_WARNING);
  });

  it("shows 'In use now' and 'Saved for next launch' for every row, with the values apart when they differ", () => {
    const html = renderFolders(foldersScreenView(allSet));
    expect(html.match(/In use now/g)).toHaveLength(5);
    expect(html.match(/Saved for next launch/g)).toHaveLength(5);
    expect(textOf(html)).toContain("the same folder");
  });

  it("never shows a saved folder as in use (F35-6)", () => {
    const html = renderFolders(
      foldersScreenView(foldersCall([folderView("exports", { saved: "/only/saved", in_use: null })])),
    );
    const row = html.slice(html.indexOf(">Exports"), html.indexOf(">Imports"));
    const [inUse, saved] = row.split("Saved for next launch");
    expect(inUse).toContain("In use now");
    expect(inUse).not.toContain("/only/saved");
    expect(saved).toContain("/only/saved");
    expect(inUse).toContain("not set");
  });

  it("shows different in-use and saved paths separately", () => {
    const html = renderFolders(
      foldersScreenView(foldersCall([folderView("vault", { in_use: "/old/vault", saved: "/new/vault" })])),
    );
    const row = html.slice(html.indexOf(">Vault"), html.indexOf(">Ledger"));
    const [inUse, saved] = row.split("Saved for next launch");
    expect(inUse).toContain("/old/vault");
    expect(inUse).not.toContain("/new/vault");
    expect(saved).toContain("/new/vault");
  });

  it("shows each typed problem and start problem as words plus the code", () => {
    for (const problem of ["unavailable", "not_absolute", "overlap"] as const) {
      const html = renderFolders(
        foldersScreenView(
          foldersCall([folderView("imports", { saved: "/x", problem, start_problem: problem })]),
        ),
      );
      expect(html).toContain(`<code class="font-mono">${problem}</code>`);
      expect(textOf(html)).toContain("The saved folder cannot be used");
      expect(textOf(html)).toContain("It was not used at this start");
    }
  });

  it("warns when the saved folders file cannot be read", () => {
    const html = renderFolders(
      foldersScreenView(foldersCall([], { saved_file: "invalid", saved_file_at_start: "invalid" })),
    );
    expect(textOf(html)).toContain("every saved folder will be ignored");
    expect(textOf(html)).toContain("no saved folder is in use");
  });

  it("the Choose button names a kind only: no path, no value attribute", () => {
    const html = renderFolders(foldersScreenView(allSet));
    const buttons = html.match(/<button[^>]*>Choose<\/button>/g) ?? [];
    expect(buttons).toHaveLength(5);
    for (const kind of ["Vault", "Ledger", "Questionnaires", "Exports", "Imports"]) {
      expect(html).toContain(`aria-label="Choose the ${kind} folder"`);
    }
    for (const button of buttons) {
      const opening = /^<button[^>]*>/.exec(button)?.[0] ?? "";
      expect(opening).not.toMatch(/value=|data-|href|\//);
    }
  });

  it("disables Choose while the sidecar is not healthy and while a dialog is open", () => {
    const view = foldersScreenView(allSet);
    for (const html of [
      renderFolders(view, IDLE, false),
      renderFolders(view, { phase: "picking", kind: "vault" }),
    ]) {
      const buttons = html.match(/<button[^>]*>Choose<\/button>/g) ?? [];
      expect(buttons).toHaveLength(5);
      for (const button of buttons) expect(button).toContain("disabled");
    }
    for (const button of renderFolders(view).match(/<button[^>]*>Choose<\/button>/g) ?? []) {
      expect(button).not.toContain("disabled");
    }
  });

  it("shows the saved statement verbatim only after a folder was saved", () => {
    const view = foldersScreenView(allSet);
    expect(textOf(renderFolders(view, { phase: "saved", kind: "vault", display: "/chosen" }))).toContain(SAVED_STATEMENT);
    expect(textOf(renderFolders(view, { phase: "saved", kind: "vault", display: "/chosen" }))).toContain("/chosen");
    for (const pick of [IDLE, { phase: "cancelled" } as PickState, { phase: "picking", kind: "vault" } as PickState]) {
      expect(textOf(renderFolders(view, pick))).not.toContain(SAVED_STATEMENT);
    }
  });

  it("has distinct empty, loading, not-ready and error states, never 'not set' for unread data", () => {
    const loading = renderFolders(foldersScreenView(undefined));
    expect(textOf(loading)).toContain("Reading the folder settings");
    const notReady = renderFolders(foldersScreenView(foldersCall([], { ready: false })));
    expect(textOf(notReady)).toContain("has not decided the folders yet");
    for (const html of [loading, notReady]) {
      expect(textOf(html)).not.toMatch(/0 results/i);
      // No row, so no "In use now: not set" claim is made about data nobody has read.
      expect(html).not.toContain("In use now");
      expect(html).not.toContain("<h3");
    }
    const failed = renderFolders(foldersScreenView({ kind: "folder_error", error: "save_failed" }));
    expect(failed).toContain('role="alert"');
    expect(failed).toContain("save_failed");
    const cancelled = renderFolders(foldersScreenView(allSet), { phase: "cancelled" });
    expect(textOf(cancelled)).toContain("No folder was chosen");
    const pickFailed = renderFolders(foldersScreenView(allSet), {
      phase: "failed",
      failure: { category: "error", text: "t".repeat(20), code: "overlap", models: [], showNoDownload: false },
    });
    expect(pickFailed).toContain('role="alert"');
  });

  it("shows host paths as escaped plain text (F36-1)", () => {
    for (const cell of CELLS) {
      const html = renderFolders(
        foldersScreenView(foldersCall([folderView("vault", { in_use: cell.text, saved: cell.text })])),
        { phase: "saved", kind: "vault", display: cell.text },
      );
      expectSafeMarkup(html);
      expect(html, cell.id).toContain(escapeHtml(visibleControls(cell.text)));
    }
  });

  for (const [name, html] of [
    ["loaded", renderFolders(foldersScreenView(allSet))],
    ["failed", renderFolders(foldersScreenView({ kind: "rejected" }))],
  ] as const) {
    it(`renders ${name} without an inline style, script, link or remote URL`, () => {
      expectSafeMarkup(html);
      expectNoRemoteUrl(html);
    });
  }
});

// ----- Index ----------------------------------------------------------------------------------

describe("Index screen", () => {
  const JOB = "0123456789abcdef";
  const status = (over: object = {}) =>
    replyOf("vault_index_status", 200, {
      job_id: JOB,
      status: "running",
      files_done: 2,
      files_total: 9,
      files_indexed: 1,
      files_unchanged: 1,
      files_removed: 0,
      files_failed: 0,
      chunks_embedded: 14,
      failure: null,
      ...over,
    });
  const play = (events: IndexEvent[]): IndexState => events.reduce(reduceIndex, INITIAL_INDEX_STATE);
  const started: IndexEvent[] = [
    { type: "start_requested" },
    { type: "start_result", result: replyOf("vault_index_start", 202, { job_id: JOB }), nowMs: 10_000 },
  ];

  const render = (
    state: IndexState,
    over: { nowMs?: number; readiness?: ReadinessView; vault?: boolean | null; enabled?: boolean } = {},
  ) =>
    renderToStaticMarkup(
      <IndexScreen
        state={state}
        nowMs={over.nowMs ?? 10_000}
        readiness={over.readiness ?? READY}
        vaultInUse={over.vault === undefined ? true : over.vault}
        enabled={over.enabled ?? true}
        onStart={NOOP}
      />,
    );
  // The class attribute is dropped so a `disabled:` class cannot stand in for the real attribute.
  const startButton = (html: string) =>
    (/<button[^>]*>Index the vault<\/button>/.exec(html)?.[0] ?? "").replace(/class="[^"]*"/, "");

  it("idle: the button is on and nothing else claims a result", () => {
    const html = render(INITIAL_INDEX_STATE);
    expect(startButton(html)).not.toContain("disabled");
    expect(textOf(html)).not.toMatch(/Files done|Index finished|0 results/);
  });

  it("no vault folder in use: says so with its code and turns the button off", () => {
    const html = render(INITIAL_INDEX_STATE, { vault: false });
    expect(textOf(html)).toContain("No vault folder is in use.");
    expect(html).toContain("vault_root_not_configured");
    expect(startButton(html)).toContain("disabled");
    // The button names the reason on screen, so a screen reader reads why it is off.
    expect(startButton(html)).toContain('aria-describedby="index-vault-reason"');
    expect(html).toMatch(/<p id="index-vault-reason" role="alert"/);
  });

  it("vault not read yet: says so, never 'no vault'; the button is off and described by that line", () => {
    const html = render(INITIAL_INDEX_STATE, { vault: null });
    expect(textOf(html)).toContain("The vault folder has not been read yet.");
    expect(textOf(html)).not.toContain("No vault folder is in use");
    expect(startButton(html)).toContain("disabled");
    expect(startButton(html)).toContain('aria-describedby="index-vault-unread"');
    expect(html).toContain('<p id="index-vault-unread">The vault folder has not been read yet.</p>');
  });

  it("models missing: aria-describedby names the models block id", () => {
    const html = render(INITIAL_INDEX_STATE, { readiness: DEGRADED });
    expect(startButton(html)).toContain('aria-describedby="index-models-reason"');
    expect(html).toContain('<div id="index-models-reason" role="alert"');
  });

  it("an enabled button has no aria-describedby", () => {
    const html = render(INITIAL_INDEX_STATE);
    expect(startButton(html)).not.toContain("disabled");
    expect(startButton(html)).not.toContain("aria-describedby");
  });

  it("models missing: exact names, the no-download sentence, the button off with that reason", () => {
    const html = render(INITIAL_INDEX_STATE, { readiness: DEGRADED });
    expect(textOf(html)).toContain(NO_DOWNLOAD_STATEMENT);
    expect(html).toContain("llama3.1:8b");
    expect(html).toContain("nomic-embed-text");
    expect(startButton(html)).toContain("disabled");
    expect(textOf(render(INITIAL_INDEX_STATE))).not.toContain(NO_DOWNLOAD_STATEMENT);
  });

  it("not healthy: the button is off", () => {
    expect(startButton(render(INITIAL_INDEX_STATE, { enabled: false }))).toContain("disabled");
  });

  it("running: files done / total, counts and a local counter; no ETA, no percent; button off", () => {
    const state = reduceIndex(play(started), { type: "status_result", result: status() });
    const html = render(state, { nowMs: 17_900 });
    const text = textOf(html);
    expect(text).toContain("Files done: 2 / 9");
    expect(text).toContain("Running for 7 s.");
    expect(text).toContain("Chunks embedded: 14");
    expect(startButton(html)).toContain("disabled");
    expectNoEtaOrPercent(html);
  });

  it("starting is its own state", () => {
    const html = render(play([{ type: "start_requested" }]));
    expect(textOf(html)).toContain("Starting the index job.");
    expect(startButton(html)).toContain("disabled");
  });

  it("finished: complete, incomplete and failed have different words; the button is on again", () => {
    const words: Record<string, string> = {
      complete: "Index finished.",
      incomplete: "Index finished with problems.",
      failed: "Index failed.",
    };
    for (const [outcome, word] of Object.entries(words)) {
      const state = reduceIndex(play(started), { type: "status_result", result: status({ status: outcome, failure: outcome === "failed" ? "embedding_failed" : null }) });
      const html = render(state);
      expect(textOf(html)).toContain(word);
      for (const other of Object.values(words).filter((w) => w !== word)) {
        expect(textOf(html)).not.toContain(other);
      }
      expect(startButton(html)).not.toContain("disabled");
    }
    const failed = reduceIndex(play(started), { type: "status_result", result: status({ status: "failed", failure: "embedding_failed" }) });
    expect(render(failed)).toContain('<code class="font-mono">embedding_failed</code>');
  });

  it("never prints a failure that is not a plain code word", () => {
    const state = reduceIndex(play(started), {
      type: "status_result",
      result: status({ status: "failed", failure: CELLS[1]?.text ?? "x" }),
    });
    const html = render(state);
    expect(textOf(html)).toContain("Index failed.");
    expect(html).not.toContain("Code:");
    expectSafeMarkup(html);
  });

  it("busy (409 run_in_progress): 'another job is running', one message, button on, nothing running", () => {
    const state = play([
      { type: "start_requested" },
      { type: "start_result", result: errorOf("vault_index_start", 409, "run_in_progress"), nowMs: 1 },
    ]);
    const html = render(state);
    expect(textOf(html)).toContain(BUSY_STATEMENT);
    expect(html).toContain("run_in_progress");
    expect(textOf(html)).not.toMatch(/Files done|Running for/);
  });

  it("model refusal from the sidecar: names plus the no-download sentence", () => {
    const state = play([
      { type: "start_requested" },
      {
        type: "start_result",
        result: errorOf("vault_index_start", 503, "models_unavailable", { models_missing: ["nomic-embed-text"] }),
        nowMs: 1,
      },
    ]);
    const html = render(state);
    expect(html).toContain("nomic-embed-text");
    expect(textOf(html)).toContain(NO_DOWNLOAD_STATEMENT);
  });

  it("each other refusal and the unknown job have their own words", () => {
    const refusals: Array<[number, string]> = [
      [503, "vault_root_not_configured"],
      [503, "retrieval_unavailable"],
      [503, "ledger_root_not_configured"],
    ];
    const seen = new Set<string>();
    for (const [code, word] of refusals) {
      const html = render(
        play([
          { type: "start_requested" },
          { type: "start_result", result: errorOf("vault_index_start", code, word), nowMs: 1 },
        ]),
      );
      expect(html).toContain(word);
      seen.add(textOf(html));
    }
    expect(seen.size).toBe(refusals.length);
    const unknown = reduceIndex(play(started), { type: "status_result", result: errorOf("vault_index_status", 404, "run_not_found") });
    expect(textOf(render(unknown))).toContain(UNKNOWN_INDEX_JOB);
    for (const result of [
      { kind: "proxy_error", error: "timeout" } as const,
      { kind: "invalid_response" } as const,
    ]) {
      const html = render(play([{ type: "start_requested" }, { type: "start_result", result, nowMs: 1 }]));
      expect(html).toContain('role="alert"');
    }
  });

  it("every state renders without an inline style, script, link or remote URL", () => {
    const states = [
      INITIAL_INDEX_STATE,
      play([{ type: "start_requested" }]),
      play(started),
      reduceIndex(play(started), { type: "status_result", result: status({ status: "complete" }) }),
    ];
    for (const state of states) {
      expectSafeMarkup(render(state));
      expectNoRemoteUrl(render(state));
    }
  });

  it("shows missing model names from a refusal as escaped text, never markup (F36-1)", () => {
    for (const cell of CELLS) {
      const state = play([
        { type: "start_requested" },
        {
          type: "start_result",
          result: errorOf("vault_index_start", 503, "models_unavailable", { models_missing: [cell.text] }),
          nowMs: 1,
        },
      ]);
      const html = render(state);
      expectSafeMarkup(html);
      expect(html, cell.id).toContain(escapeHtml(visibleControls(cell.text)));
    }
  });
});

// ----- Questionnaire --------------------------------------------------------------------------

describe("Questionnaire screen", () => {
  const RUN = "00ff00ff00ff";
  const inspectBody = (over: object = {}) => ({
    sheets: [
      { name: "Sheet1", rows: 40, columns: 5 },
      { name: "Other", rows: 3, columns: 2 },
    ],
    suggestion: { sheet: "Sheet1", header_row: 2, question_col: 3, id_col: 1 },
    macro_enabled: false,
    parts: {},
    warnings: [],
    sha256: "0123456789abcdef".repeat(4),
    ...over,
  });
  const summaryBody = (over: object = {}) => ({
    model: "llama3.1:8b",
    digest_before: null,
    digest_after: null,
    template_sha256: "t",
    input_sha256: "i",
    complete: true,
    counts: { SUPPORTED: 2, PARTIAL: 1, NEEDS_REVIEW: 3 },
    reason_counts: { review_overdue: 1 },
    file_name: "q-UNREVIEWED-DRAFT-00ff00ff.xlsx",
    rows_written: 6,
    dropped_parts: [],
    warnings: [],
    seconds: 12.34,
    ...over,
  });
  const statusBody = (over: object = {}) => ({
    run_id: RUN,
    status: "running",
    total: 6,
    completed: 2,
    failure: null,
    summary: null,
    ...over,
  });
  const play = (events: QuestionnaireEvent[], from = INITIAL_QUESTIONNAIRE_STATE): QuestionnaireState =>
    events.reduce(reduceQuestionnaire, from);

  const listing = (files: string[]): QuestionnaireEvent => ({
    type: "files_result",
    call: { kind: "ok", value: { kind: "questionnaires", files, truncated: false } },
  });
  const inspected = (body: object = inspectBody(), files = ["a.xlsx"]): QuestionnaireEvent[] => [
    listing(files),
    { type: "file_selected", file: files[0] ?? "a.xlsx" },
    { type: "inspect_requested" },
    { type: "inspect_result", result: replyOf("questionnaire_inspect", 200, body) },
  ];
  const ready = play(inspected());
  const start: QuestionnaireEvent[] = [
    { type: "start_requested" },
    { type: "start_result", result: replyOf("questionnaire_start", 202, { run_id: RUN, total: 6 }), nowMs: 1000 },
  ];
  const running = play(start, ready);
  const poll = (state: QuestionnaireState, body: object): QuestionnaireState =>
    reduceQuestionnaire(state, { type: "status_result", result: replyOf("questionnaire_status", 200, statusBody(body)), nowMs: 31_000 });
  const done = (body: object = {}, summary: object = summaryBody()) =>
    poll(running, { status: "complete", completed: 6, summary, ...body });

  const render = (
    state: QuestionnaireState,
    over: { nowMs?: number; readiness?: ReadinessView; ledger?: boolean | null; enabled?: boolean } = {},
  ) =>
    renderToStaticMarkup(
      <QuestionnaireScreen
        state={state}
        nowMs={over.nowMs ?? 31_000}
        readiness={over.readiness ?? READY}
        ledgerInUse={over.ledger === undefined ? true : over.ledger}
        enabled={over.enabled ?? true}
        onSelectFile={NOOP}
        onInspect={NOOP}
        onMappingChange={NOOP}
        onStart={NOOP}
        onGoToIndex={NOOP}
        onDismissRun={NOOP}
        onOpenInQueue={NOOP}
      />,
    );
  const startButton = (html: string) => /<button[^>]*>(?:Start the run|Run anyway)<\/button>/.exec(html)?.[0] ?? "";

  describe("the file list", () => {
    it("loading says what is being read", () => {
      expect(textOf(render(INITIAL_QUESTIONNAIRE_STATE))).toContain(
        "Reading the file names in the questionnaires folder.",
      );
    });

    it("an empty folder says exactly that, never an empty list", () => {
      const html = render(play([listing([])]));
      expect(textOf(html)).toContain(NO_WORKBOOKS);
      expect(html).not.toContain("<ul");
    });

    it("a missing questionnaires folder is 'not configured' with how to fix it", () => {
      const html = render(play([{ type: "files_result", call: { kind: "folder_error", error: "not_configured" } }]));
      expect(textOf(html)).toContain("No questionnaires folder is in use.");
      expect(textOf(html)).toContain("takes effect the next time you start ALIBI");
      expect(textOf(html)).not.toContain(NO_WORKBOOKS);
    });

    it("lists the names with a button per file", () => {
      const html = render(play([listing(["a.xlsx", "b.xlsm"])]));
      expect(html).toContain("a.xlsx");
      expect(html).toContain("b.xlsm");
      expect(html.match(/Use this file/g)).toHaveLength(2);
    });

    it("says when the list was cut short", () => {
      const state = play([
        { type: "files_result", call: { kind: "ok", value: { kind: "questionnaires", files: ["a.xlsx"], truncated: true } } },
      ]);
      expect(textOf(render(state))).toContain("The list was cut short");
    });
  });

  describe("inspect and the mapping", () => {
    it("loading says the file is not changed", () => {
      const state = play([listing(["a.xlsx"]), { type: "file_selected", file: "a.xlsx" }, { type: "inspect_requested" }]);
      expect(textOf(render(state))).toContain(READING_ONLY);
    });

    it("every workbook refusal code shows its own message and code", () => {
      const seen = new Set<string>();
      for (const code of [
        "file_too_large", "uncompressed_too_large", "too_many_parts", "ratio_too_high", "not_a_zip",
        "legacy_or_encrypted", "unsupported_type", "no_workbook_part", "unreadable", "doctype", "entity",
        "utf32", "encoding_not_allowed", "duplicate_part",
      ]) {
        const state = play([
          listing(["a.xlsx"]),
          { type: "file_selected", file: "a.xlsx" },
          { type: "inspect_requested" },
          { type: "inspect_result", result: errorOf("questionnaire_inspect", 422, "workbook_refused", { code }) },
        ]);
        const html = render(state);
        expect(html).toContain(`workbook_refused:${code}`);
        seen.add(textOf(html));
      }
      expect(seen.size).toBe(14);
    });

    it("shows the sheets, the suggestion, the macro note and the dropped-parts limitation", () => {
      const html = render(play(inspected(inspectBody({ macro_enabled: true, warnings: ["macro_enabled_package"] }))));
      const text = textOf(html);
      expect(text).toContain("Sheet1");
      expect(text).toContain("(40 rows, 5 columns)");
      expect(text).toContain("ALIBI suggests this mapping.");
      expect(text).toContain("This is a macro-enabled workbook.");
      expect(text).toContain("images and charts in the workbook may be dropped");
      expect(html).toContain('<code class="font-mono">macro_enabled_package</code>');
    });

    it("with no suggestion says so", () => {
      const html = render(play(inspected(inspectBody({ suggestion: null }))));
      expect(textOf(html)).toContain("ALIBI has no suggestion.");
    });

    it("shows a mapping problem and turns Start off", () => {
      const bad = reduceQuestionnaire(ready, {
        type: "mapping_changed",
        mapping: { sheet: "Sheet1", headerRow: 0, questionCol: 3, idCol: null },
      });
      const html = render(bad);
      expect(textOf(html)).toContain("The header row must be a whole number from 1 to 1048576.");
      expect(startButton(html)).toContain("disabled");
      expect(startButton(render(ready))).not.toContain("disabled");
    });
  });

  describe("before Start (F37-5)", () => {
    it("no ledger folder in use: the exact warning and 'Run anyway'", () => {
      const html = render(ready, { ledger: false });
      expect(textOf(html)).toContain(NO_LEDGER_BEFORE_START);
      expect(startButton(html)).toContain("Run anyway");
      expect(startButton(html)).not.toContain("disabled");
    });

    it("ledger folder in use: no warning, plain 'Start the run'", () => {
      const html = render(ready, { ledger: true });
      expect(textOf(html)).not.toContain("No ledger folder is in use.");
      expect(startButton(html)).toContain("Start the run");
    });

    it("ledger state unknown: the warning is shown (it cannot be confirmed)", () => {
      expect(textOf(render(ready, { ledger: null }))).toContain(NO_LEDGER_BEFORE_START);
    });

    it("the ledger warning follows ledgerInUse: gone when it is in use, back when it is not", () => {
      expect(textOf(render(ready, { ledger: true }))).not.toContain(NO_LEDGER_BEFORE_START);
      expect(textOf(render(ready, { ledger: false }))).toContain(NO_LEDGER_BEFORE_START);
    });

    it("models missing: names, the no-download sentence and Start off", () => {
      const html = render(ready, { readiness: DEGRADED });
      expect(textOf(html)).toContain(NO_DOWNLOAD_STATEMENT);
      expect(html).toContain("nomic-embed-text");
      expect(startButton(html)).toContain("disabled");
    });

    it("the sidecar not healthy turns Start off", () => {
      expect(startButton(render(ready, { enabled: false }))).toContain("disabled");
    });

    it("the warning is not shown once a run is going", () => {
      expect(textOf(render(running, { ledger: false }))).not.toContain("No ledger folder is in use.");
    });
  });

  describe("refusals of the start, each with its own fixed message", () => {
    const refusals: Array<[number, string, object]> = [
      [409, "run_in_progress", {}],
      [409, "index_not_built", {}],
      [409, "vault_changed_reindex", {}],
      [503, "retrieval_unavailable", {}],
      [503, "models_unavailable", { models_missing: ["llama3.1:8b"] }],
      [503, "questionnaire_root_not_configured", {}],
      [503, "export_root_not_configured", {}],
      [422, "mapping_refused", { code: "no_mapping" }],
      [422, "mapping_refused", { code: "sheet_not_found" }],
      [422, "mapping_refused", { code: "bad_position" }],
      [422, "mapping_refused", { code: "sheet_too_large" }],
      [422, "mapping_refused", { code: "too_many_questions" }],
      [422, "mapping_refused", { code: "already_has_answer_columns" }],
      [422, "workbook_refused", { code: "unreadable" }],
      [404, "file_not_found", {}],
      [400, "path_not_allowed", {}],
    ];
    const texts = new Set<string>();
    for (const [status, word, extra] of refusals) {
      const label = `${word}${"code" in extra ? `:${(extra as { code: string }).code}` : ""}`;
      it(`${label}`, () => {
        const state = play(
          [
            { type: "start_requested" },
            { type: "start_result", result: errorOf("questionnaire_start", status, word, extra), nowMs: 1 },
          ],
          ready,
        );
        const html = render(state);
        expect(html).toContain('role="alert"');
        expect(html).toContain(label);
        const message = /<p class="font-semibold text-status-error">(.*?)<\/p>/.exec(html)?.[1] ?? "";
        expect(message.length, label).toBeGreaterThan(10);
        expect(texts.has(message), `duplicate message for ${label}`).toBe(false);
        texts.add(message);
        expectSafeMarkup(html);
      });
    }

    it("busy says 'another job is running' and does not retry", () => {
      const html = render(
        play(
          [
            { type: "start_requested" },
            { type: "start_result", result: errorOf("questionnaire_start", 409, "run_in_progress"), nowMs: 1 },
          ],
          ready,
        ),
      );
      expect(textOf(html)).toContain(BUSY_STATEMENT);
      expect(textOf(html)).toContain("does not retry by itself");
      expect(textOf(html)).not.toContain("Answered:");
    });

    it("a stale or missing index offers the way to the Index screen", () => {
      for (const word of ["index_not_built", "vault_changed_reindex"]) {
        const html = render(
          play([{ type: "start_requested" }, { type: "start_result", result: errorOf("questionnaire_start", 409, word), nowMs: 1 }], ready),
        );
        expect(html).toContain("Go to Index the vault");
      }
      const other = render(
        play([{ type: "start_requested" }, { type: "start_result", result: errorOf("questionnaire_start", 409, "run_in_progress"), nowMs: 1 }], ready),
      );
      expect(other).not.toContain("Go to Index the vault");
    });

    it("models refused by the sidecar: names plus the no-download sentence", () => {
      const html = render(
        play(
          [
            { type: "start_requested" },
            { type: "start_result", result: errorOf("questionnaire_start", 503, "models_unavailable", { models_missing: ["llama3.1:8b"] }), nowMs: 1 },
          ],
          ready,
        ),
      );
      expect(html).toContain("llama3.1:8b");
      expect(textOf(html)).toContain(NO_DOWNLOAD_STATEMENT);
    });
  });

  describe("a run in progress", () => {
    it("shows completed / total, the local counter and the fixed speed line verbatim", () => {
      const html = render(poll(running, { completed: 2 }), { nowMs: 61_999 });
      const text = textOf(html);
      expect(text).toContain("Answered: 2 / 6");
      expect(text).toContain("Time since the last answer: 30 s");
      expect(text).toContain(RUN_SPEED_LINE);
      expectNoEtaOrPercent(html);
    });

    it("says 'writing the answers file' when completed equals total, and not before", () => {
      expect(textOf(render(poll(running, { completed: 6 })))).toContain(WRITING_FILE);
      expect(textOf(render(poll(running, { completed: 5 })))).not.toContain(WRITING_FILE);
      expect(textOf(render(poll(running, { completed: 6 })))).not.toMatch(/Run finished|UNREVIEWED/);
    });

    it("locks the controls", () => {
      const html = render(running);
      expect(startButton(html)).toContain("disabled");
      expect(html).toMatch(/<fieldset[^>]*disabled/);
      expect(html.match(/Use this file/g) ?? []).toHaveLength(0);
      for (const button of html.match(/<button[^>]*>(?:Read the sheets)<\/button>/g) ?? []) {
        expect(button).toContain("disabled");
      }
    });

    it("the speed line is not shown outside a running run", () => {
      for (const state of [ready, done(), play(start.slice(0, 1), ready)]) {
        expect(textOf(render(state))).not.toContain(RUN_SPEED_LINE);
      }
    });

    it("a failed status check shows a fixed message and the run goes on", () => {
      const state = reduceQuestionnaire(running, {
        type: "status_result",
        result: { kind: "proxy_error", error: "timeout" },
        nowMs: 5,
      });
      const html = render(state);
      expect(html).toContain('role="alert"');
      expect(textOf(html)).toContain("The sidecar did not answer in time.");
      expect(textOf(html)).toContain("Answered:");
    });
  });

  describe("a finished run", () => {
    it("shows counts per status with labels, reasons, file name, seconds and the badge", () => {
      const html = render(done());
      const text = textOf(html);
      expect(text).toContain("Run finished");
      expect(text).toContain(UNREVIEWED_DRAFT_BADGE);
      expect(text).toContain("Supported : 2");
      expect(text).toContain("Partial : 1");
      expect(text).toContain("Needs review : 3");
      expect(html).toContain('<code class="font-mono">review_overdue</code>');
      expect(text).toContain("q-UNREVIEWED-DRAFT-00ff00ff.xlsx");
      expect(text).toContain("Took 12.3 s.");
      expect(text).toContain("Rows written: 6");
      expect(text).toContain("No parts of the workbook were dropped.");
      expectNoEtaOrPercent(html);
    });

    it("an unknown status key is an error block and its text is never printed", () => {
      const html = render(done({}, summaryBody({ counts: { SUPPORTED: 1, "APPROVED-BY-ALIBI": 4 } })));
      expect(html).toContain("Unknown status");
      expect(html).not.toContain("APPROVED-BY-ALIBI");
    });

    it("lists dropped parts exactly", () => {
      const html = render(done({}, summaryBody({ dropped_parts: [["xl/media/image1.png", 1, 0]] })));
      expect(textOf(html)).toContain("xl/media/image1.png (1 before, 0 after)");
      expect(textOf(html)).not.toContain("No parts of the workbook were dropped.");
    });

    it("run_record_not_kept: the exact after-run text and no way into the queue", () => {
      for (const warning of ["run_record_not_kept", "run_record_failed"]) {
        const html = render(done({}, summaryBody({ warnings: [warning] })));
        expect(textOf(html)).toContain(NO_RECORD_AFTER_RUN);
        expect(html).not.toContain("Open in review queue");
        expect(html).not.toContain("Warning:");
      }
    });

    it("with a run record the after-run text is absent and the queue action is enabled", () => {
      const html = render(done());
      expect(textOf(html)).not.toContain(NO_RECORD_AFTER_RUN);
      expect(html).toMatch(/<button[^>]*>Open in review queue<\/button>/);
      expect(html).not.toMatch(/<button[^>]*disabled[^>]*>Open in review queue<\/button>/);
    });

    it("the queue action is off while the sidecar is not healthy", () => {
      const html = render(done(), { enabled: false });
      expect(html).toMatch(/<button[^>]*disabled[^>]*>Open in review queue<\/button>/);
    });

    it("no answers file written: no way into the queue", () => {
      const html = render(done({}, summaryBody({ file_name: null })));
      expect(html).not.toContain("Open in review queue");
    });

    it("other warnings appear only as plain code words", () => {
      const html = render(done({}, summaryBody({ warnings: ["images_dropped", "Not A Code <b>"] })));
      expect(html).toContain('<code class="font-mono">images_dropped</code>');
      expect(textOf(html)).toContain("unrecognised warning");
      expect(html).not.toContain("Not A Code");
    });

    it("stopped says the model went away and the rest were left blank; the badge still shows", () => {
      const html = render(poll(running, { status: "stopped", completed: 2, summary: summaryBody({ complete: false }) }));
      const text = textOf(html);
      expect(text).toContain("Run stopped early");
      expect(text).toContain("the rest of the questions were left blank");
      expect(text).toContain("Answered: 2 / 6");
      expect(text).toContain(UNREVIEWED_DRAFT_BADGE);
    });

    it("failed shows its code word and no badge when no draft exists", () => {
      const html = render(poll(running, { status: "failed", failure: "generation_failed" }));
      expect(textOf(html)).toContain("Run failed");
      expect(html).toContain('<code class="font-mono">generation_failed</code>');
      expect(textOf(html)).toContain("No summary was returned.");
      expect(textOf(html)).not.toContain(UNREVIEWED_DRAFT_BADGE);
    });

    it("an unknown run shows the exact text from the design", () => {
      const state = reduceQuestionnaire(running, {
        type: "status_result",
        result: errorOf("questionnaire_status", 404, "run_not_found"),
        nowMs: 1,
      });
      expect(textOf(render(state))).toContain(UNKNOWN_RUN);
      expect(textOf(render(ready))).not.toContain(UNKNOWN_RUN);
    });
  });

  describe("untrusted text is escaped plain text (F36-1)", () => {
    it("has the 11 hostile cell texts", () => {
      expect(CELLS).toHaveLength(11);
      expect(CELLS.every((c) => c.text.length > 0)).toBe(true);
    });

    for (const cell of CELLS) {
      it(`${cell.id}: as a file name, sheet name, model, dropped part and map`, () => {
        const shown = escapeHtml(visibleControls(cell.text));
        const state = play(
          [
            listing([cell.text]),
            { type: "file_selected", file: cell.text },
            { type: "inspect_requested" },
            {
              type: "inspect_result",
              result: replyOf("questionnaire_inspect", 200, inspectBody({
                sheets: [{ name: cell.text, rows: 1, columns: 1 }],
                suggestion: { sheet: cell.text, header_row: 1, question_col: 1, id_col: null },
                warnings: [cell.text],
              })),
            },
          ],
        );
        const finished = poll(
          reduceQuestionnaire(
            reduceQuestionnaire(state, { type: "start_requested" }),
            { type: "start_result", result: replyOf("questionnaire_start", 202, { run_id: RUN, total: 1 }), nowMs: 1 },
          ),
          {
            status: "complete",
            total: 1,
            completed: 1,
            summary: summaryBody({
              model: cell.text,
              file_name: cell.text,
              dropped_parts: [[cell.text, 1, 0]],
              warnings: [cell.text],
              reason_counts: { [cell.text]: 1 },
              counts: { SUPPORTED: 1, [cell.text]: 1 },
            }),
          },
        );
        for (const s of [state, finished]) {
          const html = render(s);
          expectSafeMarkup(html);
          expect(html).toContain(shown);
          // Anything the text could have opened is gone once the escaped copies are removed.
          expect(html.split(shown).join("")).not.toMatch(/<script|<img|<a[\s>]|href=|javascript:|onerror/i);
          if (/[<>&"']/.test(cell.text)) {
            expect(html).not.toContain(cell.text);
          }
        }
        // The status key, the warning and the reason are NOT echoed: an unrecognised word is shown.
        const text = textOf(render(finished));
        expect(text).toContain("unrecognised warning");
        expect(text).toContain("unrecognised reason");
        expect(text).toContain("Unknown status");
      });
    }

    it("direction-control characters in a file name are shown as marks", () => {
      const override = String.fromCodePoint(0x202e); // built at run time: no raw bidi character here
      const html = render(play([listing([`invoice${override}gpj.xlsx`])]));
      expect(html).toContain("invoice[U+202E]gpj.xlsx");
      expect(html).not.toContain(override);
    });
  });

  it("every state renders without an inline style, script, link or remote URL", () => {
    const states = [
      INITIAL_QUESTIONNAIRE_STATE,
      play([listing([])]),
      play([listing(["a.xlsx"])]),
      ready,
      play(start.slice(0, 1), ready),
      running,
      done(),
    ];
    for (const state of states) {
      expectSafeMarkup(render(state));
      expectNoRemoteUrl(render(state));
    }
  });
});
