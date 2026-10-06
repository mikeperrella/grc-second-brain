import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import check_semgrep_rule_coverage as coverage

RULES = "rules:\n  - id: rule-a\n    languages: [python]\n  - id: rule-b\n    languages: [rust]\n"


class CheckCoverage(unittest.TestCase):
    def run_main(self, rules: str, fixtures: dict[str, str]) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "rules").mkdir()
            (root / "fixtures").mkdir()
            (root / "rules" / "r.yml").write_text(rules, encoding="utf-8")
            for name, text in fixtures.items():
                (root / "fixtures" / name).write_text(text, encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = coverage.main([str(root / "rules"), str(root / "fixtures")])
        return code, out.getvalue(), err.getvalue()

    def test_every_rule_annotated_passes(self) -> None:
        code, out, _ = self.run_main(
            RULES, {"f.py": "# ruleid: rule-a\nx = 1\n", "f.rs": "// ruleid: rule-b\n"}
        )
        self.assertEqual(code, 0)
        self.assertIn("2 rules, 2 with", out)

    def test_comma_separated_ids_count(self) -> None:
        code, _, _ = self.run_main(RULES, {"f.py": "# ruleid: rule-a, rule-b\nx = 1\n"})
        self.assertEqual(code, 0)

    def test_rule_without_annotation_fails(self) -> None:
        code, _, err = self.run_main(RULES, {"f.py": "# ruleid: rule-a\nx = 1\n"})
        self.assertEqual(code, 1)
        self.assertIn("rule-b", err)

    def test_ok_annotation_alone_does_not_count(self) -> None:
        code, _, err = self.run_main(
            RULES, {"f.py": "# ruleid: rule-a\n# ok: rule-b\nx = 1\n"}
        )
        self.assertEqual(code, 1)
        self.assertIn("rule-b", err)

    def test_annotation_for_unknown_rule_fails(self) -> None:
        code, _, err = self.run_main(
            RULES, {"f.py": "# ruleid: rule-a, rule-b\n# ruleid: rule-typo\n"}
        )
        self.assertEqual(code, 1)
        self.assertIn("rule-typo", err)

    def test_no_annotations_is_an_error_not_a_pass(self) -> None:
        code, _, _ = self.run_main(RULES, {"f.py": "x = 1\n"})
        self.assertEqual(code, 2)

    def test_missing_directory_is_an_error(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = coverage.main(["/nonexistent-rules", "/nonexistent-fixtures"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
