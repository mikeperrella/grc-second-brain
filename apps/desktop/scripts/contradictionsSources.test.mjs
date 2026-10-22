// Chunk 65i (ADR-022 decisions 1 and 8, threat model F33-9 to F33-11, F40): the Contradictions
// screen's sources. This reads their own files and proves: each of its two routes is named in
// exactly one window module and only through the functions the hook calls, the screen has no write
// route, no confirm step, no style attribute and no link, the model's confidence is never
// rendered, the three new fixed lines are the DESIGN.md 11.1 sentences, and the 65i wording sits
// in its own DRAFT block.
//
// It lives in scripts/ (a Node context) because src/ is a webview context with no Node globals
// (scripts/check-eslint-scope.mjs). Run from apps/desktop by `pnpm test`.

import fs from "node:fs";
import { describe, expect, it } from "vitest";

const FILES = [
  "src/contradictions.ts",
  "src/hooks/useContradictions.ts",
  "src/components/ContradictionsScreen.tsx",
  "src/components/FindingStatusChip.tsx",
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

/** The window sources whose code contains `pattern`. */
function windowFilesWith(pattern) {
  return allSources().filter((file) => pattern.test(code(fs.readFileSync(file, "utf8"))));
}

describe("the contradictions sources (65i)", () => {
  it("finds every file and they are not empty", () => {
    expect(sources.size).toBe(FILES.length);
    for (const [file, text] of sources) {
      expect(text.length, file).toBeGreaterThan(200);
    }
  });

  it("a contradictions_start request is built and sent in exactly one window file, contradictions.ts", () => {
    expect(windowFilesWith(/route:\s*"contradictions_start"/)).toEqual(["src/contradictions.ts"]);
    expect(windowFilesWith(/callRoute<"contradictions_start">/)).toEqual(["src/contradictions.ts"]);
  });

  it("a contradictions_status request is built and sent in exactly one window file, contradictions.ts", () => {
    expect(windowFilesWith(/route:\s*"contradictions_status"/)).toEqual(["src/contradictions.ts"]);
    expect(windowFilesWith(/callRoute<"contradictions_status">/)).toEqual(["src/contradictions.ts"]);
  });

  it("the hook reaches the routes only through sendStart and pollStatus, and sends nothing on an input problem", () => {
    const hook = code(sources.get("src/hooks/useContradictions.ts") ?? "");
    expect(hook).not.toMatch(/callRoute|route:/);
    expect(hook).toMatch(/const sent = sendStart\(invoke, state\.inputs\);\s*if \(sent === null\) \{\s*gate\.end\(\);\s*return;/);
    expect(hook).toMatch(/\(\) => pollStatus\(invoke, target\)/);
    expect(hook).toMatch(/pollTargetWhen\(healthy, contradictionPollTarget\(state\)\)/);
    expect(windowFilesWith(/\bsendStart\(/).sort()).toEqual(["src/contradictions.ts", "src/hooks/useContradictions.ts"]);
    const pure = code(sources.get("src/contradictions.ts") ?? "");
    expect(pure).toMatch(/if \(contradictionProblem\(inputs\) !== null\) \{\s*return null;/);
  });

  it("the hook takes the start gate before sendStart and ends it on the null path and after the reply (65i fix)", () => {
    const hook = code((sources.get("src/hooks/useContradictions.ts") ?? "").replace(/\r\n/g, "\n"));
    expect(hook).toMatch(/const \[gate\] = useState\(\(\) => createStartGate\(\)\);/);
    const begin = hook.indexOf("gate.tryBegin()");
    const send = hook.indexOf("sendStart(invoke");
    expect(begin).toBeGreaterThan(-1);
    expect(send).toBeGreaterThan(begin);
    expect(hook).toMatch(/if \(!gate\.tryBegin\(\)\) \{\s*return;\s*\}/);
    // The null path ends the gate before returning.
    expect(hook).toMatch(/if \(sent === null\) \{\s*gate\.end\(\);\s*return;\s*\}/);
    // After the reply: the result is dispatched, then the gate ends.
    expect(hook).toMatch(
      /sent\.then\(\(result\) => \{\s*dispatch\(\{ type: "start_result", result \}\);\s*gate\.end\(\);\s*\}\)/,
    );
    // The gate is made once, by the pure module, and nothing else sends a start.
    expect(windowFilesWith(/createStartGate\(\)/).sort()).toEqual(["src/contradictions.ts", "src/hooks/useContradictions.ts"]);
  });

  it("the unset start values are left out, never null (65i)", () => {
    const pure = code(sources.get("src/contradictions.ts") ?? "");
    expect(pure).toMatch(/const scope = inputs\.scope === "" \? \{\} : \{ scope: inputs\.scope \};/);
    expect(pure).toMatch(/\.\.\.\(cap === null \? \{\} : \{ cap \}\)/);
    expect(pure).not.toMatch(/scope:\s*null|cap:\s*null/);
  });

  it("the window sources name no write route and build no confirm step", () => {
    for (const [file, text] of sources) {
      const source = code(text);
      expect(source, file).not.toMatch(/ledger_(void|acknowledge|import)\b|questionnaire_start|vault_index_start/);
      expect(source, file).not.toMatch(/voidPromise|acknowledgePromise|importPromises|ledgerActions/);
      expect(source, file).not.toMatch(/confirm|ConfirmStep|useConfirm/i);
      expect(source, file).not.toMatch(/\bstyle=|<a[\s>]|href=|dangerouslySetInnerHTML|innerHTML/);
    }
    const routes = new Set();
    for (const [, text] of sources) {
      for (const match of code(text).matchAll(/route:\s*"([a-z_]+)"|callRoute<"([a-z_]+)">/g)) {
        routes.add(match[1] ?? match[2]);
      }
    }
    expect([...routes].sort()).toEqual(["contradictions_start", "contradictions_status"]);
  });

  it("no link is made from untrusted text: no anchor, no URL, no window.open in the contradictions files", () => {
    for (const [file, text] of sources) {
      const source = code(text);
      expect(source, file).not.toMatch(/https?:\/\/|window\.open|location\.|<a\b|href|mailto:|target="_blank"/);
    }
  });

  it("the model's confidence is never read by the screen, the chip or the hook", () => {
    for (const file of ["src/hooks/useContradictions.ts", "src/components/ContradictionsScreen.tsx", "src/components/FindingStatusChip.tsx"]) {
      expect(code(sources.get(file) ?? ""), file).not.toMatch(/model_confidence|confidence/i);
    }
  });

  it("no locale formatting or truncation touches a count or a quote (65i)", () => {
    for (const [file, text] of sources) {
      expect(code(text), file).not.toMatch(/Intl\.|toLocale|\.slice\(|\.substring\(|\.substr\(/);
    }
  });

  it("no storage, no timer of its own and no console in the contradictions files", () => {
    for (const [file, text] of sources) {
      expect(code(text), file).not.toMatch(/localStorage|sessionStorage|indexedDB|document\.cookie|setInterval|console\./);
    }
    // The only timer is the shared poller's.
    expect(windowFilesWith(/setTimeout/).sort()).not.toContain("src/contradictions.ts");
  });

  it("the screen has no cancel control and no word that guesses a time or a percent", () => {
    const screen = code(sources.get("src/components/ContradictionsScreen.tsx") ?? "");
    // The cancelled STATE has a message (CONTRA_CANCELLED_TEXT); there is no cancel CONTROL.
    expect(screen).not.toMatch(/onCancel|cancelRun|>\s*Cancel|abort|<progress|progressbar|stuck|\bETA\b/i);
    expect((screen.match(/<button/g) ?? []).length).toBe(1);
    const pure = code(sources.get("src/contradictions.ts") ?? "");
    expect(pure).not.toMatch(/stuck|\bETA\b|percent|Date\.now|secondsSince/i);
  });

  it("the state is kept in Screens, with no reset when the screen changes", () => {
    const screens = code(fs.readFileSync("src/screens.tsx", "utf8"));
    expect(screens).toMatch(/const contradictions = useContradictions\(healthy\);/);
    expect(screens).not.toMatch(/resetContradictions|contradictionsEventOnScreen/);
  });

  it("no new source file holds a literal bidirectional or control character", () => {
    const files = [...FILES, "src/contradictionsTestSupport.ts", "src/contradictions.test.ts", "src/components/contradictions.test.tsx", "scripts/contradictionsSources.test.mjs"];
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

  it("the three added fixed lines and both labels are the DESIGN.md 11.1 words (read from the file, LF-normalised)", () => {
    const design = fs.readFileSync("../../docs/DESIGN.md", "utf8").replace(/\r\n/g, "\n");
    const fixed = fs.readFileSync("src/fixedTexts.ts", "utf8").replace(/\r\n/g, "\n");
    for (const sentence of [
      "The model's answer for the same pair can differ between identical runs.",
      "Findings are not kept. Closing ALIBI or starting another run drops them.",
      "Both quotes are shown so you can decide. ALIBI does not decide which statement is right.",
      "Numbers differ (the model kept this pair in this run)",
      "Numbers differ (not kept)",
    ]) {
      expect(design, sentence).toContain(`| ${sentence}`);
      expect(fixed, sentence).toContain(`"${sentence}"`);
    }
  });

  it("ledgerText.ts keeps every 65i constant in its own DRAFT block after the 65h one", () => {
    const text = (fs.readFileSync("src/ledgerText.ts", "utf8")).replace(/\r\n/g, "\n");
    const start = text.indexOf("// ===== 65i DRAFT ");
    const end = text.indexOf("// ===== end of 65i DRAFT ");
    expect(start).toBeGreaterThan(text.indexOf("// ===== end of 65h DRAFT "));
    expect(end).toBeGreaterThan(start);
    expect(text.indexOf("// ===== 65i DRAFT ", start + 1)).toBe(-1);
    expect(text.slice(start, end)).toContain("They are DRAFTS until the owner approves them.");
    expect(text.slice(end)).not.toMatch(/export const/);
    let inside = 0;
    for (const match of text.matchAll(/^export const ([A-Z0-9_]+)/gm)) {
      const at = match.index ?? 0;
      const in65i = at > start && at < end;
      expect(in65i, match[1]).toBe(/^(CONTRA_|NAV_GROUP_CHECKS$|NAV_LABEL_CONTRADICTIONS$)/.test(match[1]));
      inside += in65i ? 1 : 0;
    }
    expect(inside).toBeGreaterThan(50);
  });
});
