import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import check_junit_skips as skips

REASON = "Windows-only path behavior"


def junit(*cases: str) -> str:
    return (
        '<?xml version="1.0"?><testsuites><testsuite name="pytest">'
        + "".join(cases)
        + ("</testsuite></testsuites>")
    )


def passed(name: str) -> str:
    return f'<testcase classname="tests.test_x" name="{name}"/>'


def skipped(name: str, message: str, kind: str = "pytest.skip") -> str:
    return (
        f'<testcase classname="tests.test_x" name="{name}">'
        f'<skipped type="{kind}" message="{message}">detail</skipped></testcase>'
    )


class CheckSkips(unittest.TestCase):
    def run_main(self, xml: str | None, *args: str) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "junit.xml"
            if xml is not None:
                report.write_text(xml, encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = skips.main([str(report), *args])
        return code, out.getvalue(), err.getvalue()

    def test_no_skips_passes_even_with_nothing_allowed(self) -> None:
        code, out, _ = self.run_main(junit(passed("a"), passed("b")))
        self.assertEqual(code, 0)
        self.assertIn("2 tests, 0 skipped", out)

    def test_allowed_skip_passes(self) -> None:
        code, out, _ = self.run_main(junit(passed("a"), skipped("b", REASON)), "--allow", REASON)
        self.assertEqual(code, 0)
        self.assertIn("1 skipped (1 with an allowed reason)", out)

    def test_any_skip_fails_when_nothing_is_allowed(self) -> None:  # Windows rule
        code, _, err = self.run_main(junit(passed("a"), skipped("b", REASON)))
        self.assertEqual(code, 1)
        self.assertIn("tests.test_x::b", err)
        self.assertIn(REASON, err)

    def test_other_reason_fails_even_if_one_reason_is_allowed(self) -> None:
        xml = junit(skipped("b", REASON), skipped("c", "needs network"))
        code, _, err = self.run_main(xml, "--allow", REASON)
        self.assertEqual(code, 1)
        self.assertIn("tests.test_x::c", err)
        self.assertNotIn("tests.test_x::b", err)

    def test_reason_must_match_exactly(self) -> None:
        code, _, _ = self.run_main(junit(skipped("b", REASON + " (extra)")), "--allow", REASON)
        self.assertEqual(code, 1)

    def test_xfail_counts_as_a_skip(self) -> None:
        code, _, _ = self.run_main(
            junit(skipped("b", "expected", "pytest.xfail")), "--allow", REASON
        )
        self.assertEqual(code, 1)

    def test_missing_report_is_an_error_not_a_pass(self) -> None:
        code, _, err = self.run_main(None, "--allow", REASON)
        self.assertEqual(code, 2)
        self.assertIn("cannot read junit report", err)

    def test_malformed_report_is_an_error(self) -> None:
        self.assertEqual(self.run_main("<testsuite><oops")[0], 2)

    def test_report_with_no_tests_is_an_error(self) -> None:
        code, _, err = self.run_main(junit())
        self.assertEqual(code, 2)
        self.assertIn("contains no tests", err)


if __name__ == "__main__":
    unittest.main()
