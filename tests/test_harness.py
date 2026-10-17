"""Regression checks for truthful executor status and bounded query terms."""

import unittest

from harness import classify_aider_result, queries_for


class HarnessTests(unittest.TestCase):
    def test_partial_aider_edit_is_not_success(self):
        before = {"calculator.py": "", "README.md": ""}
        after = {"calculator.py": "def add(a,b): return a+b\n", "README.md": ""}
        self.assertEqual(classify_aider_result("finished", before, after, list(before), "Applied edit to calculator.py"), "partial_edit")

    def test_no_aider_edit_is_not_success(self):
        before = {"greeting.py": ""}
        self.assertEqual(classify_aider_result("finished", before, before, list(before), ""), "no_files_written")

    def test_task_search_terms_drop_scaffolding(self):
        first, second = queries_for("Build a small Python module named csv_cleaner.py to normalize CSV")
        self.assertIn("csv", first)
        self.assertIn("cleaner", first)
        self.assertNotIn("module", first)
        self.assertIn("python", second)


if __name__ == "__main__":
    unittest.main()
