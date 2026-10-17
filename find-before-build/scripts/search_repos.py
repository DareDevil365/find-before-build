"""Fetch bounded, timestamped GitHub repository evidence without running code."""

from __future__ import annotations

import argparse
import base64
import hashlib
from itertools import zip_longest
import json
import os
from pathlib import Path
import re
import sys
from datetime import datetime, timezone, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

API = "https://api.github.com"
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE = ROOT / ".cache" / "github"
SEARCH_FILLER = {"a", "an", "and", "app", "build", "code", "component", "for", "github", "in", "js", "javascript", "library", "project", "repo", "simple", "the", "tool", "user", "web", "with"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_json(path: str, token: str | None, timeout: float = 12) -> dict | list:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "find-before-build/0.1",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(API + path, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as exc:
        detail = exc.read(300).decode("utf-8", "replace")
        raise RuntimeError(f"GitHub HTTP {exc.code} for {path}: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"GitHub request failed for {path}: {exc}") from exc


def cache_key(queries: list[str], max_results: int) -> str:
    raw = json.dumps([queries, max_results, 1], sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()[:20]


def related_cached_result(cache_dir: Path, queries: list[str], max_results: int, failure: str) -> dict | None:
    """Use one recent, strongly related cached search when a fresh search fails."""
    wanted = {term for term in re.findall(r"[a-z0-9]+", " ".join(queries).lower()) if term not in SEARCH_FILLER and len(term) > 2}
    if len(wanted) < 2:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    best: tuple[float, dict] | None = None
    for path in cache_dir.glob("*.json"):
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            retrieved = datetime.fromisoformat(saved["retrieved_at"].replace("Z", "+00:00"))
            cached_queries = saved["queries"]
            candidates = saved["candidates"]
            if retrieved < cutoff or not isinstance(cached_queries, list) or not isinstance(candidates, list) or not candidates:
                continue
            have = {term for term in re.findall(r"[a-z0-9]+", " ".join(cached_queries).lower()) if term not in SEARCH_FILLER and len(term) > 2}
            overlap = wanted & have
            if len(overlap) < 2 or len(overlap) / len(wanted) < 0.4:
                continue
            rank = len(overlap) / len(wanted) + len(overlap) / max(len(have), 1) + retrieved.timestamp() / 1e12
            if best is None or rank > best[0]:
                best = (rank, saved)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
    if best is None:
        return None
    saved = best[1]
    return {**saved, "candidates": saved["candidates"][:max_results], "source": "related_cache", "quality": "cached_unverified", "requested_queries": queries, "cache_used_at": now_iso(), "warning": "Fresh GitHub search failed; using recent related cached evidence. New queries were not verified. " + failure[:180]}


def fetch_readme(full_name: str, token: str | None) -> str:
    payload = fetch_json(f"/repos/{full_name}/readme", token)
    if not isinstance(payload, dict):
        return ""
    if payload.get("encoding") == "base64":
        try:
            return base64.b64decode(payload.get("content", "")).decode("utf-8", "replace")[:12000]
        except ValueError:
            return ""
    return ""


def fetch_issue_health(full_name: str, token: str | None) -> dict:
    issues = fetch_json(f"/repos/{full_name}/issues?state=all&sort=updated&per_page=30", token)
    if not isinstance(issues, list):
        return {"recent_closed_issues": None, "sampled_issues": None}
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    actual = [item for item in issues if isinstance(item, dict) and "pull_request" not in item]
    closed = 0
    for item in actual:
        when = item.get("closed_at")
        if when:
            try:
                if datetime.fromisoformat(when.replace("Z", "+00:00")) >= cutoff:
                    closed += 1
            except ValueError:
                pass
    return {"recent_closed_issues": closed, "sampled_issues": len(actual)}


def search(queries: list[str], max_results: int = 5, cache_dir: Path = DEFAULT_CACHE, offline: bool = False) -> dict:
    if not queries or len(queries) > 3:
        raise ValueError("Provide one to three queries")
    if not 1 <= max_results <= 10:
        raise ValueError("max_results must be between 1 and 10")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{cache_key(queries, max_results)}.json"
    cached = None
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    if offline:
        if cached is None:
            raise RuntimeError("No cached GitHub result for these queries")
        return {**cached, "source": "cache", "cache_used_at": now_iso()}

    token = os.getenv("GITHUB_TOKEN")
    try:
        per_query: list[list[dict]] = []
        for query in queries:
            params = urlencode({"q": query, "per_page": max_results})
            payload = fetch_json(f"/search/repositories?{params}", token)
            if not isinstance(payload, dict):
                raise RuntimeError("Unexpected GitHub search response")
            per_query.append(payload.get("items", []))
        found: dict[str, dict] = {}
        # Interleave results so every query can contribute to a bounded sample.
        for row in zip_longest(*per_query):
            for item in row:
                if item is None:
                    continue
                full_name = item.get("full_name")
                if full_name and full_name not in found:
                    found[full_name] = item
                if len(found) >= max_results:
                    break
            if len(found) >= max_results:
                break
        candidates = []
        enrichment_limited = False
        for item in list(found.values())[:max_results]:
            name = item["full_name"]
            record = {
                "full_name": name,
                "matched_queries": [query for query, matches in zip(queries, per_query) if any(match.get("full_name") == name for match in matches)],
                "url": item.get("html_url", f"https://github.com/{name}"),
                "description": item.get("description") or "",
                "stars": item.get("stargazers_count", 0),
                "open_issues": item.get("open_issues_count"),
                "pushed_at": item.get("pushed_at"),
                "license": (item.get("license") or {}).get("spdx_id"),
                "archived": bool(item.get("archived")),
                "size_kb": item.get("size"),
                "readme": "",
                "recent_closed_issues": None,
                "sampled_issues": None,
                "warnings": [],
            }
            if enrichment_limited:
                record["warnings"].append("README and issue checks skipped after GitHub API rate limit")
            else:
                try:
                    record["readme"] = fetch_readme(name, token)
                except RuntimeError as exc:
                    record["warnings"].append(f"README unavailable: {exc}")
                    if "HTTP 403" in str(exc) or "rate limit" in str(exc).lower():
                        enrichment_limited = True
                if not enrichment_limited:
                    try:
                        record.update(fetch_issue_health(name, token))
                    except RuntimeError as exc:
                        record["warnings"].append(f"Issue sample unavailable: {exc}")
                        if "HTTP 403" in str(exc) or "rate limit" in str(exc).lower():
                            enrichment_limited = True
            candidates.append(record)
        result = {"queries": queries, "retrieved_at": now_iso(), "source": "live", "quality": "metadata_only" if enrichment_limited else "enriched", "candidates": candidates}
        cache_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        return result
    except RuntimeError as exc:
        if cached is not None:
            return {**cached, "source": "cache", "cache_used_at": now_iso(), "warning": str(exc)}
        related = related_cached_result(cache_dir, queries, max_results, str(exc))
        if related is not None:
            return related
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", action="append", required=True, help="Search query; repeat up to three times")
    parser.add_argument("--max-results", type=int, default=5)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = search(args.query, args.max_results, args.cache_dir, args.offline)
    except (RuntimeError, ValueError) as exc:
        print(f"search_repos: {exc}", file=sys.stderr)
        return 1
    rendered = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    print(f"{len(result['candidates'])} candidates from {result['source']} evidence ({result['retrieved_at']})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
