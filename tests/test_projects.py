"""Named outputs must never overwrite a prior program or leak dependency files."""

from pathlib import Path
import tempfile
import unittest
import contextlib
import io
import json
from unittest.mock import patch

from harness_core.projects import create_project, project_name
from ui import Job
import harness


class ProjectTests(unittest.TestCase):
    def test_cli_defaults_to_named_results_and_keeps_record_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def build(args):
                self.assertEqual(args.workspace, root / "Results" / "calculator")
                self.assertEqual(args.record, root / "runs" / "builds" / "calculator" / "record.json")
                manifest = json.loads((args.record.parent / "build.json").read_text())
                self.assertEqual(manifest["workspace"], "Results/calculator")
                return {"status": "finished", "elapsed_seconds": 0, "model_calls": 0, "token_usage": {}, "written_files": [], "model_written_lines": 0}

            with patch.object(harness, "ROOT", root), patch("sys.argv", ["harness.py", "Build a calculator"]), patch.object(harness, "run", side_effect=build), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(harness.main(), 0)

    def test_safe_readable_names_and_collision_numbering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = create_project(root, "Build a scientific calculator with keyboard input")
            (first / "main.py").write_text("original")
            second = create_project(root, "Build a scientific calculator with keyboard input")
            self.assertEqual(first, root / "Results" / "scientific-calculator")
            self.assertEqual(second.name, "scientific-calculator-2")
            self.assertEqual((first / "main.py").read_text(), "original")
            self.assertEqual(create_project(root, "ignored", "../../My App").parent, root / "Results")
            self.assertEqual(create_project(root, "ignored", "Build tools with history").name, "build-tools-with-history")
            self.assertEqual(project_name("CON"), "con-app")
            self.assertEqual(project_name("???"), "program")

    def test_output_list_excludes_dependencies_and_cache_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("main.py", "node_modules/pkg/index.js", "__pycache__/main.pyc", ".venv/config", ".env", "src/app.js"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("test")
            job = Job("test", "task", "test", root, root / "record.json")
            self.assertEqual(job.files(), ["main.py", "src/app.js"])


if __name__ == "__main__":
    unittest.main()
