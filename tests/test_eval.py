"""Evaluator checks avoid invented metrics and reject unsafe seed paths."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
import contextlib
import io
from unittest.mock import patch

import eval as evaluation


class EvaluationTests(unittest.TestCase):
    def test_relative_output_passes_absolute_paths_to_harness(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            relative = Path(directory).relative_to(Path.cwd())
            output = relative / "evaluation"
            output.mkdir()

            def generated(command, **kwargs):
                record = Path(command[command.index("--record") + 1])
                workspace = Path(command[command.index("--workspace") + 1])
                self.assertTrue(record.is_absolute())
                self.assertTrue(workspace.is_absolute())
                record.write_text(json.dumps({"status": "finished", "written_files": []}))
                return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

            with patch.object(evaluation.subprocess, "run", side_effect=generated), patch.object(evaluation, "run_check", return_value={"passed": True, "elapsed_seconds": 0}):
                result = evaluation.run_arm(label="baseline", spec={"task": "test", "checks": [["check"]]}, workspace=output / "baseline", output=output, backend="koboldcpp", engine="native", model=None, max_turns=1, timeout=5)
            self.assertTrue(result["acceptance_passed"])

    def test_failed_acceptance_sets_unsuccessful_evaluator_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = root / "spec.json"
            spec.write_text(json.dumps({"task": "test", "checks": [[sys.executable, "-c", "raise SystemExit(1)"]]}))
            for passed in (False, True):
                fake = {"harness_exit_code": 0, "acceptance_passed": passed, "wall_seconds": 1, "token_usage": {}, "write_count": 1}
                with patch("sys.argv", ["eval.py", str(spec), "--output", str(root / str(passed))]), patch.object(evaluation, "run_arm", side_effect=lambda **kw: dict(fake, label=kw["label"])), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(evaluation.main(), 0 if passed else 1)

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
