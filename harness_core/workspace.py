"""Constrained file and command tools for the coding loop."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.parse import urlsplit


TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "list_files", "description": "List workspace files, excluding hidden and generated directories.", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 file inside the workspace.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Create or replace a UTF-8 file inside the workspace. Send complete contents.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "run_command", "description": "Run an argv command in the workspace, after explicit terminal confirmation.", "parameters": {"type": "object", "properties": {"argv": {"type": "array", "items": {"type": "string"}}}, "required": ["argv"]}}},
    {"type": "function", "function": {"name": "ask_user", "description": "Ask the human a concise question only when a material requirement is missing or blocked; waits for an answer.", "parameters": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}}},
]

REFERENCE_TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "list_repo_files", "description": "List files in a selected, downloaded GitHub source repository without running them.", "parameters": {"type": "object", "properties": {"repository": {"type": "string"}}, "required": ["repository"]}}},
    {"type": "function", "function": {"name": "read_repo_file", "description": "Read a small UTF-8 file from a selected source repository.", "parameters": {"type": "object", "properties": {"repository": {"type": "string"}, "path": {"type": "string"}}, "required": ["repository", "path"]}}},
    {"type": "function", "function": {"name": "import_repo_file", "description": "Copy a small source file into the workspace for adaptation; records source and license. Never executes it.", "parameters": {"type": "object", "properties": {"repository": {"type": "string"}, "path": {"type": "string"}, "destination": {"type": "string"}}, "required": ["repository", "path", "destination"]}}},
]

WEB_TOOL_SCHEMA = {"type": "function", "function": {"name": "inspect_url", "description": "Read HTML table structure from a URL explicitly provided in the user's task; never execute page code.", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class Workspace:
    def __init__(self, root: Path, confirm_commands: bool = True, references: dict | None = None, ask_user=None, allowed_urls: set[str] | None = None):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.confirm_commands = confirm_commands
        self.references = references or {}
        self.ask_user = ask_user
        self.allowed_urls = allowed_urls or set()
        self.log: list[dict] = []

    def path(self, given: str) -> Path:
        if not isinstance(given, str) or not given or "\x00" in given:
            raise ValueError("Invalid workspace path")
        path = (self.root / given).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError("Path is outside the workspace")
        return path

    def reference_path(self, repository: str, given: str) -> Path:
        if repository not in self.references:
            raise ValueError("Repository is not in the selected reference set")
        if not isinstance(given, str) or not given or "\x00" in given:
            raise ValueError("Invalid source path")
        root = self.references[repository]["root"]
        path = (root / given).resolve()
        if not path.is_relative_to(root) or path == root or ".git" in path.relative_to(root).parts:
            raise ValueError("Source path is outside the selected repository")
        if path.name.lower() in {"agents.md", "claude.md", "gemini.md", "codex.md"}:
            raise ValueError("Repository agent instructions are not source material")
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
            elif name == "list_repo_files":
                repository = arguments["repository"]
                if repository not in self.references:
                    raise ValueError("Repository is not in the selected reference set")
                root = self.references[repository]["root"]
                paths = []
                for path in root.rglob("*"):
                    relative = path.relative_to(root)
                    if path.is_file() and path.name.lower() not in {"agents.md", "claude.md", "gemini.md", "codex.md"} and not any(part.startswith(".") or part in {"node_modules", "__pycache__"} for part in relative.parts):
                        paths.append(relative.as_posix())
                    if len(paths) >= 200:
                        break
                result = json.dumps(sorted(paths))
            elif name == "read_repo_file":
                path = self.reference_path(arguments["repository"], arguments["path"])
                if path.stat().st_size > 30_000:
                    raise ValueError("Source file exceeds 30 KB read limit")
                result = path.read_text(encoding="utf-8")
            elif name == "import_repo_file":
                repository = arguments["repository"]
                source = self.reference_path(repository, arguments["path"])
                if source.stat().st_size > 30_000:
                    raise ValueError("Source file exceeds 30 KB import limit")
                content = source.read_text(encoding="utf-8")
                destination = self.path(arguments["destination"])
                if destination.exists():
                    raise ValueError("Destination already exists; use read_file/write_file to adapt it")
                reference = self.references[repository]
                license_file = next((p for p in reference["root"].iterdir() if p.is_file() and p.name.lower().startswith(("license", "licence", "copying")) and p.stat().st_size <= 50_000), None)
                if license_file is None:
                    raise ValueError("Source license text was not found; import skipped")
                destination.parent.mkdir(parents=True, exist_ok=True)
                self.path(arguments["destination"]).write_text(content, encoding="utf-8")
                safe_name = repository.replace("/", "__")
                license_target = self.root / "third_party_licenses" / f"{safe_name}.txt"
                license_target.parent.mkdir(parents=True, exist_ok=True)
                if not license_target.exists():
                    license_target.write_text(license_file.read_text(encoding="utf-8"), encoding="utf-8")
                attribution = self.root / "THIRD_PARTY_SOURCES.md"
                source_url = f"{reference['url']}/blob/{reference['commit']}/{quote(arguments['path'])}"
                with attribution.open("a", encoding="utf-8") as handle:
                    handle.write(f"- `{destination.relative_to(self.root).as_posix()}` adapted from [{repository}]({source_url}) ({reference['license']}); license in `third_party_licenses/{safe_name}.txt`.\n")
                result = f"Imported {arguments['path']} from {repository} to {arguments['destination']} with attribution and license"
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
            elif name == "ask_user":
                question = arguments["question"]
                if not isinstance(question, str) or not 5 <= len(question.strip()) <= 300:
                    raise ValueError("Question must be 5-300 characters")
                if self.ask_user is None:
                    result = input(question.strip() + "\n> ").strip()
                else:
                    result = self.ask_user(question.strip())
                if not isinstance(result, str) or not result.strip():
                    result = "No answer supplied"
                self.log.append({"tool": name, "status": "answered", "question": question.strip(), "answer": result[:1000]})
                return result[:1000]
            elif name == "inspect_url":
                url = arguments["url"]
                if url not in self.allowed_urls or urlsplit(url).scheme != "https":
                    raise ValueError("URL was not explicitly supplied in the task")
                request = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; FindBeforeBuild/0.2)", "Accept": "text/html"})
                with build_opener(_NoRedirect).open(request, timeout=15) as response:
                    if not str(response.headers.get("Content-Type", "")).lower().startswith(("text/html", "text/plain")):
                        raise ValueError("URL is not a text page")
                    body = response.read(200_001)
                    if len(body) > 200_000:
                        raise ValueError("Page exceeds 200 KB inspection limit")
                    html = body.decode("utf-8", "replace")
                    tables = re.findall(r"<table\b[\s\S]*?</table>", html, re.I)
                    result = json.dumps({"url": response.geturl(), "fetched_at": datetime.now(timezone.utc).isoformat(), "table_count": len(tables), "tables_html": [table[:4500] for table in tables[:2]], "text_excerpt": re.sub(r"<[^>]+>", " ", html)[:1200]}, ensure_ascii=False)
            else:
                raise ValueError(f"Unknown tool: {name}")
            self.log.append({"tool": name, "status": "ok", "path": arguments.get("path") if name in {"read_file", "write_file", "read_repo_file", "import_repo_file"} else None})
            return result[:12000]
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
            self.log.append({"tool": name, "status": "error", "error": str(exc)[:250]})
            return f"ERROR: {exc}"
