"""Workspace containment and tool approval boundaries."""

from pathlib import Path
import tempfile
import unittest

from harness_core.workspace import Workspace


class WorkspaceTests(unittest.TestCase):
    def test_write_read_and_traversal_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Workspace(Path(temp), confirm_commands=False)
            self.assertIn("Wrote", workspace.call("write_file", {"path": "src/main.py", "content": "print('ok')\n"}))
            self.assertEqual(workspace.call("read_file", {"path": "src/main.py"}), "print('ok')\n")
            self.assertIn("ERROR", workspace.call("write_file", {"path": "../outside.py", "content": "bad"}))
            self.assertFalse((Path(temp).parent / "outside.py").exists())

    def test_command_disabled_cannot_execute(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Workspace(Path(temp), confirm_commands=False)
            result = workspace.call("run_command", {"argv": ["python", "-c", "open('oops','w').write('x')"]})
            self.assertIn("disabled", result)
            self.assertFalse((Path(temp) / "oops").exists())
            self.assertEqual(workspace.log[-1]["status"], "disabled")

    def test_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp)
            link = root / "escape"
            try:
                link.symlink_to(Path(outside), target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Symlinks unavailable in this environment")
            workspace = Workspace(root, confirm_commands=False)
            self.assertIn("ERROR", workspace.call("write_file", {"path": "escape/file.txt", "content": "bad"}))
            self.assertFalse((Path(outside) / "file.txt").exists())


if __name__ == "__main__":
    unittest.main()
