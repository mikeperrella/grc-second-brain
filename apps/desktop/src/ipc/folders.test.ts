import { describe, expect, it } from "vitest";
import {
  FOLDER_ERRORS,
  FOLDER_KINDS,
  FOLDER_PROBLEMS,
  fileListSchema,
  foldersReplySchema,
  getFolders,
  listFiles,
  pickFolder,
  pickReplySchema,
} from "./folders";
import type { Invoke } from "./proxy";

// Fixtures written BY HAND from apps/desktop/src-tauri/src/folders/mod.rs (FoldersReply,
// FolderView, PickReply, FileList, FolderError, FolderKind, FolderProblem, SavedFileState). There
// is no Rust-generated fixture for these yet, so this checks the schema against the Rust types as
// read, not against Rust's own output.

const FOLDERS_REPLY = {
  ready: true,
  saved_file: "ok",
  saved_file_at_start: "ok",
  folders: [
    { kind: "vault", in_use: "C:\\vault", saved: "C:\\vault", problem: null, start_problem: null },
    { kind: "questionnaires", in_use: null, saved: "C:\\q", problem: null, start_problem: null },
    { kind: "exports", in_use: null, saved: null, problem: null, start_problem: null },
    { kind: "imports", in_use: null, saved: "C:\\i", problem: "overlap", start_problem: "unavailable" },
    { kind: "ledger", in_use: null, saved: "C:\\l", problem: "not_absolute", start_problem: null },
  ],
};

const ok = (value: unknown): Invoke => () => Promise.resolve(value);
const reject = (value: unknown): Invoke => () => Promise.reject(value);

describe("the enums", () => {
  it("name exactly the Rust variants", () => {
    expect(FOLDER_KINDS).toEqual(["vault", "questionnaires", "exports", "imports", "ledger"]);
    expect(FOLDER_PROBLEMS).toEqual(["unavailable", "not_absolute", "overlap"]);
    expect(FOLDER_ERRORS).toEqual(["unavailable", "overlap", "not_configured", "not_listable", "save_failed"]);
  });
});

describe("the reply schemas", () => {
  it("parses folders_get, before and after the host has decided the folders", () => {
    expect(foldersReplySchema.safeParse(FOLDERS_REPLY).success).toBe(true);
    expect(
      foldersReplySchema.safeParse({ ready: false, saved_file: "invalid", saved_file_at_start: null, folders: [] }).success,
    ).toBe(true);
  });

  it("parses both folder_pick outcomes", () => {
    expect(pickReplySchema.safeParse({ outcome: "saved", kind: "vault", display: "C:\\v" }).success).toBe(true);
    expect(pickReplySchema.safeParse({ outcome: "cancelled" }).success).toBe(true);
  });

  it("parses folder_list_files", () => {
    expect(fileListSchema.safeParse({ kind: "exports", files: ["a.xlsx"], truncated: false }).success).toBe(true);
  });

  it("refuses unknown kinds, unknown keys and a picked path sent back as an argument shape", () => {
    expect(foldersReplySchema.safeParse({ ...FOLDERS_REPLY, extra: 1 }).success).toBe(false);
    expect(
      pickReplySchema.safeParse({ outcome: "saved", kind: "backups", display: "x" }).success,
    ).toBe(false);
    expect(pickReplySchema.safeParse({ outcome: "saved", kind: "vault", display: "x", token: "t" }).success).toBe(false);
    expect(pickReplySchema.safeParse({ outcome: "picked" }).success).toBe(false);
    expect(fileListSchema.safeParse({ kind: "vault", files: [1], truncated: false }).success).toBe(false);
  });
});

describe("the clients", () => {
  it("send the command name and only an enum kind, never a path", async () => {
    const calls: Array<{ command: string; args: Record<string, unknown> }> = [];
    const invoke: Invoke = (command, args) => {
      calls.push({ command, args });
      return Promise.resolve({ outcome: "cancelled" });
    };
    await pickFolder(invoke, "ledger");
    await getFolders(invoke);
    await listFiles(invoke, "exports");
    expect(calls).toEqual([
      { command: "folder_pick", args: { kind: "ledger" } },
      { command: "folders_get", args: {} },
      { command: "folder_list_files", args: { kind: "exports" } },
    ]);
  });

  it("return the parsed value on success", async () => {
    expect(await getFolders(ok(FOLDERS_REPLY))).toEqual({ kind: "ok", value: FOLDERS_REPLY });
    expect(await pickFolder(ok({ outcome: "cancelled" }), "vault")).toEqual({
      kind: "ok",
      value: { outcome: "cancelled" },
    });
  });

  it("map each FolderError word to folder_error", async () => {
    for (const word of FOLDER_ERRORS) {
      expect(await pickFolder(reject({ error: word }), "vault")).toEqual({ kind: "folder_error", error: word });
    }
  });

  it("map anything else to rejected or invalid_response, without echoing it", async () => {
    expect(await getFolders(reject("command folders_get not allowed"))).toEqual({ kind: "rejected" });
    expect(await getFolders(reject({ error: "new_word" }))).toEqual({ kind: "rejected" });
    const bad = await getFolders(ok({ ready: true, secret: "SECRET-TEXT" }));
    expect(bad).toEqual({ kind: "invalid_response" });
    expect(JSON.stringify(bad)).not.toContain("SECRET-TEXT");
  });

  it("refuse a kind outside the enum before calling the host", async () => {
    let called = false;
    const invoke: Invoke = () => {
      called = true;
      return Promise.resolve({});
    };
    // @ts-expect-error a kind outside the enum is a type error as well as a run-time refusal
    expect(await pickFolder(invoke, "C:\\Windows")).toEqual({ kind: "invalid_response" });
    // @ts-expect-error likewise
    expect(await listFiles(invoke, "../..")).toEqual({ kind: "invalid_response" });
    expect(called).toBe(false);
  });

  it("never throw, even when the host call throws synchronously or the value has a throwing getter", async () => {
    const throwsSync: Invoke = () => {
      throw new Error("sync");
    };
    expect(await getFolders(throwsSync)).toEqual({ kind: "rejected" });
    const hostile = {
      get ready(): boolean {
        throw new Error("boom");
      },
    };
    expect(await getFolders(ok(hostile))).toEqual({ kind: "invalid_response" });
  });
});
