// State for the Blast radius screen (chunk 65h; ADR-018, ADR-022 decisions 6 and 8, threat model
// F32-2 and F32-4; docs/DESIGN.md 11.1 and 11.3). Pure: no React, no invoke.
//
// Blast radius is READ-ONLY: the sidecar writes nothing for it (ADR-018 decision 7), so there is no
// confirm step and no write. Flow: list the cited files (`ledger_cited_paths`) → choose one of them
// (the path sent is exactly the listed one, picked by its position, never typed) → choose exactly
// one of "new text" or "deleting the file" → optional customer → Preview (`ledger_blast_radius`).
//
// The proposed text is measured in UTF-8 BYTES before any call; over the limit nothing is sent.
// It is held in the reducer's state only: never stored, never logged, never put in a URL.
// "New" flags are worked out here (proposed flags minus the flags of the file now), never from the
// reply's words. A refusal is fixed words from messages.ts; not configured is never 0 or empty.

import {
  MAX_PROPOSED_TEXT_BYTES,
  RECALL_DETAILS,
  callRoute,
  utf8ByteLength,
  type CallResult,
  type Invoke,
  type RouteReply,
  type RouteRequest,
} from "./ipc/proxy";
import { integerCounts } from "./ledger";
import { BLAST_PROBLEM_NO_MODE, BLAST_PROBLEM_NO_PATH, BLAST_PROBLEM_TOO_LARGE } from "./ledgerText";
import { describeLedgerError, describeNonReply, type Described, type LedgerErrorBody } from "./messages";
import { describeRecallFlag } from "./recall";

type BlastBody = Extract<RouteReply<"ledger_blast_radius">, { status: 200 }>["body"];
type CitedPathsBody = Extract<RouteReply<"ledger_cited_paths">, { status: 200 }>["body"];
export type BlastReport = BlastBody;
export type BlastItem = BlastBody["items"][number];
export type CitedPath = CitedPathsBody["paths"][number];

// ----- the inputs -----------------------------------------------------------------------------

/** Exactly one of the two: the proposed new text, or deleting the file. `null` until chosen. */
export type BlastMode = "text" | "delete";

export interface BlastInputs {
  /** A path exactly as `ledger_cited_paths` listed it, or `null`. Never typed by the person. */
  path: string | null;
  mode: BlastMode | null;
  /** Held while the person edits; sent only in `text` mode. */
  proposedText: string;
  /** `null` means every customer: the request then has no `customer_id` at all. */
  customerId: string | null;
}

export const INITIAL_INPUTS: BlastInputs = { path: null, mode: null, proposedText: "", customerId: null };

export type BlastChange = { kind: "text"; text: string } | { kind: "delete" };

/** What one preview request is made from: complete and checked. */
export interface BlastFields {
  path: string;
  change: BlastChange;
  customerId: string | null;
}

export type BlastProblem = "no_path" | "no_mode" | "text_too_large";

/** The size of a proposed text in UTF-8 bytes (not characters) and whether it is over the limit. */
export function textSize(text: string): { bytes: number; over: boolean } {
  const bytes = utf8ByteLength(text);
  return { bytes, over: bytes > MAX_PROPOSED_TEXT_BYTES };
}

/** The first problem with the inputs, or `null` when a preview can be asked for. */
export function inputProblem(inputs: BlastInputs): BlastProblem | null {
  if (inputs.path === null) {
    return "no_path";
  }
  if (inputs.mode === null) {
    return "no_mode";
  }
  if (inputs.mode === "text" && textSize(inputs.proposedText).over) {
    return "text_too_large";
  }
  return null;
}

/** The words of an input problem. No wildcard: a new problem breaks compilation here. */
export function blastProblemText(problem: BlastProblem): string {
  switch (problem) {
    case "no_path":
      return BLAST_PROBLEM_NO_PATH;
    case "no_mode":
      return BLAST_PROBLEM_NO_MODE;
    case "text_too_large":
      return BLAST_PROBLEM_TOO_LARGE;
  }
}

/** The fields of a preview, or `null` when an input problem stops it (nothing is sent then). */
export function currentFields(inputs: BlastInputs): BlastFields | null {
  if (inputProblem(inputs) !== null || inputs.path === null || inputs.mode === null) {
    return null;
  }
  const change: BlastChange = inputs.mode === "text" ? { kind: "text", text: inputs.proposedText } : { kind: "delete" };
  return { path: inputs.path, change, customerId: inputs.customerId };
}

/**
 * The preview request. Exactly one of `proposed_text` and `delete_file: true`: the text mode
 * carries the text and `delete_file: false` (the host needs the field every time); the delete mode
 * carries `delete_file: true` and no text at all. `customer_id` is left out when none is chosen,
 * never null.
 */
export function blastRequest(fields: BlastFields): Extract<RouteRequest, { route: "ledger_blast_radius" }> {
  const customer = fields.customerId === null ? {} : { customer_id: fields.customerId };
  return fields.change.kind === "text"
    ? { route: "ledger_blast_radius", path: fields.path, proposed_text: fields.change.text, delete_file: false, ...customer }
    : { route: "ledger_blast_radius", path: fields.path, delete_file: true, ...customer };
}

/**
 * The one place the preview is sent (the hook calls this). With an input problem (no file, no
 * choice, a text over the byte limit) it returns `null` and `invoke` is never called. The request
 * schema in callRoute checks the bytes again, so an over-limit text cannot be sent from here.
 */
export function sendPreview(
  invoke: Invoke,
  inputs: BlastInputs,
): { fields: BlastFields; reply: Promise<CallResult<"ledger_blast_radius">> } | null {
  const fields = currentFields(inputs);
  if (fields === null) {
    return null;
  }
  return { fields, reply: callRoute<"ledger_blast_radius">(invoke, blastRequest(fields)) };
}

// ----- detail and flags -----------------------------------------------------------------------

/** A proposed detail from the closed set `RECALL_DETAILS`, or `null` (shown as "unrecognised"). */
export function detailCode(value: string): string | null {
  return (RECALL_DETAILS as readonly string[]).includes(value) ? value : null;
}

/**
 * The proposed flags that are not among the flags of the file now (DESIGN.md 11.3), in the
 * proposed order, each once. Only known flags: an unknown one shows an error block, never "New".
 */
export function newFlags(proposed: readonly string[], now: readonly string[]): readonly string[] {
  const current = new Set(now);
  const out: string[] = [];
  for (const flag of proposed) {
    if (describeRecallFlag(flag).kind === "known" && !current.has(flag) && !out.includes(flag)) {
      out.push(flag);
    }
  }
  return out;
}

// ----- failures -------------------------------------------------------------------------------

/** Fixed words for a call that did not give its 200 body, or `null` when it did. */
export function blastFailureOf(result: CallResult<"ledger_cited_paths" | "ledger_blast_radius">): Described | null {
  if (result.kind !== "reply") {
    return describeNonReply(result);
  }
  const reply = result.reply as { status: number; body: unknown };
  if (reply.status === 200) {
    return null;
  }
  return describeLedgerError(reply.body as LedgerErrorBody);
}

/** The integer counts of a `recall_refused` or `blast_radius_refused` reply, else none. */
function refusalCounts(
  result: CallResult<"ledger_cited_paths" | "ledger_blast_radius">,
): readonly (readonly [string, number])[] {
  if (result.kind !== "reply" || result.reply.status === 200) {
    return [];
  }
  const body = result.reply.body;
  return body.error === "recall_refused" || body.error === "blast_radius_refused" ? integerCounts(body.counts) : [];
}

// ----- the state ------------------------------------------------------------------------------

export type PathsView =
  | { phase: "idle" }
  | { phase: "loading" }
  /** Includes "no ledger folder is in use": never shown as an empty list. */
  | { phase: "failed"; failure: Described; counts: readonly (readonly [string, number])[] }
  | { phase: "listed"; paths: readonly CitedPath[] };

export type PreviewView =
  | { phase: "none" }
  | { phase: "loading" }
  | { phase: "failed"; failure: Described; counts: readonly (readonly [string, number])[] }
  /** The report, with the path and the kind of change it was asked for (the text is not copied). */
  | { phase: "previewed"; report: BlastReport; path: string; mode: BlastMode };

export interface BlastState {
  paths: PathsView;
  inputs: BlastInputs;
  /** Goes up on every input change, so an answer for older inputs is recognised and dropped. */
  generation: number;
  preview: PreviewView;
}

export const INITIAL_BLAST_STATE: BlastState = {
  paths: { phase: "idle" },
  inputs: INITIAL_INPUTS,
  generation: 0,
  preview: { phase: "none" },
};

export type InputChange =
  /** The position of a listed path, or `null` for none. The path itself is read from the list. */
  | { kind: "path"; index: number | null }
  | { kind: "mode"; mode: BlastMode }
  | { kind: "text"; text: string }
  | { kind: "customer"; customerId: string | null };

export type BlastEvent =
  | { type: "paths_requested" }
  | { type: "paths_result"; result: CallResult<"ledger_cited_paths"> }
  | { type: "input_changed"; change: InputChange }
  /** `generation` is the one the request was built from. */
  | { type: "preview_requested"; generation: number }
  | { type: "preview_result"; generation: number; fields: BlastFields; result: CallResult<"ledger_blast_radius"> }
  /** The person left the screen: the pasted text, the path and the preview are forgotten. */
  | { type: "reset" };

/**
 * What a screen change does to the Blast radius state (DESIGN.md 11.1: the pasted text stays in
 * the window for this preview only): when the screen now shown is another one, the state is reset.
 * On the Blast radius screen itself it does nothing. screens.tsx sends this on every screen change.
 */
export function blastEventOnScreen(shown: string): { type: "reset" } | null {
  return shown === "blast" ? null : { type: "reset" };
}

/** The path at `index` of the listed paths, exactly as listed, or `null`. */
export function pathAt(paths: PathsView, index: number | null): string | null {
  if (index === null || paths.phase !== "listed") {
    return null;
  }
  return paths.paths[index]?.source_path ?? null;
}

/** The position of the chosen path in the list, or `null` (for the select's value). */
export function chosenIndex(state: BlastState): number | null {
  if (state.paths.phase !== "listed" || state.inputs.path === null) {
    return null;
  }
  const at = state.paths.paths.findIndex((entry) => entry.source_path === state.inputs.path);
  return at < 0 ? null : at;
}

function applyChange(state: BlastState, change: InputChange): BlastInputs {
  const inputs = state.inputs;
  switch (change.kind) {
    case "path":
      return { ...inputs, path: pathAt(state.paths, change.index) };
    case "mode":
      return { ...inputs, mode: change.mode };
    case "text":
      return { ...inputs, proposedText: change.text };
    case "customer":
      return { ...inputs, customerId: change.customerId };
  }
}

/** True when nothing of a person's input, the paths view or a preview is held. */
function isClean(state: BlastState): boolean {
  return (
    state.inputs.path === INITIAL_INPUTS.path &&
    state.inputs.mode === INITIAL_INPUTS.mode &&
    state.inputs.proposedText === INITIAL_INPUTS.proposedText &&
    state.inputs.customerId === INITIAL_INPUTS.customerId &&
    state.paths.phase === "idle" &&
    state.preview.phase === "none"
  );
}

export function reduceBlast(state: BlastState, event: BlastEvent): BlastState {
  switch (event.type) {
    case "reset":
      // Already clean: the same object, so no re-render happens. Otherwise the generation goes up,
      // so a late answer for the old inputs is dropped.
      if (isClean(state)) {
        return state;
      }
      return { ...INITIAL_BLAST_STATE, generation: state.generation + 1 };
    case "paths_requested":
      return { ...state, paths: { phase: "loading" } };
    case "paths_result": {
      const failure = blastFailureOf(event.result);
      if (failure !== null) {
        return {
          ...state,
          paths: { phase: "failed", failure, counts: refusalCounts(event.result) },
          inputs: { ...state.inputs, path: null },
          generation: state.generation + 1,
          preview: { phase: "none" },
        };
      }
      if (event.result.kind !== "reply" || event.result.reply.status !== 200) {
        return state;
      }
      const paths = event.result.reply.body.paths;
      // A chosen path stays chosen only while it is still listed.
      const kept = paths.some((entry) => entry.source_path === state.inputs.path) ? state.inputs.path : null;
      const changed = kept !== state.inputs.path;
      return {
        ...state,
        paths: { phase: "listed", paths },
        inputs: { ...state.inputs, path: kept },
        generation: changed ? state.generation + 1 : state.generation,
        preview: changed ? { phase: "none" } : state.preview,
      };
    }
    case "input_changed":
      // Any change drops the shown result: a result is never shown beside other inputs.
      return {
        ...state,
        inputs: applyChange(state, event.change),
        generation: state.generation + 1,
        preview: { phase: "none" },
      };
    case "preview_requested":
      if (event.generation !== state.generation) {
        return state;
      }
      return { ...state, preview: { phase: "loading" } };
    case "preview_result": {
      // A late answer for other inputs, or one nobody waits for, changes nothing.
      if (event.generation !== state.generation || state.preview.phase !== "loading") {
        return state;
      }
      const failure = blastFailureOf(event.result);
      if (failure !== null) {
        return { ...state, preview: { phase: "failed", failure, counts: refusalCounts(event.result) } };
      }
      if (event.result.kind !== "reply" || event.result.reply.status !== 200) {
        return state;
      }
      return {
        ...state,
        preview: {
          phase: "previewed",
          report: event.result.reply.body,
          path: event.fields.path,
          mode: event.fields.change.kind,
        },
      };
    }
  }
}
