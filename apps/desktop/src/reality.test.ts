// The Reality check screen's pure logic (chunk 65j; ADR-016, ADR-022, threat model F26-7; docs/
// DESIGN.md 11.1). What is sent is driven through `sendInspect` and `sendCheck`, the functions the
// hook calls, with a recording fake `invoke`: what reaches it is exactly what the host would get.
// Every reply is built from the sidecar's RECORDED replies (synthetic data).

import { describe, expect, it } from "vitest";
import { REALITY_RESULT_EXCEPTIONS, REALITY_RESULT_NONE } from "./fixedTexts";
import { CHECK_REFUSAL_CODES, EXPORT_REFUSAL_CODES, routeRequestSchema, type Invoke } from "./ipc/proxy";
import { REALITY_NOT_AVAILABLE } from "./ledgerText";
import { describeRealityError, checkRefusalMessage, exportRefusalMessage, type LedgerErrorBody } from "./messages";
import {
  INITIAL_REALITY_STATE,
  checkCountRows,
  checkRequest,
  countText,
  currentCheckFields,
  fileAt,
  inspectCountRows,
  isChosen,
  readResult,
  realityEventOnScreen,
  realityFailureOf,
  realityProblem,
  realityProblemText,
  reduceReality,
  sendCheck,
  sendInspect,
  type CheckReport,
  type RealityProblem,
  type RealityState,
} from "./reality";
import {
  FILE,
  OTHER_FILE,
  STATUS_AT,
  TYPE_AT,
  candidate,
  checkReport,
  checkedState,
  chosenState,
  claimsListed,
  claimsReport,
  filesListed,
  inspectReport,
  inspected,
  readState,
  readyState,
  recordedCheck,
  recordedClaims,
  recordedInspect,
  run,
} from "./realityTestSupport";
import { errorOf, replyOf } from "./testSupport";

/** A fake invoke that records every call and answers with `answer`. */
function recording(answer: unknown = { status: 200, body: recordedCheck("ok").body }) {
  const calls: { command: string; args: Record<string, unknown> }[] = [];
  const invoke: Invoke = (command, args) => {
    calls.push({ command, args });
    return Promise.resolve(answer);
  };
  return { invoke, calls };
}

const SHA = "2f70196855ceca5b28031de475f25605521b52c41028eee426e93552ba466507";
const OTHER_SHA = "0".repeat(64);

describe("what the window sends", () => {
  it("the export read is exactly the name the Imports listing gave, nothing else", async () => {
    const state = run(filesListed([FILE, OTHER_FILE]));
    const name = fileAt(state.files, 1);
    expect(name).toBe(OTHER_FILE);
    const { invoke, calls } = recording({ status: 200, body: recordedInspect("ok").body });
    await sendInspect(invoke, name ?? "");
    expect(calls).toHaveLength(1);
    expect(calls[0]?.command).toBe("sidecar_call");
    expect(calls[0]?.args.request).toEqual({ route: "reality_inspect", file: OTHER_FILE });
  });

  it("a position with no listed name chooses nothing, and the listing's own order is the one used", () => {
    const state = run(filesListed([FILE]));
    expect(fileAt(state.files, 5)).toBeNull();
    expect(reduceReality(state, { type: "file_chosen", index: 5 })).toBe(state);
    expect(fileAt({ phase: "idle" }, 0)).toBeNull();
  });

  it("the check carries the file, the claim as listed, the chosen values as listed, and the typed date", async () => {
    const state = readyState();
    const { invoke, calls } = recording();
    const sent = sendCheck(invoke, state);
    expect(sent).not.toBeNull();
    await sent;
    expect(calls).toHaveLength(1);
    const claim = candidate();
    expect(calls[0]?.args.request).toEqual({
      route: "reality_check",
      file: FILE,
      claim: { path: claim.path, start_line: claim.start_line, end_line: claim.end_line, quote: claim.quote },
      mfa_types: ["Okta Verify"],
      phone_types: [],
      active_statuses: ["Active"],
      exported_at: "2026-10-01",
    });
  });

  it("the claim carries exactly its four fields: no doc id, no words, no control note", () => {
    const fields = currentCheckFields(readyState());
    expect(Object.keys(fields?.claim ?? {}).sort()).toEqual(["end_line", "path", "quote", "start_line"]);
  });

  it("a claim is sent exactly as the claims reply gave it, to the last space", () => {
    const odd = candidate({ quote: "  MFA   is required.  ", start_line: 7, end_line: 9, path: "odd name.md" });
    const state = run(
      [
        { type: "input_changed", change: { kind: "mfa", index: TYPE_AT.oktaVerify } },
        { type: "input_changed", change: { kind: "status", index: STATUS_AT.active } },
        { type: "input_changed", change: { kind: "claim", index: 0 } },
        { type: "input_changed", change: { kind: "date", text: "2026-10-01" } },
        { type: "input_changed", change: { kind: "confirm", confirmed: true } },
      ],
      run([...filesListed(), ...claimsListed(claimsReport({ candidates: [odd] })), ...inspected()]),
    );
    expect(currentCheckFields(state)?.claim).toEqual({
      path: "odd name.md",
      start_line: 7,
      end_line: 9,
      quote: "  MFA   is required.  ",
    });
  });

  it("an unset value is left out, never null: every value is present, a list may be empty, none is null", () => {
    const request = checkRequest(currentCheckFields(readyState()) ?? failFields());
    const text = JSON.stringify(request);
    expect(text).not.toContain("null");
    expect(text).not.toContain("undefined");
    expect(Object.values(request).every((value) => value !== null && value !== undefined)).toBe(true);
    // The phone list is the empty list when no phone type was named, and the host accepts it.
    expect(request.phone_types).toEqual([]);
    expect(routeRequestSchema.safeParse(request).success).toBe(true);
  });

  it("a phone type that was chosen is sent in phone_types, as listed", () => {
    const state = run([{ type: "input_changed", change: { kind: "phone", index: TYPE_AT.phone } }], chosenState());
    const withConfirm = run([{ type: "input_changed", change: { kind: "confirm", confirmed: true } }], state);
    expect(currentCheckFields(withConfirm)?.phone_types).toEqual(["Phone"]);
  });

  it("the values sent come in the order of the inspect reply, whatever order they were chosen in", () => {
    const state = run(
      [
        { type: "input_changed", change: { kind: "mfa", index: TYPE_AT.phone } },
        { type: "input_changed", change: { kind: "mfa", index: TYPE_AT.oktaVerify } },
        { type: "input_changed", change: { kind: "confirm", confirmed: true } },
      ],
      run([
        { type: "input_changed", change: { kind: "status", index: STATUS_AT.active } },
        { type: "input_changed", change: { kind: "claim", index: 0 } },
        { type: "input_changed", change: { kind: "date", text: "2026-10-01" } },
      ], readState()),
    );
    expect(currentCheckFields(state)?.mfa_types).toEqual(["Okta Verify", "Phone"]);
  });
});

function failFields(): never {
  throw new Error("no check fields");
}

describe("nothing is sent until the MFA types are confirmed", () => {
  it("everything chosen but not confirmed: the problem is not_confirmed and invoke is never called", () => {
    const state = chosenState();
    expect(realityProblem(state)).toBe("not_confirmed");
    const { invoke, calls } = recording();
    expect(sendCheck(invoke, state)).toBeNull();
    expect(calls).toHaveLength(0);
    expect(currentCheckFields(state)).toBeNull();
  });

  it("confirming makes the same state sendable, and one call is made", async () => {
    const { invoke, calls } = recording();
    expect(realityProblem(readyState())).toBeNull();
    await sendCheck(invoke, readyState());
    expect(calls).toHaveLength(1);
  });

  it("changing an MFA type, a phone type or a status clears the confirmation", () => {
    for (const change of [
      { kind: "mfa", index: TYPE_AT.phone },
      { kind: "phone", index: TYPE_AT.phone },
      { kind: "status", index: STATUS_AT.deprovisioned },
    ] as const) {
      const after = run([{ type: "input_changed", change }], readyState());
      expect(after.inputs.confirmed, change.kind).toBe(false);
      expect(realityProblem(after), change.kind).toBe("not_confirmed");
    }
  });

  it("changing the claim or the date does not clear it, and changing the file does", () => {
    const claimChanged = run([{ type: "input_changed", change: { kind: "claim", index: 0 } }], readyState());
    expect(claimChanged.inputs.confirmed).toBe(true);
    const dateChanged = run([{ type: "input_changed", change: { kind: "date", text: "2026-10-02" } }], readyState());
    expect(dateChanged.inputs.confirmed).toBe(true);
    const fileChanged = run([{ type: "file_chosen", index: 1 }], readyState());
    expect(fileChanged.inputs.confirmed).toBe(false);
    expect(fileChanged.inputs.mfaTypes).toEqual([]);
    expect(fileChanged.inputs.statuses).toEqual([]);
  });

  it("each missing step has its own problem, in order, and sends nothing", () => {
    const problems: [RealityProblem, RealityState][] = [
      ["no_file", INITIAL_REALITY_STATE],
      ["not_read", run([...filesListed(), { type: "file_chosen", index: 0 }])],
      ["no_mfa", run([...filesListed(), ...claimsListed(), ...inspected()])],
      [
        "no_status",
        run(
          [{ type: "input_changed", change: { kind: "mfa", index: TYPE_AT.oktaVerify } }],
          run([...filesListed(), ...claimsListed(), ...inspected()]),
        ),
      ],
      [
        "no_claim",
        run(
          [
            { type: "input_changed", change: { kind: "mfa", index: TYPE_AT.oktaVerify } },
            { type: "input_changed", change: { kind: "status", index: STATUS_AT.active } },
          ],
          readState(),
        ),
      ],
      [
        "no_date",
        run(
          [
            { type: "input_changed", change: { kind: "mfa", index: TYPE_AT.oktaVerify } },
            { type: "input_changed", change: { kind: "status", index: STATUS_AT.active } },
            { type: "input_changed", change: { kind: "claim", index: 0 } },
          ],
          readState(),
        ),
      ],
      ["date_invalid", run([{ type: "input_changed", change: { kind: "date", text: "2026-02-30" } }], chosenState())],
      ["not_confirmed", chosenState()],
    ];
    const texts = new Set<string>();
    for (const [problem, state] of problems) {
      expect(realityProblem(state), problem).toBe(problem);
      const { invoke, calls } = recording();
      expect(sendCheck(invoke, state), problem).toBeNull();
      expect(calls, problem).toHaveLength(0);
      texts.add(realityProblemText(problem));
    }
    expect(texts.size).toBe(problems.length);
  });
});

describe("a value cut short cannot be chosen or sent", () => {
  const cut = inspectReport({
    authenticator_types: [
      { value: "Password", count: 35, truncated: false },
      { value: "A very long type name cut short by the sidecar", count: 4, truncated: true },
    ],
    statuses: [
      { value: "Active", count: 32, truncated: false },
      { value: "A very long status cut short by the sidecar", count: 2, truncated: true },
    ],
  });

  it("choosing a truncated type, phone type or status changes nothing (the same state object)", () => {
    const state = readState(cut);
    for (const kind of ["mfa", "phone", "status"] as const) {
      expect(reduceReality(state, { type: "input_changed", change: { kind, index: 1 } }), kind).toBe(state);
      expect(isChosen(state, kind, 1), kind).toBe(false);
    }
  });

  it("an index with no value changes nothing either", () => {
    const state = readState(cut);
    expect(reduceReality(state, { type: "input_changed", change: { kind: "mfa", index: 9 } })).toBe(state);
    expect(reduceReality(state, { type: "input_changed", change: { kind: "claim", index: 9 } })).toBe(state);
  });

  it("a truncated value is never in the request, even if it were somehow in the inputs", () => {
    const state = readyState(cut);
    const forced: RealityState = {
      ...state,
      inputs: {
        ...state.inputs,
        mfaTypes: ["Password", "A very long type name cut short by the sidecar"],
        statuses: ["Active", "A very long status cut short by the sidecar"],
      },
    };
    const fields = currentCheckFields(forced);
    expect(fields?.mfa_types).toEqual(["Password"]);
    expect(fields?.active_statuses).toEqual(["Active"]);
    expect(JSON.stringify(fields)).not.toContain("cut short");
  });

  it("a state whose only chosen values were cut short has no MFA type or no status, and sends nothing", () => {
    const state = readyState(cut);
    const onlyCut: RealityState = {
      ...state,
      inputs: { ...state.inputs, mfaTypes: ["A very long type name cut short by the sidecar"] },
    };
    expect(realityProblem(onlyCut)).toBe("no_mfa");
    expect(currentCheckFields(onlyCut)).toBeNull();
  });
});

describe("the claim", () => {
  it("a claim that is no longer listed after the claims are read again is forgotten", () => {
    const state = readyState();
    const after = run(claimsListed(claimsReport({ candidates: [candidate({ quote: "Another sentence." })] })), state);
    expect(after.inputs.claim).toBeNull();
    expect(realityProblem(after)).toBe("no_claim");
    const same = run(claimsListed(), state);
    expect(same.inputs.claim).not.toBeNull();
  });

  it("a claims failure clears the chosen claim and shows the refusal", () => {
    const state = run(
      [
        { type: "claims_requested" },
        { type: "claims_result", result: errorOf("reality_claims", 503, "import_root_not_configured") },
      ],
      readyState(),
    );
    expect(state.claims.phase).toBe("failed");
    expect(state.inputs.claim).toBeNull();
    expect(state.check.phase).toBe("none");
  });
});

describe("the result sentence comes from the reply's finding", () => {
  it("exceptions_found gives the first DESIGN.md 11.1 sentence, word for word", () => {
    expect(readResult(checkReport(), SHA)).toEqual({
      kind: "sentence",
      text: "This export shows users in scope without MFA, by the MFA types you confirmed.",
    });
    expect(REALITY_RESULT_EXCEPTIONS).toBe("This export shows users in scope without MFA, by the MFA types you confirmed.");
  });

  it("no_exceptions_in_export gives the second sentence, word for word", () => {
    const none = checkReport({
      finding: "no_exceptions_in_export",
      without_mfa: 0,
      admins_without_mfa: 0,
      phone_only: 0,
      exceptions: [],
      exceptions_not_listed: 0,
    });
    expect(readResult(none, SHA)).toEqual({
      kind: "sentence",
      text: "This export shows no users in scope without MFA, by the MFA types you confirmed.",
    });
    expect(REALITY_RESULT_NONE).toBe(
      "This export shows no users in scope without MFA, by the MFA types you confirmed.",
    );
  });

  it("neither sentence says compliant, non-compliant, pass or fail", () => {
    for (const text of [REALITY_RESULT_EXCEPTIONS, REALITY_RESULT_NONE]) {
      expect(text).not.toMatch(/compliant|pass|fail/i);
    }
  });

  it("the finding word decides: a finding of none with users without MFA is no sentence, but an error", () => {
    const lying = checkReport({ finding: "no_exceptions_in_export" });
    expect(lying.without_mfa).toBeGreaterThan(0);
    expect(readResult(lying, SHA)).toEqual({ kind: "disagree" });
  });

  it("a finding of exceptions with nobody without MFA is no sentence, but an error", () => {
    expect(readResult(checkReport({ without_mfa: 0 }), SHA)).toEqual({ kind: "disagree" });
  });

  it("a finding of none with a listed login or an unlisted login count is an error too", () => {
    const base = { finding: "no_exceptions_in_export", without_mfa: 0 } as const;
    expect(readResult(checkReport({ ...base, exceptions_not_listed: 0 }), SHA)).toEqual({ kind: "disagree" });
    expect(readResult(checkReport({ ...base, exceptions: [], exceptions_not_listed: 3 }), SHA)).toEqual({
      kind: "disagree",
    });
  });

  it("counts alone never produce a sentence: zero counts with the finding of exceptions is not the second sentence", () => {
    const reading = readResult(checkReport({ without_mfa: 0, exceptions: [], exceptions_not_listed: 0 }), SHA);
    expect(reading.kind).toBe("disagree");
    expect(JSON.stringify(reading)).not.toContain("no users");
  });

  it("a different hash between inspect and check is no sentence, whichever the finding", () => {
    expect(readResult(checkReport(), OTHER_SHA)).toEqual({ kind: "hash_mismatch" });
    expect(readResult(checkReport({ finding: "no_exceptions_in_export", without_mfa: 0, exceptions: [] }), OTHER_SHA)).toEqual({
      kind: "hash_mismatch",
    });
  });

  it("the stored result keeps the hash the inspect reply gave, so the comparison is against that one", () => {
    const state = checkedState();
    expect(state.check.phase).toBe("checked");
    if (state.check.phase === "checked") {
      expect(state.check.inspectSha256).toBe(SHA);
      expect(readResult(state.check.report, state.check.inspectSha256).kind).toBe("sentence");
    }
    const mismatched = checkedState(checkReport({ sha256: OTHER_SHA }));
    if (mismatched.check.phase === "checked") {
      expect(readResult(mismatched.check.report, mismatched.check.inspectSha256).kind).toBe("hash_mismatch");
    }
  });

  it("a finding word outside the closed set is an error, never a label", () => {
    const unknown = { ...checkReport(), finding: "all_clear" } as unknown as CheckReport;
    expect(readResult(unknown, SHA)).toEqual({ kind: "unknown_finding" });
    const empty = { ...checkReport(), finding: "" } as unknown as CheckReport;
    expect(readResult(empty, SHA)).toEqual({ kind: "unknown_finding" });
  });
});

describe("counts are shown as the reply sent them", () => {
  it("a count that is not there is 'not available', never 0", () => {
    expect(countText(null)).toBe("not available");
    expect(countText(undefined)).toBe("not available");
    expect(countText(null)).toBe(REALITY_NOT_AVAILABLE);
    expect(countText(null)).not.toBe("0");
    expect(countText(0)).toBe("0");
    expect(countText(13)).toBe("13");
  });

  it("no locale grouping: a big count is plain digits", () => {
    expect(countText(1234567)).toBe("1234567");
  });

  it("every count row of the check is the recorded number, the phone-only count under its own label", () => {
    const rows = new Map(checkCountRows(checkReport()));
    expect(rows.get("In scope")).toBe("32");
    expect(rows.get("With MFA")).toBe("19");
    expect(rows.get("Without MFA")).toBe("13");
    expect(rows.get("Administrators without MFA")).toBe("3");
    expect(rows.get("Administrator status unknown")).toBe("1");
    expect(rows.get("With only a phone type")).toBe("3");
    expect(rows.get("Repeated logins")).toBe("1");
    expect(new Set(checkCountRows(checkReport()).map(([label]) => label)).size).toBe(checkCountRows(checkReport()).length);
  });

  it("every count row of the inspect reply is the recorded number", () => {
    const rows = new Map(inspectCountRows(inspectReport()));
    expect(rows.get("Size in bytes")).toBe("7509");
    expect(rows.get("Data rows")).toBe("40");
    expect(rows.get("Repeated logins")).toBe("1");
  });
});

describe("failures, each with its own words", () => {
  it("every export_refused code has its own non-empty message", () => {
    const texts = EXPORT_REFUSAL_CODES.map((code) => exportRefusalMessage(code));
    for (const text of texts) {
      expect(text.length).toBeGreaterThan(10);
    }
    expect(new Set(texts).size).toBe(EXPORT_REFUSAL_CODES.length);
  });

  it("every check_refused code has its own non-empty message", () => {
    const texts = CHECK_REFUSAL_CODES.map((code) => checkRefusalMessage(code));
    for (const text of texts) {
      expect(text.length).toBeGreaterThan(10);
    }
    expect(new Set(texts).size).toBe(CHECK_REFUSAL_CODES.length);
  });

  it("describeRealityError keeps the code beside the words, for each refusal", () => {
    for (const code of EXPORT_REFUSAL_CODES) {
      const body = { error: "export_refused", code } as LedgerErrorBody;
      expect(describeRealityError(body)).toMatchObject({ code: `export_refused:${code}`, text: exportRefusalMessage(code) });
    }
    for (const code of CHECK_REFUSAL_CODES) {
      const body = { error: "check_refused", code } as LedgerErrorBody;
      expect(describeRealityError(body)).toMatchObject({ code: `check_refused:${code}`, text: checkRefusalMessage(code) });
    }
  });

  it("the column an export_refused names is not shown", () => {
    const body = { error: "export_refused", code: "missing_column", column: "user.login" } as LedgerErrorBody;
    expect(JSON.stringify(describeRealityError(body))).not.toContain("user.login");
  });

  it("import_root_not_configured names both folders on a reality route", () => {
    const described = describeRealityError({ error: "import_root_not_configured" } as LedgerErrorBody);
    expect(described.code).toBe("import_root_not_configured");
    expect(described.text).toMatch(/imports folder/);
    expect(described.text).toMatch(/vault folder/);
  });

  it("every recorded refusal of the three routes gives words and a safe code", () => {
    for (const [route, file] of [
      ["reality_inspect", ["not_configured", "file_not_found", "path_not_allowed", "export_refused", "invalid_request"]],
      ["reality_claims", ["not_configured", "control_map_too_large", "auth_controls_invalid"]],
      [
        "reality_check",
        [
          "not_configured",
          "file_not_found",
          "path_not_allowed",
          "export_refused",
          "claim_not_verbatim",
          "exported_at_in_future",
          "invalid_request",
          "control_map_too_large",
          "auth_controls_invalid",
        ],
      ],
    ] as const) {
      for (const name of file) {
        const recorded =
          route === "reality_inspect" ? recordedInspect(name) : route === "reality_claims" ? recordedClaims(name) : recordedCheck(name);
        const failure = realityFailureOf(replyOf(route, recorded.status, recorded.body));
        expect(failure, `${route} ${name}`).not.toBeNull();
        expect(failure?.text.length ?? 0, `${route} ${name}`).toBeGreaterThan(10);
        expect(failure?.code, `${route} ${name}`).toMatch(/^[a-z0-9_:]+$/);
      }
    }
  });

  it("a 200 is no failure, and a call that gave no reply has proxy words", () => {
    expect(realityFailureOf(replyOf("reality_check", 200, recordedCheck("ok").body))).toBeNull();
    expect(realityFailureOf({ kind: "proxy_error", error: "timeout" })?.code).toBe("timeout");
    expect(realityFailureOf({ kind: "proxy_error", error: "too_large" })?.code).toBe("too_large");
    expect(realityFailureOf({ kind: "invalid_response" })?.code).toBe("invalid_response");
    expect(realityFailureOf({ kind: "rejected" })?.code).toBe("rejected");
    expect(realityFailureOf({ kind: "invalid_request" })?.code).toBe("invalid_request");
  });
});

describe("the listing", () => {
  it("no imports folder in use is a typed refusal, never an empty list", () => {
    const state = run([
      { type: "files_requested" },
      { type: "files_result", call: { kind: "folder_error", error: "not_configured" } },
    ]);
    expect(state.files.phase).toBe("failed");
    if (state.files.phase === "failed") {
      expect(state.files.failure.code).toBe("import_root_not_configured");
      expect(state.files.failure.text).toMatch(/imports folder/);
    }
  });

  it("an empty folder is a listed state with no names, apart from the refusal", () => {
    const state = run(filesListed([]));
    expect(state.files).toEqual({ phase: "listed", files: [], truncated: false });
  });

  it("other folder failures have their own words", () => {
    for (const call of [
      { kind: "folder_error", error: "unavailable" },
      { kind: "invalid_response" },
      { kind: "rejected" },
    ] as const) {
      const state = run([{ type: "files_requested" }, { type: "files_result", call }]);
      expect(state.files.phase).toBe("failed");
    }
  });

  it("a chosen file that is gone from a later listing is forgotten with its choices and result", () => {
    const state = run([...filesListed([OTHER_FILE])], run([{ type: "files_requested" }], checkedState()));
    expect(state.inputs.file).toBeNull();
    expect(state.inputs.mfaTypes).toEqual([]);
    expect(state.check.phase).toBe("none");
  });
});

describe("answers that nobody waits for are dropped", () => {
  it("an inspect answer for another file changes nothing", () => {
    const state = run(filesListed(), INITIAL_REALITY_STATE);
    const chosen = reduceReality(state, { type: "file_chosen", index: 0 });
    const late = reduceReality(chosen, {
      type: "inspect_result",
      file: OTHER_FILE,
      result: replyOf("reality_inspect", 200, recordedInspect("ok").body),
    });
    expect(late).toBe(chosen);
  });

  it("an inspect failure is shown and nothing can be chosen", () => {
    const chosen = reduceReality(run(filesListed()), { type: "file_chosen", index: 0 });
    const failed = reduceReality(chosen, {
      type: "inspect_result",
      file: FILE,
      result: errorOf("reality_inspect", 422, "export_refused", { code: "missing_column", column: "user.login" }),
    });
    expect(failed.inspect.phase).toBe("failed");
    expect(realityProblem(failed)).toBe("not_read");
  });

  it("a check answer for older inputs is dropped, and one with no check waiting too", () => {
    const state = readyState();
    const loading = reduceReality(state, { type: "check_requested", generation: state.generation });
    expect(loading.check.phase).toBe("loading");
    const changed = reduceReality(loading, { type: "input_changed", change: { kind: "date", text: "2026-10-02" } });
    const late = reduceReality(changed, {
      type: "check_result",
      generation: state.generation,
      result: replyOf("reality_check", 200, recordedCheck("ok").body),
    });
    expect(late).toBe(changed);
    expect(reduceReality(state, { type: "check_result", generation: state.generation, result: replyOf("reality_check", 200, checkReport()) })).toBe(state);
  });

  it("a second check request while one is going changes nothing", () => {
    const state = readyState();
    const loading = reduceReality(state, { type: "check_requested", generation: state.generation });
    expect(reduceReality(loading, { type: "check_requested", generation: loading.generation })).toBe(loading);
  });

  it("a check request built from an older generation is refused", () => {
    const state = readyState();
    expect(reduceReality(state, { type: "check_requested", generation: state.generation - 1 })).toBe(state);
  });

  it("a check that is not ready is not marked as going", () => {
    const state = chosenState();
    expect(reduceReality(state, { type: "check_requested", generation: state.generation })).toBe(state);
  });

  it("a refusal of the check is shown with its own words and holds no result", () => {
    const state = readyState();
    const loading = reduceReality(state, { type: "check_requested", generation: state.generation });
    for (const code of CHECK_REFUSAL_CODES) {
      const failed = reduceReality(loading, {
        type: "check_result",
        generation: loading.generation,
        result: errorOf("reality_check", 422, "check_refused", { code }),
      });
      expect(failed.check.phase, code).toBe("failed");
      if (failed.check.phase === "failed") {
        expect(failed.check.failure.text).toBe(checkRefusalMessage(code));
      }
    }
  });

  it("a call that gave no reply is shown as failed, not stuck on loading", () => {
    const state = readyState();
    const loading = reduceReality(state, { type: "check_requested", generation: state.generation });
    const failed = reduceReality(loading, {
      type: "check_result",
      generation: loading.generation,
      result: { kind: "rejected" },
    });
    expect(failed.check.phase).toBe("failed");
  });
});

describe("leaving the screen clears the choices, the result and the logins", () => {
  it("realityEventOnScreen resets on any other screen and does nothing on its own", () => {
    expect(realityEventOnScreen("reality")).toBeNull();
    for (const other of ["folders", "index", "questionnaire", "review", "ledger", "recall", "blast", "import", "contradictions"]) {
      expect(realityEventOnScreen(other), other).toEqual({ type: "reset" });
    }
  });

  it("the reset drops the file, the types, the statuses, the claim, the date, the result and every login", () => {
    const shown = checkedState();
    expect(JSON.stringify(shown)).toContain("u06@example.test");
    const event = realityEventOnScreen("ledger");
    expect(event).not.toBeNull();
    const after = event === null ? shown : reduceReality(shown, event);
    expect(after.inputs).toEqual(INITIAL_REALITY_STATE.inputs);
    expect(after.files).toEqual({ phase: "idle" });
    expect(after.inspect).toEqual({ phase: "none" });
    expect(after.claims).toEqual({ phase: "idle" });
    expect(after.check).toEqual({ phase: "none" });
    expect(JSON.stringify(after)).not.toContain("u06@example.test");
    expect(JSON.stringify(after)).not.toContain("Okta Verify");
    expect(JSON.stringify(after)).not.toContain(FILE);
  });

  it("a reset of a clean state is the same object (no render), and a reset of a held state raises the generation", () => {
    expect(reduceReality(INITIAL_REALITY_STATE, { type: "reset" })).toBe(INITIAL_REALITY_STATE);
    const held = readyState();
    expect(reduceReality(held, { type: "reset" }).generation).toBe(held.generation + 1);
  });

  it("a reset in the middle of a check drops the late answer: the logins never come back", () => {
    const state = readyState();
    const loading = reduceReality(state, { type: "check_requested", generation: state.generation });
    const reset = reduceReality(loading, { type: "reset" });
    const late = reduceReality(reset, {
      type: "check_result",
      generation: state.generation,
      result: replyOf("reality_check", 200, recordedCheck("ok").body),
    });
    expect(late).toBe(reset);
    expect(JSON.stringify(late)).not.toContain("u06@example.test");
  });

  it("a reset in the middle of reading the listing or the export drops those late answers too", () => {
    const loadingFiles = reduceReality(INITIAL_REALITY_STATE, { type: "files_requested" });
    const reset = reduceReality(loadingFiles, { type: "reset" });
    expect(reset.files).toEqual({ phase: "idle" });
    expect(reduceReality(reset, { type: "files_result", call: { kind: "ok", value: { kind: "imports", files: [FILE], truncated: false } } })).toBe(reset);
    const chosen = reduceReality(run(filesListed()), { type: "file_chosen", index: 0 });
    const afterReset = reduceReality(chosen, { type: "reset" });
    expect(
      reduceReality(afterReset, {
        type: "inspect_result",
        file: FILE,
        result: replyOf("reality_inspect", 200, recordedInspect("ok").body),
      }),
    ).toBe(afterReset);
  });

  it("any input change drops the shown result", () => {
    const shown = checkedState();
    expect(shown.check.phase).toBe("checked");
    const changed = reduceReality(shown, { type: "input_changed", change: { kind: "date", text: "2026-10-03" } });
    expect(changed.check.phase).toBe("none");
  });
});
