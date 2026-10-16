import { describe, expect, it } from "vitest";
import { summarizeFolders } from "./foldersSummary";
import type { FolderCall, FoldersReply } from "./ipc/folders";

const view = (
  kind: FoldersReply["folders"][number]["kind"],
  inUse: string | null,
  saved: string | null,
  problem: FoldersReply["folders"][number]["problem"] = null,
) => ({ kind, in_use: inUse, saved, problem, start_problem: null });

const ok = (reply: FoldersReply): FolderCall<FoldersReply> => ({ kind: "ok", value: reply });

describe("summarizeFolders", () => {
  it("is unknown before any answer and before the host has decided the folders", () => {
    expect(summarizeFolders(undefined)).toEqual({ state: "unknown" });
    expect(
      summarizeFolders(ok({ ready: false, saved_file: "ok", saved_file_at_start: null, folders: [] })),
    ).toEqual({ state: "unknown" });
  });

  it("says nothing is set in words and counts, never as zero results", () => {
    const summary = summarizeFolders(ok({ ready: true, saved_file: "ok", saved_file_at_start: "ok", folders: [] }));
    expect(summary).toEqual({
      state: "known",
      inUse: 0,
      total: 5,
      pendingNextLaunch: [],
      notSet: ["vault", "questionnaires", "exports", "imports", "ledger"],
      invalid: [],
    });
  });

  it("separates in use, saved for the next launch, not set and invalid", () => {
    const summary = summarizeFolders(
      ok({
        ready: true,
        saved_file: "ok",
        saved_file_at_start: "ok",
        folders: [
          view("vault", "C:\\v", "C:\\v"),
          view("questionnaires", null, "C:\\q"),
          view("exports", null, null),
          view("imports", null, "C:\\i", "overlap"),
          view("ledger", null, null),
        ],
      }),
    );
    expect(summary).toEqual({
      state: "known",
      inUse: 1,
      total: 5,
      pendingNextLaunch: ["questionnaires", "imports"],
      notSet: ["exports", "ledger"],
      invalid: ["imports"],
    });
  });

  it("maps each failure to unavailable with a fixed word", () => {
    expect(summarizeFolders({ kind: "folder_error", error: "unavailable" })).toEqual({ state: "unavailable", code: "unavailable" });
    expect(summarizeFolders({ kind: "invalid_response" })).toEqual({ state: "unavailable", code: "invalid_response" });
    expect(summarizeFolders({ kind: "rejected" })).toEqual({ state: "unavailable", code: "rejected" });
  });
});
