// Chunk 65j (ADR-016, ADR-022, threat model F26-7 and F43): the Reality check screen's sources. This
// reads their own files and proves: each of the three reality routes (and the Imports listing) is
// named in exactly one window module and only through the functions the hook calls, the screen has
// no write route, no style attribute and no link, the result sentence comes from the reply's
// `finding` word, the statements come from window constants and never from the reply, the logins
// sentence sits above the list, leaving the screen resets the state, the four new 11.1 sentences are
// the DESIGN.md words, and the 65j wording sits in its own DRAFT block.
//
// It lives in scripts/ (a Node context) because src/ is a webview context with no Node globals
// (scripts/check-eslint-scope.mjs). Run from apps/desktop by `pnpm test`.

import fs from "node:fs";
import { describe, expect, it } from "vitest";

const FILES = [
  "src/reality.ts",
  "src/hooks/useReality.ts",
  "src/components/RealityScreen.tsx",
];

const sources = new Map(FILES.map((file) => [file, fs.readFileSync(file, "utf8")]));

/** Every window source file under src/ (tests and test helpers excluded). */
function allSources(dir = "src") {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const path = `${dir}/${entry.name}`;
    if (entry.isDirectory()) {
      out.push(...allSources(path));
    } else if (/\.(ts|tsx)$/.test(entry.name) && !/\.test\.|TestSupport\.ts$|testSupport\.ts$/.test(entry.name)) {
      out.push(path);
    }
  }
  return out;
}

/** The source with // and block comments removed, so the checks look at code only. */
function code(text) {
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

function lf(text) {
  return text.replace(/\r\n/g, "\n");
}

/** The window sources whose code contains `pattern`. */
function windowFilesWith(pattern) {
  return allSources().filter((file) => pattern.test(code(fs.readFileSync(file, "utf8"))));
}

/** The 65j DRAFT block of ledgerText.ts, LF-normalised. */
function block65j() {
  const text = lf(fs.readFileSync("src/ledgerText.ts", "utf8"));
  const start = text.indexOf("// ===== 65j DRAFT ");
  const end = text.indexOf("// ===== end of 65j DRAFT ");
  return { text, start, end };
}

describe("the reality check sources (65j)", () => {
  it("finds every file and they are not empty", () => {
    expect(sources.size).toBe(FILES.length);
    for (const [file, text] of sources) {
      expect(text.length, file).toBeGreaterThan(200);
    }
  });

  it("a reality_inspect request is built and sent in exactly one window file, reality.ts", () => {
    expect(windowFilesWith(/route:\s*"reality_inspect"/)).toEqual(["src/reality.ts"]);
    expect(windowFilesWith(/callRoute<"reality_inspect">/)).toEqual(["src/reality.ts"]);
  });

  it("a reality_claims request is built and sent in exactly one window file, reality.ts", () => {
    expect(windowFilesWith(/route:\s*"reality_claims"/)).toEqual(["src/reality.ts"]);
    expect(windowFilesWith(/callRoute<"reality_claims">/)).toEqual(["src/reality.ts"]);
  });

  it("a reality_check request is built and sent in exactly one window file, reality.ts", () => {
    expect(windowFilesWith(/route:\s*"reality_check"/)).toEqual(["src/reality.ts"]);
    expect(windowFilesWith(/callRoute<"reality_check">/)).toEqual(["src/reality.ts"]);
  });

  it("the Imports folder is listed in exactly one window file, reality.ts, and with no path", () => {
    expect(windowFilesWith(/listFiles\(invoke,\s*"imports"\)/)).toEqual(["src/reality.ts"]);
    expect(windowFilesWith(/folder_list_files/).filter((file) => file !== "src/ipc/folders.ts")).toEqual([]);
  });

  it("the hook names no route and reaches them only through the reality.ts functions", () => {
    const hook = code(sources.get("src/hooks/useReality.ts") ?? "");
    expect(hook).not.toMatch(/callRoute|route:|listFiles\(/);
    expect(hook).toMatch(/listImports\(invoke\)/);
    expect(hook).toMatch(/sendClaims\(invoke\)/);
    expect(hook).toMatch(/sendInspect\(invoke, file\)/);
    expect(hook).toMatch(/const sent = sendCheck\(invoke, state\);\s*if \(sent === null\) \{\s*gate\.end\(\);\s*return;/);
    expect(windowFilesWith(/\bsendCheck\(/).sort()).toEqual(["src/hooks/useReality.ts", "src/reality.ts"]);
    const pure = code(sources.get("src/reality.ts") ?? "");
    expect(pure).toMatch(/const fields = currentCheckFields\(state\);\s*if \(fields === null\) \{\s*return null;/);
  });

  it("the check takes the start gate before sendCheck, and ends it on the null path and after the answer", () => {
    const hook = code(lf(sources.get("src/hooks/useReality.ts") ?? ""));
    expect(hook).toMatch(/const \[gate\] = useState\(\(\) => createStartGate\(\)\);/);
    const begin = hook.indexOf("gate.tryBegin()");
    const send = hook.indexOf("sendCheck(invoke");
    expect(begin).toBeGreaterThan(-1);
    expect(send).toBeGreaterThan(begin);
    expect(hook).toMatch(/if \(!gate\.tryBegin\(\)\) \{\s*return;\s*\}/);
    expect(hook).toMatch(/if \(sent === null\) \{\s*gate\.end\(\);\s*return;\s*\}/);
    expect(hook).toMatch(/\)\s*\.finally\(\(\) => gate\.end\(\)\);/);
  });

  it("every call the hook makes turns a rejection into a result, so no control waits for ever", () => {
    const hook = code(lf(sources.get("src/hooks/useReality.ts") ?? ""));
    // Four calls, each with a rejection handler as the second argument of `then`.
    expect((hook.match(/\{ kind: "rejected" \}/g) ?? []).length).toBe(4);
    expect(hook).not.toMatch(/await /);
  });

  it("the check request is built from fields with no null and no unset value", () => {
    const pure = code(sources.get("src/reality.ts") ?? "");
    // Only the code that builds the request (the inputs' own "not chosen yet" values are null).
    const building = pure.slice(pure.indexOf("export function currentCheckFields"), pure.indexOf("export function sendInspect"));
    expect(building.length).toBeGreaterThan(200);
    expect(building).not.toMatch(/\b(file|claim|mfa_types|phone_types|active_statuses|exported_at)\s*:\s*null/);
    expect(building).not.toMatch(/\bnull\s*\?\s*\{\}/);
    expect(pure).toMatch(/return \{ route: "reality_check", \.\.\.fields \};/);
    // The claim carries its four fields and nothing else.
    expect(pure).toMatch(
      /claim: \{ path: claim\.path, start_line: claim\.start_line, end_line: claim\.end_line, quote: claim\.quote \}/,
    );
  });

  it("nothing is sent until the types are confirmed: the last problem is not_confirmed, then null", () => {
    const pure = code(lf(sources.get("src/reality.ts") ?? ""));
    expect(pure).toMatch(/if \(!inputs\.confirmed\) \{\s*return "not_confirmed";\s*\}\s*return null;/);
    expect(pure).toMatch(/case "mfa":\s*return \{ \.\.\.inputs, mfaTypes: toggled\(inputs\.mfaTypes, value\), confirmed: false \};/);
  });

  it("a value cut short cannot be chosen: choosable means not truncated, and the request reads only those", () => {
    const pure = code(sources.get("src/reality.ts") ?? "");
    expect(pure).toMatch(/export function isChoosable\(entry: ValueCount\): boolean \{\s*return !entry\.truncated;/);
    expect(pure).toMatch(/if \(isChoosable\(entry\) && picked\.includes\(entry\.value\)/);
    expect(pure).toMatch(/if \(entry === undefined \|\| !isChoosable\(entry\)\) \{\s*return null;/);
  });

  it("the result sentence comes from the finding word, through a switch, and the counts only check it", () => {
    const pure = code(lf(sources.get("src/reality.ts") ?? ""));
    expect(pure).toMatch(/case "exceptions_found":[\s\S]*?REALITY_RESULT_EXCEPTIONS/);
    expect(pure).toMatch(/case "no_exceptions_in_export":[\s\S]*?REALITY_RESULT_NONE/);
    expect(pure).toMatch(/default:\s*return \{ kind: "unknown_finding" \};/);
    // The hash and the counts decide only whether there is a sentence at all.
    expect(pure).toMatch(/report\.sha256 !== inspectSha256/);
    expect(pure).toMatch(/report\.without_mfa > 0/);
    // No second verdict: the window never derives the finding from a count.
    expect(pure).not.toMatch(/finding\s*[:=]\s*report\.(in_scope|without_mfa|with_mfa)/);
  });

  it("no source names compliant, non-compliant, pass or fail as a word of the screen", () => {
    for (const [file, text] of sources) {
      // "failed" is a phase name in the code (never shown), so only the shown texts are scanned for pass and fail.
      expect(code(text), file).not.toMatch(/\bnon-?compliant\b|\bcompliant\b/i);
    }
    const { text, start, end } = block65j();
    expect(code(text.slice(start, end))).not.toMatch(/compliant|\bpass(ed)?\b|\bfail(ed|ure)?\b/i);
  });

  it("the screen shows the statements from window constants and never reads them from the reply", () => {
    const screen = code(sources.get("src/components/RealityScreen.tsx") ?? "");
    expect(screen).toMatch(/<Statement>\{REALITY_STATEMENT\}<\/Statement>/);
    expect(screen).toMatch(/<Statement>\{REALITY_STALENESS_NOTE\}<\/Statement>/);
    expect(screen).toMatch(/<Statement>\{REALITY_DUPLICATE_NOTE\}<\/Statement>/);
    expect(screen).not.toMatch(/\.statement\b|staleness_note|duplicate_note|control_note|\.finding\b/);
    for (const file of ["src/reality.ts", "src/hooks/useReality.ts"]) {
      expect(code(sources.get(file) ?? ""), file).not.toMatch(/staleness_note|duplicate_note|control_note|\.statement\b/);
    }
  });

  it("the logins sentence is rendered before the login list, and the list collapses behind Show more", () => {
    const screen = code(lf(sources.get("src/components/RealityScreen.tsx") ?? ""));
    const sentence = screen.indexOf("<Statement>{REALITY_LOGINS_PERSONAL}</Statement>");
    const list = screen.indexOf("<LoginList logins={report.exceptions} />");
    expect(sentence).toBeGreaterThan(-1);
    expect(list).toBeGreaterThan(sentence);
    expect(screen).toMatch(/aria-expanded=\{open\}/);
    expect(screen).toMatch(/\{open \? "Show less" : "Show more"\}/);
  });

  it("the phone sentence is rendered with the phone choice, as a note of that group", () => {
    const screen = code(lf(sources.get("src/components/RealityScreen.tsx") ?? ""));
    expect(screen).toMatch(/kind="phone"[\s\S]*?note=\{REALITY_PHONE_POLICY\}/);
    expect(screen).toMatch(/\{note === undefined \? null : <Statement>\{note\}<\/Statement>\}/);
  });

  it("the sources name no write route, no confirm step, no style attribute and no link", () => {
    for (const [file, text] of sources) {
      const source = code(text);
      expect(source, file).not.toMatch(/ledger_(void|acknowledge|import)\b|questionnaire_start|vault_index_start|contradictions_start/);
      expect(source, file).not.toMatch(/voidPromise|acknowledgePromise|importPromises|ledgerActions|useConfirm|ConfirmStep/);
      expect(source, file).not.toMatch(/\bstyle=|<a[\s>]|href=|dangerouslySetInnerHTML|innerHTML/);
      expect(source, file).not.toMatch(/https?:\/\/|window\.open|location\.|mailto:|target="_blank"/);
    }
    const routes = new Set();
    for (const [, text] of sources) {
      for (const match of code(text).matchAll(/route:\s*"([a-z_]+)"|callRoute<"([a-z_]+)">/g)) {
        routes.add(match[1] ?? match[2]);
      }
    }
    expect([...routes].sort()).toEqual(["reality_check", "reality_claims", "reality_inspect"]);
  });

  it("no storage, no timer, no console and no locale formatting in the reality files", () => {
    for (const [file, text] of sources) {
      const source = code(text);
      expect(source, file).not.toMatch(/localStorage|sessionStorage|indexedDB|document\.cookie|caches\./);
      expect(source, file).not.toMatch(/setInterval|setTimeout|console\./);
      expect(source, file).not.toMatch(/Intl\.|toLocale/);
      expect(source, file).not.toMatch(/\bonPaste\b|\bautoFocus\b|\bwindow\.confirm|\bconfirm\(/);
    }
  });

  it("no reality file writes a file, a download or a copy of what the export held", () => {
    for (const [file, text] of sources) {
      expect(code(text), file).not.toMatch(/Blob|createObjectURL|download|navigator\.clipboard|writeText|FileReader/);
    }
  });

  it("screens.tsx resets the reality state when the screen shown is not reality, and reads only on entering it", () => {
    const screens = code(lf(fs.readFileSync("src/screens.tsx", "utf8")));
    expect(screens).toMatch(/const \{ reset: resetReality \} = reality;/);
    expect(screens).toMatch(/if \(realityEventOnScreen\(screen\) !== null\) \{\s*resetReality\(\);\s*\}/);
    expect(screens).toMatch(/\} else if \(healthy && screen === "reality"\) \{\s*refreshReality\(\);\s*\}/);
    expect(screens).toMatch(/case "reality":\s*return \(\s*<RealityScreen/);
    expect(screens).toMatch(/onCheck=\{reality\.check\}/);
    const pure = code(lf(sources.get("src/reality.ts") ?? ""));
    expect(pure).toMatch(/return shown === "reality" \? null : \{ type: "reset" \};/);
    const hook = code(lf(sources.get("src/hooks/useReality.ts") ?? ""));
    expect(hook).toMatch(/dispatch\(\{ type: "reset" \}\);/);
  });

  it("the pure reset forgets everything: it returns the initial state with a higher generation", () => {
    const pure = code(lf(sources.get("src/reality.ts") ?? ""));
    expect(pure).toMatch(/return \{ \.\.\.INITIAL_REALITY_STATE, generation: state\.generation \+ 1 \};/);
  });

  it("the messages for the reality refusals are exhaustive switches with no catch-all text", () => {
    const messages = code(lf(fs.readFileSync("src/messages.ts", "utf8")));
    for (const name of ["exportRefusalMessage", "checkRefusalMessage"]) {
      const at = messages.indexOf(`export function ${name}(`);
      expect(at, name).toBeGreaterThan(-1);
      const body = messages.slice(at, messages.indexOf("\n}\n", at));
      expect(body, name).toMatch(/default:\s*return assertNever\(code\);/);
      expect(body, name).not.toMatch(/OTHER|GENERIC|DEFAULT/);
    }
  });

  it("no new source file holds a literal bidirectional or control character", () => {
    const files = [
      ...FILES,
      "src/realityTestSupport.ts",
      "src/reality.test.ts",
      "src/components/reality.test.tsx",
      "scripts/realitySources.test.mjs",
    ];
    for (const file of files) {
      const text = fs.readFileSync(file, "utf8");
      for (const char of text) {
        const point = char.codePointAt(0) ?? 0;
        const bad =
          (point <= 0x1f && point !== 0x09 && point !== 0x0a && point !== 0x0d) ||
          (point >= 0x7f && point <= 0x9f) ||
          (point >= 0x200b && point <= 0x200f) ||
          (point >= 0x2028 && point <= 0x202e) ||
          (point >= 0x2060 && point <= 0x2069) ||
          point === 0xfeff;
        expect(bad, `${file}: U+${point.toString(16)}`).toBe(false);
      }
    }
  });

  it("the four 11.1 sentences are the DESIGN.md words (read from the file, LF-normalised)", () => {
    const design = lf(fs.readFileSync("../../docs/DESIGN.md", "utf8"));
    const fixed = lf(fs.readFileSync("src/fixedTexts.ts", "utf8"));
    for (const sentence of [
      "This export shows users in scope without MFA, by the MFA types you confirmed.",
      "This export shows no users in scope without MFA, by the MFA types you confirmed.",
      "Counting phone (text message or voice) as MFA is a policy choice: phone codes can be phished or intercepted.",
      "The logins below are personal data. ALIBI does not save or log them.",
    ]) {
      expect(design, sentence).toContain(sentence);
      expect(fixed, sentence).toContain(`"${sentence}"`);
    }
  });

  it("the DESIGN.md logins row still says the reset claim is NOT made: no screen text says the data is gone", () => {
    const design = lf(fs.readFileSync("../../docs/DESIGN.md", "utf8"));
    expect(design).toContain('(Not "gone when you leave this screen"');
    const { text, start, end } = block65j();
    const shown = code(text.slice(start, end));
    expect(shown).not.toMatch(/\bgone\b|\bleave\b|\bleaving\b|\bforgotten\b|\bcleared\b|\bdeleted\b|\berased\b|\bdropped\b/i);
    const fixed = lf(fs.readFileSync("src/fixedTexts.ts", "utf8"));
    expect(fixed.slice(fixed.indexOf("REALITY_RESULT_EXCEPTIONS"))).not.toMatch(/gone when you leave/i);
  });

  it("ledgerText.ts keeps every 65j constant in its own DRAFT block after the 65i one", () => {
    const { text, start, end } = block65j();
    expect(start).toBeGreaterThan(text.indexOf("// ===== end of 65i DRAFT "));
    expect(end).toBeGreaterThan(start);
    expect(text.indexOf("// ===== 65j DRAFT ", start + 1)).toBe(-1);
    expect(text.slice(start, end)).toContain("They are DRAFTS until the owner approves them.");
    expect(text.slice(end)).not.toMatch(/export const/);
    let inside = 0;
    for (const match of text.matchAll(/^export const ([A-Z0-9_]+)/gm)) {
      const at = match.index ?? 0;
      const in65j = at > start && at < end;
      expect(in65j, match[1]).toBe(/^(REALITY_|NAV_LABEL_REALITY$)/.test(match[1]));
      inside += in65j ? 1 : 0;
      if (in65j) {
        // No catch-all label: every text names one state, one problem or one code.
        expect(match[1], match[1]).not.toMatch(/_OTHER_|_GENERIC_|_DEFAULT_|_ELSE_/);
      }
    }
    expect(inside).toBeGreaterThan(70);
  });
});
