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
DEFAULT_CACHE = Path.cwd() / ".cache" / "find-before-build" / "github"
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


def cached_result(saved: dict, queries: list[str], warning: str = "") -> dict:
    return {**saved, "source": "cache", "quality": "cached_unverified", "requested_queries": queries,
            "cache_used_at": now_iso(), "warning": warning or "Offline evidence; repository state has not been rechecked."}


def valid_cache(value) -> bool:
    return isinstance(value, dict) and isinstance(value.get("candidates"), list) and isinstance(value.get("queries"), list) and isinstance(value.get("retrieved_at"), str)


def query_terms(query: str) -> set[str]:
    # Qualifiers and language names must not make unrelated caches appear related.
    text = re.sub(r'\b[\w-]+:(?:"[^"]+"|\S+)', "", query)
    return {term for term in re.findall(r"[a-z0-9]+", text.lower()) if term not in SEARCH_FILLER | {"python", "typescript", "java", "rust", "golang", "language", "archived", "false", "true"} and len(term) > 2}


def related_cached_result(cache_dir: Path, queries: list[str], max_results: int, failure: str) -> dict | None:
    """Use one recent, strongly related cached search when a fresh search fails."""
    wanted = query_terms(" ".join(queries))
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
            have = query_terms(" ".join(cached_queries))
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


def search(queries: list[str], max_results: int = 10, cache_dir: Path = DEFAULT_CACHE, offline: bool = False, enrich: bool = True) -> dict:
    if not queries or len(queries) > 20 or any(not isinstance(query, str) or not query.strip() or len(query) > 256 for query in queries):
        raise ValueError("Provide one to twenty nonempty queries, each at most 256 characters")
    if not 1 <= max_results <= 100:
        raise ValueError("max_results must be between 1 and 100")
    queries = list(dict.fromkeys(" ".join(query.split()) for query in queries))
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{cache_key(queries, max_results)}.json"
    cached = None
    if cache_path.exists():
        try:
            value = json.loads(cache_path.read_text(encoding="utf-8"))
            cached = value if valid_cache(value) else None
        except (OSError, json.JSONDecodeError):
            pass
    if offline:
        if cached is None:
            raise RuntimeError("No cached GitHub result for these queries")
        return cached_result(cached, queries)

    token = os.getenv("GITHUB_TOKEN")
    try:
        per_query: list[list[dict]] = []
        query_runs = []
        warnings = []
        for query in queries:
            executed = query if re.search(r"\barchived:", query, re.I) else query + " archived:false"
            # Keep GitHub's best-match ordering. Do not sort discovery by stars.
            params = urlencode({"q": executed, "per_page": max_results})
            try:
                payload = fetch_json(f"/search/repositories?{params}", token)
                if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                    raise RuntimeError("Unexpected GitHub search response")
                items = [item for item in payload["items"] if isinstance(item, dict) and isinstance(item.get("full_name"), str)]
                per_query.append(items)
                query_runs.append({"query": query, "executed_query": executed, "status": "ok", "returned": len(items), "total_count": payload.get("total_count"), "incomplete_results": bool(payload.get("incomplete_results"))})
                if payload.get("incomplete_results"):
                    warnings.append(f"GitHub returned incomplete results for {query!r}; this search is not exhaustive")
            except RuntimeError as exc:
                per_query.append([])
                query_runs.append({"query": query, "executed_query": executed, "status": "error", "error": str(exc)[:300]})
                warnings.append(str(exc)[:300])
        if not any(run["status"] == "ok" for run in query_runs):
            raise RuntimeError("All GitHub queries failed: " + "; ".join(warnings)[:500])
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
                "language": item.get("language"),
                "topics": item.get("topics") or [],
                "default_branch": item.get("default_branch"),
                "fork": bool(item.get("fork")),
                "disabled": bool(item.get("disabled")),
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
            if not enrich:
                record["warnings"].append("README and issue history not requested; inspect promising candidates next")
            elif enrichment_limited:
                record["warnings"].append("README and issue checks skipped after GitHub API rate limit")
            else:
                try:
                    record["readme"] = fetch_readme(name, token)
                except RuntimeError as exc:
                    record["warnings"].append(f"README unavailable: {exc}")
                    if "HTTP 403" in str(exc) or "HTTP 429" in str(exc) or "rate limit" in str(exc).lower():
                        enrichment_limited = True
                if not enrichment_limited:
                    try:
                        record.update(fetch_issue_health(name, token))
                    except RuntimeError as exc:
                        record["warnings"].append(f"Issue sample unavailable: {exc}")
                        if "HTTP 403" in str(exc) or "HTTP 429" in str(exc) or "rate limit" in str(exc).lower():
                            enrichment_limited = True
            candidates.append(record)
        enriched = [bool(candidate["readme"]) and candidate["sampled_issues"] is not None for candidate in candidates]
        quality = "enriched" if enriched and all(enriched) else "partial" if any(enriched) else "metadata_only"
        result = {"queries": queries, "query_runs": query_runs, "retrieved_at": now_iso(), "source": "live", "quality": quality,
                  "coverage": "partial" if warnings else "complete_for_requested_queries", "warnings": warnings, "candidates": candidates,
                  "next_steps": [f"Try synonyms, fewer product terms, or an in:readme search for {run['query']!r}" for run in query_runs if run["status"] == "ok" and not run["returned"]]}
        cache_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        return result
    except RuntimeError as exc:
        if cached is not None:
            return cached_result(cached, queries, str(exc))
        related = related_cached_result(cache_dir, queries, max_results, str(exc))
        if related is not None:
            return related
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", action="append", required=True, help="GitHub repository query; repeat for product, component, and alternative searches")
    parser.add_argument("--max-results", type=int, default=10, help="Candidate count per batch, 1–100; run additional batches as needed")
    parser.add_argument("--metadata-only", action="store_true", help="Discover broadly first; inspect shortlisted repositories separately")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = search(args.query, args.max_results, args.cache_dir, args.offline, enrich=not args.metadata_only)
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
