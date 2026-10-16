// The contracts for the three folder commands (ADR-020 decisions 5 to 8; CLAUDE.md 7).
//
// What Rust sends (apps/desktop/src-tauri/src/commands.rs and folders/mod.rs):
//   * `folders_get`        RESOLVES with FoldersReply, takes no arguments.
//   * `folder_pick`        takes `{kind}` (an enum, never a path); RESOLVES with PickReply.
//   * `folder_list_files`  takes `{kind}`; RESOLVES with FileList.
//   * Each REJECTS with a FolderError `{"error": <word>}`. A command the capability does not allow
//     rejects with a plain string.
// The window only ever receives display strings and words. It never sends a path back.
//
// There is no committed Rust fixture for these shapes yet (only for health and the proxy), so
// folders.test.ts checks them against fixtures written by hand from folders/mod.rs. A
// Rust-generated fixture is a follow-up in src-tauri, which this chunk does not touch.
//
// Same rules as health.ts: strictObject everywhere, nothing throws, Zod errors are dropped.

import "../zodConfig";
import { z } from "zod";
import type { Invoke } from "./proxy";

/** `FolderKind` in folders/mod.rs, in the order folders are accepted. */
export const FOLDER_KINDS = ["vault", "questionnaires", "exports", "imports", "ledger"] as const;
export const folderKindSchema = z.enum(FOLDER_KINDS);
export type FolderKind = z.infer<typeof folderKindSchema>;

/** `FolderProblem`: why a saved folder is not used. */
export const FOLDER_PROBLEMS = ["unavailable", "not_absolute", "overlap"] as const;
const folderProblemSchema = z.enum(FOLDER_PROBLEMS);
export type FolderProblem = z.infer<typeof folderProblemSchema>;

/** `SavedFileState`. */
export const SAVED_FILE_STATES = ["ok", "invalid"] as const;
const savedFileStateSchema = z.enum(SAVED_FILE_STATES);

/** `FolderView`: one folder, both columns. */
const folderViewSchema = z.strictObject({
  kind: folderKindSchema,
  in_use: z.string().nullable(),
  saved: z.string().nullable(),
  problem: folderProblemSchema.nullable(),
  start_problem: folderProblemSchema.nullable(),
});
export type FolderView = z.infer<typeof folderViewSchema>;

/** `FoldersReply`. */
export const foldersReplySchema = z.strictObject({
  ready: z.boolean(),
  saved_file: savedFileStateSchema,
  saved_file_at_start: savedFileStateSchema.nullable(),
  folders: z.array(folderViewSchema),
});
export type FoldersReply = z.infer<typeof foldersReplySchema>;

/** `PickReply`, tagged by `outcome`. */
export const pickReplySchema = z.discriminatedUnion("outcome", [
  z.strictObject({
    outcome: z.literal("saved"),
    kind: folderKindSchema,
    display: z.string(),
  }),
  z.strictObject({ outcome: z.literal("cancelled") }),
]);
export type PickReply = z.infer<typeof pickReplySchema>;

/** `FileList`. */
export const fileListSchema = z.strictObject({
  kind: folderKindSchema,
  files: z.array(z.string()),
  truncated: z.boolean(),
});
export type FileList = z.infer<typeof fileListSchema>;

/** `FolderError`, serialized `{"error": word}`. */
export const FOLDER_ERRORS = [
  "unavailable",
  "overlap",
  "not_configured",
  "not_listable",
  "save_failed",
] as const;
export const folderErrorSchema = z.strictObject({ error: z.enum(FOLDER_ERRORS) });
export type FolderErrorWord = z.infer<typeof folderErrorSchema>["error"];

/** What a folder call gives back. Never an exception. */
export type FolderCall<T> =
  | { kind: "ok"; value: T }
  | { kind: "folder_error"; error: FolderErrorWord }
  | { kind: "invalid_response" }
  | { kind: "rejected" };

async function run<T>(
  invoke: Invoke,
  command: string,
  args: Record<string, unknown>,
  schema: z.ZodType<T>,
): Promise<FolderCall<T>> {
  try {
    let raw: unknown;
    try {
      raw = await invoke(command, args);
    } catch (rejection: unknown) {
      const error = folderErrorSchema.safeParse(rejection);
      return error.success
        ? { kind: "folder_error", error: error.data.error }
        : { kind: "rejected" };
    }
    const parsed = schema.safeParse(raw);
    return parsed.success ? { kind: "ok", value: parsed.data } : { kind: "invalid_response" };
  } catch {
    // A hostile value (a getter that throws) must not escape as an exception.
    return { kind: "invalid_response" };
  }
}

/** Both columns for every folder. */
export function getFolders(invoke: Invoke): Promise<FolderCall<FoldersReply>> {
  return run(invoke, "folders_get", {}, foldersReplySchema);
}

/** Ask the host to open its folder dialog for `kind`. The window supplies no path. */
export function pickFolder(invoke: Invoke, kind: FolderKind): Promise<FolderCall<PickReply>> {
  const checked = folderKindSchema.safeParse(kind);
  if (!checked.success) {
    return Promise.resolve({ kind: "invalid_response" });
  }
  return run(invoke, "folder_pick", { kind: checked.data }, pickReplySchema);
}

/** The workbook names in the in-use questionnaire or export folder. */
export function listFiles(invoke: Invoke, kind: FolderKind): Promise<FolderCall<FileList>> {
  const checked = folderKindSchema.safeParse(kind);
  if (!checked.success) {
    return Promise.resolve({ kind: "invalid_response" });
  }
  return run(invoke, "folder_list_files", { kind: checked.data }, fileListSchema);
}
