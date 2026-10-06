import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import check_policy_drift as drift

CANONICAL = """# Title

## 1. Intro

Not mirrored.

---

## 3. Air-gap

Rule A.
Rule B.

---

## 7. Standards

Standard one.

---

## 11. Never do

- Never X.
"""


def mirror_from(text: str) -> str:
    """What a faithful mirror looks like: the same sections without the `---` separators."""
    keep = text.split("## 3.", 1)[1]
    return "---\nalwaysApply: true\n---\n\n# Mirror\n\n## 3." + keep.replace("\n---\n", "\n")


class SyntheticFiles(unittest.TestCase):
    def test_faithful_mirror_is_in_sync(self) -> None:
        self.assertEqual(drift.find_drift(CANONICAL, mirror_from(CANONICAL)), [])

    def test_trailing_rules_crlf_and_trailing_spaces_are_not_drift(self) -> None:
        mirror = mirror_from(CANONICAL).replace("Rule A.", "Rule A.   ").replace("\n", "\r\n")
        self.assertEqual(drift.find_drift(CANONICAL, mirror), [])

    def test_changed_line_is_reported_with_a_diff(self) -> None:
        mirror = mirror_from(CANONICAL).replace("Rule B.", "Rule B, weakened.")
        problems = drift.find_drift(CANONICAL, mirror)
        self.assertEqual(len(problems), 1)
        self.assertIn("Section 3 differs", problems[0])
        self.assertIn("-Rule B.", problems[0])
        self.assertIn("+Rule B, weakened.", problems[0])

    def test_changed_heading_is_drift(self) -> None:
        mirror = mirror_from(CANONICAL).replace("## 7. Standards", "## 7. Code standards")
        self.assertIn("Section 7 differs", drift.find_drift(CANONICAL, mirror)[0])

    def test_missing_section_in_the_rule_is_reported(self) -> None:
        mirror = mirror_from(CANONICAL).split("## 11.")[0]
        self.assertEqual(drift.find_drift(CANONICAL, mirror), ["Cursor rule is missing section 11"])

    def test_missing_section_in_claude_md_is_reported(self) -> None:
        canonical = CANONICAL.split("## 11.")[0]
        problems = drift.find_drift(canonical, mirror_from(CANONICAL))
        self.assertIn("CLAUDE.md is missing section 11", problems)

    def test_extra_numbered_section_in_the_rule_is_reported(self) -> None:
        mirror = mirror_from(CANONICAL) + "\n## 5. Something else\n\nText.\n"
        problems = drift.find_drift(CANONICAL, mirror)
        self.assertEqual(len(problems), 1)
        self.assertIn("must not mirror: 5", problems[0])

    def test_duplicate_section_is_reported(self) -> None:
        mirror = mirror_from(CANONICAL) + "\n## 3. Air-gap\n\nRule A.\nRule B.\n"
        self.assertIn(
            "Cursor rule has section 3 more than once", drift.find_drift(CANONICAL, mirror)
        )


class RealFiles(unittest.TestCase):
    def test_the_real_cursor_rule_matches_claude_md(self) -> None:
        claude = drift.DEFAULT_CLAUDE.read_text(encoding="utf-8")
        rule = drift.DEFAULT_RULE.read_text(encoding="utf-8")
        self.assertEqual(drift.find_drift(claude, rule), [])

    def test_editing_one_real_rule_line_is_caught(self) -> None:
        claude = drift.DEFAULT_CLAUDE.read_text(encoding="utf-8")
        rule = drift.DEFAULT_RULE.read_text(encoding="utf-8")
        edited = rule.replace("Never `0.0.0.0` or `::`.", "Sometimes `0.0.0.0` is fine.")
        self.assertNotEqual(edited, rule, "the line this test edits no longer exists")
        problems = drift.find_drift(claude, edited)
        self.assertEqual(len(problems), 1)
        self.assertIn("Section 3 differs", problems[0])


class CommandLine(unittest.TestCase):
    def run_main(self, claude: str | None, rule: str | None) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            claude_path = Path(tmp) / "CLAUDE.md"
            rule_path = Path(tmp) / "rule.mdc"
            if claude is not None:
                claude_path.write_text(claude, encoding="utf-8")
            if rule is not None:
                rule_path.write_text(rule, encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = drift.main(["--claude", str(claude_path), "--rule", str(rule_path)])
        return code, out.getvalue(), err.getvalue()

    def test_exit_0_when_in_sync(self) -> None:
        code, out, _ = self.run_main(CANONICAL, mirror_from(CANONICAL))
        self.assertEqual(code, 0)
        self.assertIn("matches", out)

    def test_exit_1_and_diff_on_stderr_when_drifted(self) -> None:
        code, out, err = self.run_main(
            CANONICAL, mirror_from(CANONICAL).replace("Rule A.", "Rule Z.")
        )
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("Section 3 differs", err)
        self.assertIn("CLAUDE.md is canonical", err)

    def test_exit_2_when_a_file_is_missing(self) -> None:
        code, _, err = self.run_main(CANONICAL, None)
        self.assertEqual(code, 2)
        self.assertIn("cannot read policy file", err)


if __name__ == "__main__":
    unittest.main()
