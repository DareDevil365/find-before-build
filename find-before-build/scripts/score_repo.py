"""Score GitHub repository evidence and show every scoring reason."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys

PERMISSIVE = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "Unlicense", "0BSD"}
COPYLEFT = {"GPL-2.0", "GPL-3.0", "AGPL-3.0", "LGPL-2.1", "LGPL-3.0", "MPL-2.0"}
STOP = {"build", "create", "make", "simple", "basic", "with", "using", "that", "this", "from", "into", "for", "the", "and", "a", "an", "app", "tool", "web"}


def words(value: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]{3,}", value.lower()) if word not in STOP}


def score_one(repo: dict, task: str, at: datetime | None = None) -> dict:
    at = at or datetime.now(timezone.utc)
    signals: dict[str, dict] = {}
    flags: list[str] = []
    license_id = repo.get("license") or "UNKNOWN"
    license_score = 25 if license_id in PERMISSIVE else 0
    if license_id in COPYLEFT:
        flags.append(f"copyleft license: {license_id}; review obligations")
    elif license_id not in PERMISSIVE:
        flags.append(f"license not verified as permissive: {license_id}")
    signals["license"] = {"points": license_score, "max": 25, "reason": license_id}

    pushed = repo.get("pushed_at")
    days = None
    if pushed:
        try:
            days = max(0, (at - datetime.fromisoformat(pushed.replace("Z", "+00:00"))).days)
        except ValueError:
            pass
    recency = 25 if days is not None and days <= 90 else 18 if days is not None and days <= 180 else 10 if days is not None and days <= 365 else 0
    signals["activity"] = {"points": recency, "max": 25, "reason": f"last push {days} days ago" if days is not None else "last push unknown"}
    if days is None or days > 365 or repo.get("archived"):
        flags.append("stale, archived, or activity unknown")

    task_words = words(task)
    readme_words = words((repo.get("full_name") or "").replace("-", " ").replace("_", " ") + " " + (repo.get("description") or "") + " " + (repo.get("readme") or ""))
    matched = sorted(task_words & readme_words)
    overlap = len(matched) / len(task_words) if task_words else 0
    fit = round(20 * overlap)
    signals["readme_fit"] = {"points": fit, "max": 20, "reason": f"matched {matched}; README text is a claim, not a verified feature"}
    if not repo.get("readme"):
        flags.append("README unavailable")

    closed = repo.get("recent_closed_issues")
    open_count = repo.get("open_issues")
    if closed is None:
        health, reason = 0, "issue history unavailable"
        flags.append("maintenance health unknown")
    elif closed >= 3:
        health, reason = 15, f"{closed} sampled issues closed within 90 days"
    elif closed >= 1:
        health, reason = 10, f"{closed} sampled issue closed within 90 days"
    elif open_count == 0:
        health, reason = 8, "no open issues; activity cannot be inferred"
    else:
        health, reason = 0, f"no recent closed issues in sample; {open_count} open issues/PRs"
    signals["maintenance"] = {"points": health, "max": 15, "reason": reason}

    stars = max(0, int(repo.get("stars") or 0))
    star_points = min(15, round(15 * math.log10(stars + 1) / 4))
    signals["stars"] = {"points": star_points, "max": 15, "reason": f"{stars} stars; logarithmic scale"}
    total = sum(s["points"] for s in signals.values())
    domain_words = task_words - {"scraper", "scraping", "crawler", "python", "compare", "comparison", "converter", "generator", "website", "application"}
    if domain_words and not domain_words.intersection(readme_words):
        flags.append("no task-domain overlap; consider only as a generic component")
        total = min(total, 50)
    return {"full_name": repo.get("full_name"), "url": repo.get("url"), "score": total, "signals": signals, "flags": flags, "description": repo.get("description", ""), "size_kb": repo.get("size_kb"), "matched_queries": repo.get("matched_queries", [])}


def score_all(data: dict, task: str) -> dict:
    ranked = [score_one(repo, task) for repo in data.get("candidates", [])]
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return {"task": task, "source": data.get("source"), "retrieved_at": data.get("retrieved_at"), "ranked": ranked}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        result = score_all(data, args.task)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"score_repo: {exc}", file=sys.stderr)
        return 1
    rendered = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    for item in result["ranked"]:
        reasons = "; ".join(f"{key}={v['points']}/{v['max']} ({v['reason']})" for key, v in item["signals"].items())
        print(f"{item['full_name']}: {item['score']}/100 | {reasons} | flags: {', '.join(item['flags']) or 'none'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
