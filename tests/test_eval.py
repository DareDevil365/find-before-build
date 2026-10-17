"""Evaluator checks avoid invented metrics and reject unsafe seed paths."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

import eval as evaluation


class EvaluationTests(unittest.TestCase):
    def test_acceptance_check_reports_actual_exit_status(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            passed = evaluation.run_check([sys.executable, "-c", "print('ok')"], workspace, 5)
            failed = evaluation.run_check([sys.executable, "-c", "raise SystemExit(3)"], workspace, 5)
            self.assertTrue(passed["passed"])
            self.assertEqual(failed["exit_code"], 3)
            self.assertFalse(failed["passed"])

    def test_spec_requires_task_defined_checks_and_workspace_safe_seeds(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "spec.json"
            path.write_text(json.dumps({"task": "A task", "checks": [["python", "-m", "unittest"]], "seed_files": {"../escape": "x"}}))
            with self.assertRaises(ValueError):
                evaluation.load_spec(path)
            path.write_text(json.dumps({"task": "A task", "checks": [["python", "-m", "unittest"]], "seed_files": {"test.py": "print(1)"}}))
            spec = evaluation.load_spec(path)
            self.assertEqual(spec["task"], "A task")


if __name__ == "__main__":
    unittest.main()
