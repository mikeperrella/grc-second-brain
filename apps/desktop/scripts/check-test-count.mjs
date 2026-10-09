// CI guard: fails if the Vitest JSON report says no test ran, or if the report is missing or
// unreadable. `vitest run` with no test files used to exit 0 (the template had none), which is a
// green that checks nothing. Usage: node scripts/check-test-count.mjs <vitest-report.json>
//
// `evaluateReport` is pure so scripts/check-test-count.selftest.mjs can exercise it without
// running Vitest.

import fs from "node:fs";
import process from "node:process";
import { pathToFileURL } from "node:url";

const COUNTS = [
  "numTotalTests",
  "numPassedTests",
  "numFailedTests",
  "numPendingTests",
  "numTodoTests",
];

/** @param {string} text the contents of the Vitest JSON report */
export function evaluateReport(text) {
  let report;
  try {
    report = JSON.parse(text);
  } catch {
    return { ok: false, line: "", problem: "the report is not valid JSON" };
  }
  if (report === null || typeof report !== "object") {
    return { ok: false, line: "", problem: "the report is not a JSON object" };
  }
  for (const name of COUNTS) {
    if (!Number.isInteger(report[name])) {
      return { ok: false, line: "", problem: `the report has no integer "${name}"` };
    }
  }
  const total = report.numTotalTests;
  const passed = report.numPassedTests;
  const failed = report.numFailedTests;
  const skipped = report.numPendingTests + report.numTodoTests;
  const line = `Frontend tests run: ${total} (passed ${passed}, failed ${failed}, skipped ${skipped})`;
  // A suite where every test is skipped also checks nothing, so "ran" means passed or failed.
  if (passed + failed === 0) {
    return { ok: false, line, problem: "no test ran (zero passed and zero failed)" };
  }
  return { ok: true, line };
}

function main() {
  const path = process.argv[2];
  if (path === undefined) {
    console.error("usage: node scripts/check-test-count.mjs <vitest-report.json>");
    return 1;
  }
  let text;
  try {
    text = fs.readFileSync(path, "utf8");
  } catch {
    console.error(`FAIL: cannot read the Vitest report at ${path}; did the test step crash?`);
    return 1;
  }
  const result = evaluateReport(text);
  if (result.line !== "") {
    console.log(result.line);
  }
  const summary = process.env.GITHUB_STEP_SUMMARY;
  if (summary !== undefined && summary !== "") {
    const note = result.ok ? "" : `\n\n**FAIL: ${result.problem}**`;
    fs.appendFileSync(summary, `### Frontend tests\n\n${result.line || "no report"}${note}\n`);
  }
  if (!result.ok) {
    console.error(`FAIL: ${result.problem}`);
    return 1;
  }
  return 0;
}

if (process.argv[1] !== undefined && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = main();
}
