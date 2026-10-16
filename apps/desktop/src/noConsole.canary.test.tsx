// F36-4 (G29, frontend half): the window never logs. A spy on every console method must record
// zero calls while the IPC clients run through their failure paths and every presentational
// component renders. ESLint `no-console` bans the calls statically (scripts/eslint-rules.test.mjs);
// this is the run-time canary. Hostile values carry a marker that must not appear anywhere.

import { renderToStaticMarkup } from "react-dom/server";
import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";
import { FoldersScreen } from "./components/FoldersScreen";
import { FoldersSummary } from "./components/FoldersSummary";
import { IndexScreen } from "./components/IndexScreen";
import { PlainText } from "./components/PlainText";
import { QuestionnaireScreen } from "./components/QuestionnaireScreen";
import { ReadinessIndicator } from "./components/ReadinessIndicator";
import { Refusal } from "./components/Refusal";
import { Shell } from "./components/Shell";
import { StatusBar } from "./components/StatusBar";
import { StatusChip } from "./components/StatusChip";
import { foldersScreenView } from "./foldersScreen";
import { INITIAL_INDEX_STATE, reduceIndex } from "./indexJob";
import { getFolders, listFiles, pickFolder } from "./ipc/folders";
import { fetchHealth } from "./ipc/health";
import { callRoute, type CallResult, type Invoke } from "./ipc/proxy";
import { DraftRowView } from "./components/DraftRowView";
import { LongText } from "./components/LongText";
import { ReviewQueueScreen } from "./components/ReviewQueueScreen";
import { describeApiError, draftRefusedMessage } from "./messages";
import { INITIAL_REVIEW_QUEUE_STATE, reduceReviewQueue, type ReviewQueueEvent } from "./reviewQueue";
import { startPolling } from "./poller";
import { visibleControls } from "./plainText";
import { INITIAL_QUESTIONNAIRE_STATE, reduceQuestionnaire, type QuestionnaireEvent } from "./questionnaireRun";
import { readinessView } from "./readiness";
import { summarizeFolders } from "./foldersSummary";

const METHODS = ["log", "info", "warn", "error", "debug", "trace", "dir", "table", "group"] as const;
const MARKER = "SECRET-DOCUMENT-TEXT";

describe("no console output", () => {
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

  /** Called at the END of each test, so a call made inside it is seen before the next clear. */
  function expectSilent(): void {
    for (const [index, spy] of spies.entries()) {
      expect(spy, METHODS[index]).not.toHaveBeenCalled();
    }
  }

  it("is silent through every failure path of the clients", async () => {
    const hostile = { get status(): number { throw new Error(MARKER); } };
    const invokes: Invoke[] = [
      () => Promise.reject(MARKER),
      () => Promise.reject({ error: "new_word", detail: MARKER }),
      () => Promise.resolve({ detail: MARKER }),
      () => Promise.resolve(hostile),
      () => Promise.reject(hostile),
      () => {
        throw new Error(MARKER);
      },
    ];
    for (const invoke of invokes) {
      await callRoute<"readiness">(invoke, { route: "readiness" });
      await callRoute<"draft_rows">(invoke, { route: "draft_rows", file: "a.xlsx" });
      await getFolders(invoke);
      await pickFolder(invoke, "vault");
      await listFiles(invoke, "exports");
      await fetchHealth(() => invoke("sidecar_health", {}));
    }
    expect(readinessView({ kind: "invalid_response" })).toEqual({ state: "unavailable", code: "invalid_response" });
    expect(summarizeFolders({ kind: "rejected" })).toEqual({ state: "unavailable", code: "rejected" });
    expectSilent();
  });

  it("is silent while every presentational component renders, including with hostile text", () => {
    const html = [
      renderToStaticMarkup(
        <StatusBar
          health={{ state: "healthy" }}
          readiness={{ state: "degraded", models: [MARKER, "<b>x</b>"], ollama: "bad_response" }}
          folders={{ state: "unavailable", code: "rejected" }}
        />,
      ),
      renderToStaticMarkup(<ReadinessIndicator view={{ state: "unavailable", code: "timeout" }} />),
      renderToStaticMarkup(<FoldersSummary summary={{ state: "unknown" }} />),
      renderToStaticMarkup(<StatusChip value={MARKER} />),
      renderToStaticMarkup(<Shell healthy={false} statusBar={null} />),
    ];
    // The unknown status is shown as an error block, so the marker used as a status never appears.
    expect(html[3]).not.toContain(MARKER);
    expectSilent();
  });

  it("is silent through the 6g screens, reducers, message tables and pollers, with hostile text", async () => {
    const noop = (): void => undefined;
    const hostileFolder = { kind: "vault", in_use: MARKER, saved: MARKER, problem: "overlap", start_problem: null } as const;
    const folders = foldersScreenView({
      kind: "ok",
      value: { ready: true, saved_file: "ok", saved_file_at_start: "ok", folders: [hostileFolder] },
    });
    const fail = (error: string, status = 409, extra: object = {}) =>
      ({ kind: "reply", route: "questionnaire_start", reply: { status, body: { error, ...extra } } }) as unknown as CallResult<"questionnaire_start">;

    // Every refusal path of the three flows, with the marker where file text would be.
    let q = INITIAL_QUESTIONNAIRE_STATE;
    for (const event of [
      { type: "files_result", call: { kind: "ok", value: { kind: "questionnaires", files: [MARKER], truncated: false } } },
      { type: "file_selected", file: MARKER },
      { type: "inspect_requested" },
      {
        type: "inspect_result",
        result: { kind: "reply", route: "questionnaire_inspect", reply: { status: 200, body: {
          sheets: [{ name: MARKER, rows: 1, columns: 1 }],
          suggestion: null, macro_enabled: false, parts: {}, warnings: [MARKER],
          sha256: "0123456789abcdef".repeat(4),
        } } },
      },
      { type: "start_requested" },
      { type: "start_result", result: fail("models_unavailable", 503, { models_missing: [MARKER] }), nowMs: 1 },
      { type: "start_requested" },
      { type: "start_result", result: fail("run_in_progress"), nowMs: 1 },
      { type: "start_requested" },
      { type: "start_result", result: { kind: "proxy_error", error: "timeout" }, nowMs: 1 },
    ] as unknown as QuestionnaireEvent[]) {
      q = reduceQuestionnaire(q, event);
    }
    let i = INITIAL_INDEX_STATE;
    i = reduceIndex(i, { type: "start_requested" });
    i = reduceIndex(i, {
      type: "start_result",
      result: { kind: "reply", route: "vault_index_start", reply: { status: 503, body: { error: "models_unavailable", models_missing: [MARKER] } } } as unknown as CallResult<"vault_index_start">,
      nowMs: 1,
    });

    const html = [
      renderToStaticMarkup(<FoldersScreen view={folders} pick={{ phase: "saved", kind: "vault", display: MARKER }} enabled onChoose={noop} />),
      renderToStaticMarkup(<FoldersScreen view={{ state: "loading" }} pick={{ phase: "idle" }} enabled={false} onChoose={noop} />),
      renderToStaticMarkup(<IndexScreen state={i} nowMs={0} readiness={{ state: "degraded", models: [MARKER], ollama: "bad_response" }} vaultInUse={false} enabled onStart={noop} />),
      renderToStaticMarkup(
        <QuestionnaireScreen
          state={q}
          nowMs={0}
          readiness={{ state: "unavailable", code: "timeout" }}
          ledgerInUse={null}
          enabled
          onSelectFile={noop}
          onInspect={noop}
          onMappingChange={noop}
          onStart={noop}
          onGoToIndex={noop}
          onDismissRun={noop}
          onOpenInQueue={noop}
        />,
      ),
      renderToStaticMarkup(<Refusal failure={describeApiError({ error: "models_unavailable", models_missing: [MARKER] })} />),
      renderToStaticMarkup(<PlainText text={MARKER} />),
    ].join("");
    expect(html).toContain(MARKER); // shown to the user as text, never logged

    // The pollers run, are stopped, and say nothing.
    vi.useFakeTimers();
    try {
      const stop = startPolling(() => Promise.resolve(MARKER), noop, 10);
      await vi.advanceTimersByTimeAsync(50);
      stop();
    } finally {
      vi.useRealTimers();
    }
    expect(describeApiError({ error: "workbook_refused", code: "unreadable" }).code).toBe("workbook_refused:unreadable");
    expect(visibleControls(MARKER)).toBe(MARKER);
    expectSilent();
  });

  it("is silent through the review queue: reducer, components and the client calls, with hostile text", async () => {
    const hostileRow = {
      row: 1,
      question_text: MARKER,
      status: MARKER,
      reasons: [MARKER],
      answer_text: MARKER,
      citation: { source_path: MARKER, start_line: 1, end_line: 2, quote: MARKER.repeat(40) },
      status_edited: true,
      answer_edited: true,
      evidence_edited: true,
    };
    const result = (status: number, body: unknown) =>
      ({ kind: "reply", route: "draft_rows", reply: { status, body } }) as unknown as CallResult<"draft_rows">;
    let state = INITIAL_REVIEW_QUEUE_STATE;
    for (const event of [
      { type: "files_requested" },
      { type: "files_result", call: { kind: "ok", value: { kind: "exports", files: [MARKER], truncated: false } } },
      { type: "file_selected", file: MARKER },
      { type: "draft_requested" },
      {
        type: "draft_result",
        file: MARKER,
        result: result(200, { model: MARKER, file_name: MARKER, complete: false, question_count: 1, rows: [hostileRow] }),
      },
    ] as unknown as ReviewQueueEvent[]) {
      state = reduceReviewQueue(state, event);
    }
    const refusedState = reduceReviewQueue(
      reduceReviewQueue(
        reduceReviewQueue(state, { type: "draft_requested" }),
        { type: "draft_result", file: MARKER, result: result(422, { error: "draft_refused", code: "question_mismatch", counts: { rows_file: 2 } }) },
      ),
      { type: "files_result", call: { kind: "folder_error", error: "not_configured" } },
    );
    const html = [
      renderToStaticMarkup(<ReviewQueueScreen state={state} enabled onOpenFile={() => undefined} />),
      renderToStaticMarkup(<ReviewQueueScreen state={refusedState} enabled={false} onOpenFile={() => undefined} />),
      renderToStaticMarkup(<DraftRowView row={hostileRow} />),
      renderToStaticMarkup(<LongText text={MARKER.repeat(100)} />),
    ].join("");
    expect(html).toContain(MARKER); // shown to the user as text, never logged
    // The client calls the queue's hook makes, through their failure paths.
    const failing: Invoke = () => Promise.reject(MARKER);
    await callRoute<"draft_rows">(failing, { route: "draft_rows", file: MARKER });
    await listFiles(failing, "exports");
    expect(draftRefusedMessage("question_mismatch").length).toBeGreaterThan(10);
    expectSilent();
  });

  it("would notice a console call (the spy is live)", () => {
    // Calls the spy itself (not console), so the file holds no console call. If the spies were
    // not attached to the console methods, `expectSilent` above could never fail.
    const first = spies[0];
    expect(first).toBeDefined();
    first?.("canary");
    expect(first).toHaveBeenCalledTimes(1);
  });
});
