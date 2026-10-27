"""Inspect a GitHub repository at one commit without cloning or running it."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
from urllib.parse import quote

from search_repos import fetch_json, now_iso


def inspect_repository(name: str, paths: list[str] | None = None, ref: str | None = None, find: list[str] | None = None) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name):
        raise ValueError("Use an exact owner/repository name")
    token = os.getenv("GITHUB_TOKEN")
    metadata = fetch_json(f"/repos/{name}", token)
    if not isinstance(metadata, dict) or not metadata.get("full_name"):
        raise RuntimeError("Repository metadata was unavailable")
    requested_ref = ref or metadata.get("default_branch") or "HEAD"
    commit = fetch_json(f"/repos/{name}/commits/{quote(requested_ref, safe='')}", token)
    sha = commit.get("sha") if isinstance(commit, dict) else None
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40,64}", sha):
        raise RuntimeError("Could not pin the inspection to a commit")
    entries = fetch_json(f"/repos/{name}/contents?ref={sha}", token)
    if not isinstance(entries, list):
        raise RuntimeError("Repository root listing was unavailable")
    names = [item.get("path", "") for item in entries if isinstance(item, dict) and item.get("type") == "file"]
    automatic = [path for path in names if path.lower().startswith(("readme", "license", "licence", "copying")) or path.lower() in {"package.json", "pyproject.toml", "cargo.toml", "go.mod", "requirements.txt"}]
    wanted = list(dict.fromkeys([*automatic, *(paths or [])]))
    files, warnings, directories = [], [], []
    readme = ""
    for given in wanted:
        path = PurePosixPath(given)
        if not isinstance(given, str) or "\\" in given or path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("Source paths must be relative and stay inside the repository")
        if path.name.lower() in {"agents.md", "claude.md", "gemini.md", "codex.md", ".env"} or path.suffix.lower() in {".key", ".pem"}:
            warnings.append(f"{given}: skipped instructions or secret-like file")
            continue
        try:
            item = fetch_json(f"/repos/{name}/contents/{quote(given, safe='/')}?ref={sha}", token)
            if isinstance(item, list):
                directories.append({"path": given, "entries": [{"path": entry.get("path"), "type": entry.get("type")} for entry in item if isinstance(entry, dict)]})
                continue
            if not isinstance(item, dict) or item.get("type") != "file" or item.get("encoding") != "base64":
                raise ValueError("Not a regular readable source file")
            if item.get("size", 0) > 1_000_000:
                raise ValueError("File exceeds the 1 MB per-file inspection limit")
            body = base64.b64decode(item.get("content", ""), validate=False)
            if len(body) > 1_000_000:
                raise ValueError("Decoded file exceeds the inspection limit")
            content = body.decode("utf-8")
            if path.name.lower().startswith("readme"):
                readme = content[:12000]
            url = f"https://github.com/{name}/blob/{sha}/{quote(given, safe='/')}"
            lines = content.splitlines()
            passages = []
            last_end = 0
            for number, line in enumerate(lines, 1):
                if number <= last_end or not any(term.casefold() in line.casefold() for term in find or []):
                    continue
                start, end = max(1, number - 3), min(len(lines), number + 4)
                passages.append({"line_start": start, "line_end": end, "url": f"{url}#L{start}-L{end}", "text": "\n".join(lines[start - 1:end])[:2500]})
                last_end = end
                if len(passages) == 8:
                    break
            files.append({"path": given, "url": url, "content": content[:12000], "truncated": len(content) > 12000, "passages": passages})
        except (RuntimeError, ValueError, UnicodeError) as exc:
            warnings.append(f"{given}: {str(exc)[:200]}")
    record = {"full_name": metadata["full_name"], "url": metadata.get("html_url", f"https://github.com/{name}"), "description": metadata.get("description") or "", "language": metadata.get("language"), "topics": metadata.get("topics") or [],
              "license": (metadata.get("license") or {}).get("spdx_id"), "size_kb": metadata.get("size"), "stars": metadata.get("stargazers_count", 0), "pushed_at": metadata.get("pushed_at"), "archived": bool(metadata.get("archived")), "disabled": bool(metadata.get("disabled")),
              "readme": readme, "inspected_commit": sha, "inspected_files": [item["path"] for item in files], "warnings": warnings}
    return {"source": "live_inspection", "quality": "source_inspected", "retrieved_at": now_iso(), "repository": name, "commit": sha,
            "root_entries": [{"path": item.get("path"), "type": item.get("type")} for item in entries if isinstance(item, dict)], "directories": directories, "files": files, "candidates": [record], "warnings": warnings,
            "verification_note": "Source and documentation were read, not executed. Feature claims, dependency compatibility, and runtime behavior still require task-specific checks."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", help="Exact owner/name from observed search results")
    parser.add_argument("--file", "--path", dest="paths", action="append", default=[], help="Relevant source/test file or directory to list; repeat as needed")
    parser.add_argument("--ref", help="Branch, tag, or commit; resolved to a commit before reading files")
    parser.add_argument("--find", action="append", default=[], help="Literal term to locate in files; returns bounded passages with line permalinks")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = inspect_repository(args.repository, args.paths, args.ref, args.find)
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"inspect_repo: {exc}", file=sys.stderr)
        return 1
    text = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
