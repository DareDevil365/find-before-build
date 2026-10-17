"""Selected source repositories stay read-only until an attributed import."""

import tempfile
from pathlib import Path
import unittest

from harness_core.reuse import selected_names
from harness_core.workspace import Workspace


class ReuseTests(unittest.TestCase):
    def test_selection_is_bounded_and_deduplicated(self):
        decision = {"repositories": ["owner/one", "owner/one", "owner/two", "owner/three", "owner/four"]}
        self.assertEqual(selected_names(decision), ["owner/one", "owner/two", "owner/three"])

    def test_import_copies_license_and_attribution(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            source.mkdir()
            (source / "LICENSE").write_text("MIT test license", encoding="utf-8")
            (source / "parse.py").write_text("def parse(): return 1\n", encoding="utf-8")
            workspace = Workspace(root / "target", references={"owner/repo": {"root": source.resolve(), "url": "https://github.com/owner/repo", "commit": "abc123", "license": "MIT"}})
            result = workspace.call("import_repo_file", {"repository": "owner/repo", "path": "parse.py", "destination": "parser.py"})
            self.assertIn("Imported", result)
            self.assertIn("parse", (workspace.root / "parser.py").read_text(encoding="utf-8"))
            self.assertIn("MIT test", (workspace.root / "third_party_licenses" / "owner__repo.txt").read_text(encoding="utf-8"))
            self.assertIn("owner/repo", (workspace.root / "THIRD_PARTY_SOURCES.md").read_text(encoding="utf-8"))

    def test_source_cannot_escape_selected_repository(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            source.mkdir()
            workspace = Workspace(root / "target", references={"owner/repo": {"root": source.resolve(), "url": "https://github.com/owner/repo", "commit": "abc123", "license": "MIT"}})
            self.assertIn("ERROR", workspace.call("read_repo_file", {"repository": "owner/repo", "path": "../outside.txt"}))


if __name__ == "__main__":
    unittest.main()
