"""Constrained file and command tools for the coding loop."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess


TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "list_files", "description": "List workspace files, excluding hidden and generated directories.", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 file inside the workspace.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Create or replace a UTF-8 file inside the workspace. Send complete contents.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "run_command", "description": "Run an argv command in the workspace, after explicit terminal confirmation.", "parameters": {"type": "object", "properties": {"argv": {"type": "array", "items": {"type": "string"}}}, "required": ["argv"]}}},
]


class Workspace:
    def __init__(self, root: Path, confirm_commands: bool = True):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.confirm_commands = confirm_commands
        self.log: list[dict] = []

    def path(self, given: str) -> Path:
        if not isinstance(given, str) or not given or "\x00" in given:
            raise ValueError("Invalid workspace path")
        path = (self.root / given).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError("Path is outside the workspace")
        return path

    def call(self, name: str, arguments: dict) -> str:
        try:
            if name == "list_files":
                paths = []
                for path in self.root.rglob("*"):
                    if path.is_file() and not any(part.startswith(".") or part in {"__pycache__", "node_modules", "runs"} for part in path.relative_to(self.root).parts):
                        paths.append(path.relative_to(self.root).as_posix())
                    if len(paths) >= 100:
                        break
                result = json.dumps(sorted(paths))
            elif name == "read_file":
                path = self.path(arguments["path"])
                if path.stat().st_size > 100_000:
                    raise ValueError("File exceeds 100 KB read limit")
                result = path.read_text(encoding="utf-8")
            elif name == "write_file":
                path = self.path(arguments["path"])
                content = arguments["content"]
                if not isinstance(content, str) or len(content) > 100_000:
                    raise ValueError("Content must be text under 100 KB")
                path.parent.mkdir(parents=True, exist_ok=True)
                # Re-resolve after mkdir so a link introduced during creation cannot escape.
                self.path(arguments["path"]).write_text(content, encoding="utf-8")
                result = f"Wrote {path.relative_to(self.root).as_posix()} ({len(content)} characters)"
            elif name == "run_command":
                argv = arguments["argv"]
                if not isinstance(argv, list) or not argv or any(not isinstance(x, str) or not x for x in argv):
                    raise ValueError("argv must be a nonempty array of strings")
                if self.confirm_commands:
                    print(f"Command requested in {self.root}: {json.dumps(argv)}")
                    if input("Run it? [y/N] ").strip().lower() != "y":
                        result = "Command denied by user"
                        self.log.append({"tool": name, "status": "denied"})
                        return result
                else:
                    result = "Command disabled in this run"
                    self.log.append({"tool": name, "status": "disabled"})
                    return result
                completed = subprocess.run(argv, cwd=self.root, capture_output=True, text=True, timeout=30, shell=False, errors="replace")
                result = json.dumps({"exit_code": completed.returncode, "stdout": completed.stdout[:8000], "stderr": completed.stderr[:4000]})
            else:
                raise ValueError(f"Unknown tool: {name}")
            self.log.append({"tool": name, "status": "ok", "path": arguments.get("path") if name in {"read_file", "write_file"} else None})
            return result[:12000]
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
            self.log.append({"tool": name, "status": "error", "error": str(exc)[:250]})
            return f"ERROR: {exc}"
