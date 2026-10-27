"""Pinned read-only repository inspection, source navigation, and partial failures."""

import base64
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


directory = Path(__file__).resolve().parents[1] / "find-before-build" / "scripts"
sys.path.insert(0, str(directory))
spec = importlib.util.spec_from_file_location("inspect_repo", directory / "inspect_repo.py")
inspector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inspector)


class InspectionTests(unittest.TestCase):
    def test_reads_are_pinned_and_directories_enable_followup_navigation(self):
        sha = "a" * 40
        calls = []

        def fetch(path, token):
            calls.append(path)
            if path == "/repos/demo/parser":
                return {"full_name": "demo/parser", "default_branch": "main", "license": {"spdx_id": "MIT"}}
            if "/commits/" in path:
                return {"sha": sha}
            self.assertIn("ref=" + sha, path)
            if path.endswith("/contents?ref=" + sha):
                return [{"type": "file", "path": "README.md"}, {"type": "dir", "path": "src"}]
            if "/contents/src?" in path:
                return [{"type": "file", "path": "src/parser.py"}]
            if "/contents/missing.py?" in path:
                raise RuntimeError("GitHub HTTP 404")
            return {"type": "file", "encoding": "base64", "size": 12, "content": base64.b64encode(b"Parser usage").decode()}

        with patch.object(inspector, "fetch_json", side_effect=fetch):
            result = inspector.inspect_repository("demo/parser", ["src", "missing.py", "AGENTS.md"], find=["usage"])
        self.assertEqual(result["commit"], sha)
        self.assertEqual(result["directories"][0]["entries"][0]["path"], "src/parser.py")
        self.assertIn(sha, result["files"][0]["url"])
        self.assertIn("#L1-L1", result["files"][0]["passages"][0]["url"])
        self.assertEqual(len(result["warnings"]), 2)
        self.assertFalse(any("/contents/AGENTS.md" in path for path in calls))

    def test_invalid_repository_name_cannot_make_a_request(self):
        with patch.object(inspector, "fetch_json") as fetch:
            with self.assertRaises(ValueError):
                inspector.inspect_repository("https://example.org/repo")
            fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
