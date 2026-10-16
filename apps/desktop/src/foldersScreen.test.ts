// The Folders screen's state: rows in the recommended order, the two columns kept apart (F35-6),
// and the pick state machine. No React.

import { describe, expect, it } from "vitest";
import {
  FOLDER_ROW_ORDER,
  folderInUse,
  foldersScreenView,
  ledgerInUse,
  reducePick,
  type PickState,
} from "./foldersScreen";
import { FOLDER_KINDS, type FolderCall, type FolderView, type FoldersReply } from "./ipc/folders";

function view(kind: FolderView["kind"], over: Partial<FolderView> = {}): FolderView {
  return { kind, in_use: null, saved: null, problem: null, start_problem: null, ...over };
}

function reply(folders: FolderView[], over: Partial<FoldersReply> = {}): FolderCall<FoldersReply> {
  return {
    kind: "ok",
    value: { ready: true, saved_file: "ok", saved_file_at_start: "ok", folders, ...over },
  };
}

describe("foldersScreenView", () => {
  it("is loading before the first answer", () => {
    expect(foldersScreenView(undefined)).toEqual({ state: "loading" });
  });

  it("is not_ready until the host has decided the folders (never 'not set')", () => {
    expect(foldersScreenView(reply([], { ready: false }))).toEqual({ state: "not_ready" });
  });

  it("turns a failed call into fixed words", () => {
    const failed = foldersScreenView({ kind: "folder_error", error: "unavailable" });
    expect(failed.state).toBe("unavailable");
    expect(foldersScreenView({ kind: "rejected" }).state).toBe("unavailable");
    expect(foldersScreenView({ kind: "invalid_response" }).state).toBe("unavailable");
  });

  it("lists the five rows in the order vault, ledger, questionnaires, exports, imports", () => {
    const shuffled = [...FOLDER_KINDS].reverse().map((k) => view(k));
    const v = foldersScreenView(reply(shuffled));
    expect(v.state).toBe("loaded");
    if (v.state === "loaded") {
      expect(v.rows.map((r) => r.kind)).toEqual(["vault", "ledger", "questionnaires", "exports", "imports"]);
      expect(v.rows.filter((r) => r.neededForQueue).map((r) => r.kind)).toEqual(["ledger"]);
    }
    expect([...FOLDER_ROW_ORDER].sort()).toEqual([...FOLDER_KINDS].sort());
  });

  it("fills in a kind the host did not list as not set", () => {
    const v = foldersScreenView(reply([view("vault", { in_use: "/v", saved: "/v" })]));
    if (v.state !== "loaded") throw new Error("expected loaded");
    const ledger = v.rows.find((r) => r.kind === "ledger");
    expect(ledger).toMatchObject({ inUse: null, saved: null, differs: false });
  });

  it("keeps in-use and saved apart: differs is true only when they differ", () => {
    const v = foldersScreenView(
      reply([
        view("vault", { in_use: "/a", saved: "/a" }),
        view("ledger", { in_use: null, saved: "/l" }),
        view("exports", { in_use: "/e", saved: "/e2" }),
      ]),
    );
    if (v.state !== "loaded") throw new Error("expected loaded");
    const by = (k: string) => v.rows.find((r) => r.kind === k);
    expect(by("vault")?.differs).toBe(false);
    expect(by("ledger")).toMatchObject({ differs: true, inUse: null, saved: "/l" });
    expect(by("exports")?.differs).toBe(true);
  });

  it("carries the typed problems and the saved-file flags", () => {
    const v = foldersScreenView(
      reply([view("imports", { saved: "/x", problem: "overlap", start_problem: "unavailable" })], {
        saved_file: "invalid",
        saved_file_at_start: "invalid",
      }),
    );
    if (v.state !== "loaded") throw new Error("expected loaded");
    expect(v.savedFileInvalid).toBe(true);
    expect(v.ignoredAtStart).toBe(true);
    expect(v.rows.find((r) => r.kind === "imports")).toMatchObject({
      problem: "overlap",
      startProblem: "unavailable",
    });
  });
});

describe("what is in use", () => {
  it("ledgerInUse and folderInUse are null until the host has said, never false", () => {
    expect(ledgerInUse(undefined)).toBeNull();
    expect(ledgerInUse(reply([], { ready: false }))).toBeNull();
    expect(ledgerInUse({ kind: "rejected" })).toBeNull();
    expect(folderInUse(undefined, "vault")).toBeNull();
  });

  it("a ledger that is only saved is not in use", () => {
    expect(ledgerInUse(reply([view("ledger", { saved: "/l" })]))).toBe(false);
    expect(ledgerInUse(reply([view("ledger", { in_use: "/l", saved: "/l" })]))).toBe(true);
    expect(ledgerInUse(reply([]))).toBe(false);
    expect(folderInUse(reply([view("vault", { in_use: "/v" })]), "vault")).toBe(true);
  });
});

describe("reducePick", () => {
  const idle: PickState = { phase: "idle" };

  it("goes idle → picking → saved with the display string", () => {
    const picking = reducePick(idle, { type: "start", kind: "vault" });
    expect(picking).toEqual({ phase: "picking", kind: "vault" });
    const saved = reducePick(picking, {
      type: "result",
      call: { kind: "ok", value: { outcome: "saved", kind: "vault", display: "/home/v" } },
    });
    expect(saved).toEqual({ phase: "saved", kind: "vault", display: "/home/v" });
  });

  it("a cancelled dialog changes nothing", () => {
    const state = reducePick(
      { phase: "picking", kind: "ledger" },
      { type: "result", call: { kind: "ok", value: { outcome: "cancelled" } } },
    );
    expect(state).toEqual({ phase: "cancelled" });
  });

  it("each host error word becomes a failure with its own code", () => {
    for (const error of ["unavailable", "overlap", "not_configured", "not_listable", "save_failed"] as const) {
      const state = reducePick(
        { phase: "picking", kind: "vault" },
        { type: "result", call: { kind: "folder_error", error } },
      );
      expect(state.phase).toBe("failed");
      if (state.phase === "failed") expect(state.failure.code).toBe(error);
    }
    const rejected = reducePick({ phase: "picking", kind: "vault" }, { type: "result", call: { kind: "rejected" } });
    expect(rejected.phase).toBe("failed");
  });

  it("opens one dialog at a time and ignores a result nobody asked for", () => {
    const picking: PickState = { phase: "picking", kind: "vault" };
    expect(reducePick(picking, { type: "start", kind: "ledger" })).toBe(picking);
    expect(
      reducePick(idle, { type: "result", call: { kind: "ok", value: { outcome: "cancelled" } } }),
    ).toBe(idle);
  });
});
