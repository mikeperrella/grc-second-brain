// Self-test of the zero-test guard: proves it can fail. A guard that was never seen to fail could
// be a hollow check itself. Run in CI before the guard runs on the real report.
// Usage: node scripts/check-test-count.selftest.mjs   (exit 0 = the guard behaves)

import process from "node:process";
import { evaluateReport } from "./check-test-count.mjs";

const report = (overrides) =>
  JSON.stringify({
    numTotalTests: 3,
    numPassedTests: 3,
    numFailedTests: 0,
    numPendingTests: 0,
    numTodoTests: 0,
    ...overrides,
  });

const cases = [
  ["a normal report passes", report({}), true],
  ["a report with failures still counts as 'tests ran'", report({ numPassedTests: 2, numFailedTests: 1 }), true],
  ["an empty suite fails", report({ numTotalTests: 0, numPassedTests: 0 }), false],
  ["an all-skipped suite fails", report({ numPassedTests: 0, numPendingTests: 3 }), false],
  ["a missing count fails", JSON.stringify({ numTotalTests: 3 }), false],
  ["a non-integer count fails", report({ numPassedTests: "3" }), false],
  ["not JSON fails", "{oops", false],
  ["an empty file fails", "", false],
  ["null fails", "null", false],
];

let wrong = 0;
for (const [name, text, expected] of cases) {
  const actual = evaluateReport(text).ok;
  const verdict = actual === expected ? "ok" : "WRONG";
  console.log(`${verdict}: ${name} (guard said ${actual ? "pass" : "fail"})`);
  if (actual !== expected) {
    wrong += 1;
  }
}
if (wrong > 0) {
  console.error(`FAIL: ${wrong} self-test case(s) behaved wrongly`);
  process.exitCode = 1;
}
