"""Explainable candidate features and retrieval ranking; no trained model required."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys

PERMISSIVE = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "Unlicense", "0BSD"}
COPYLEFT = {"GPL-2.0", "GPL-3.0", "AGPL-3.0", "LGPL-2.1", "LGPL-3.0", "MPL-2.0"}
STOP = set("build create make simple basic with using that this from into for the and an to of in on by or as is it be at app tool web website application project program software implement include support user users file files input output local localhost readme module named example examples please need want should can will have has its are our your python javascript typescript golang java rust library framework code repository github cli browser runnable".split())
LANGUAGES = {"python": "python", "javascript": "javascript", "js": "javascript", "typescript": "typescript", "ts": "typescript", "rust": "rust", "golang": "go", "go": "go", "java": "java", "c++": "c++", "c#": "c#", "ruby": "ruby", "php": "php"}


def tokens(value: str) -> list[str]:
    value = re.sub(r"\b[\w-]+:(?:\"[^\"]+\"|\S+)", " ", value.lower())
    found = re.findall(r"[a-z0-9]{2,}", value.replace("_", " "))
    return [stem(word) for word in found if word not in STOP]


def stem(word: str) -> str:
    """Small lexical normalization, not a semantic model or a full stemmer."""
    if len(word) > 4 and word.endswith("s") and not word.endswith(("ss", "us")):
        word = word[:-1]
    for suffix, minimum in (("ation", 9), ("ing", 7), ("ed", 6), ("er", 6)):
        if len(word) >= minimum and word.endswith(suffix):
            word = word[:-len(suffix)]
            break
    if len(word) >= 5 and word.endswith("e"):
        word = word[:-1]
    return word


def words(value: str) -> set[str]:
    return set(tokens(value))


def metadata_text(repo: dict) -> str:
    return " ".join([str(repo.get("full_name") or "").split("/")[-1].replace("-", " "), str(repo.get("description") or ""), " ".join(repo.get("topics") or [])])


def requested_language(task: str) -> str | None:
    for name, language in LANGUAGES.items():
        if name == "go" and not re.search(r"\b(?:in|using|with|language:?|written in)\s+go\b", task, re.I):
            continue
        if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", task.lower()):
            return language
    return None


def coverage(terms: set[str], metadata: set[str], readme: set[str], idf: dict[str, float]) -> tuple[float, list[str]]:
    denominator = sum(idf.get(term, 1) for term in terms)
    value = sum(idf.get(term, 1) * (1 if term in metadata else 0.4 if term in readme else 0) for term in terms)
    return (value / denominator if denominator else 0), sorted(terms & (metadata | readme))


def score_one(repo: dict, task: str, at: datetime | None = None, *, capabilities: list[str] | None = None, language: str | None = None, idf: dict[str, float] | None = None) -> dict:
    at = at or datetime.now(timezone.utc)
    idf = idf or {}
    signals, flags = {}, []
    metadata = words(metadata_text(repo))
    readme = words(str(repo.get("readme") or "")[:12000])
    task_fit, matched = coverage(words(task), metadata, readme, idf)
    matches = []
    for capability in capabilities or []:
        fit, terms = coverage(words(capability), metadata, readme, idf)
        matches.append({"capability": capability, "coverage": round(fit, 3), "matched_terms": terms})
    best = max(matches, key=lambda item: item["coverage"], default={"coverage": 0, "capability": None})
    relevance = max(task_fit, best["coverage"] * 0.9)
    kind = "task_match" if task_fit >= 0.35 else "component_match" if best["coverage"] >= 0.5 else "weak_match"
    signals["relevance"] = {"points": round(45 * relevance), "max": 45, "reason": f"task coverage {task_fit:.2f}; best capability coverage {best['coverage']:.2f}; metadata counts more than README claims"}
    if kind == "weak_match":
        flags.append("weak task/capability evidence; not an implementation recommendation")
    if not repo.get("readme"):
        flags.append("README unavailable")

    license_id = repo.get("license") or "UNKNOWN"
    signals["license"] = {"points": 15 if license_id in PERMISSIVE else 0, "max": 15, "reason": license_id}
    if license_id in COPYLEFT or license_id.endswith(("-only", "-or-later")):
        flags.append(f"copyleft license: {license_id}; review obligations")
    elif license_id not in PERMISSIVE:
        flags.append(f"license not verified as permissive: {license_id}")

    wanted = language or requested_language(task)
    wanted = LANGUAGES.get(str(wanted).lower(), str(wanted).lower()) if wanted else None
    actual = str(repo.get("language") or "").lower()
    compatible = actual == wanted or {actual, wanted} <= {"javascript", "typescript"}
    runtime = 5 if not wanted else 10 if actual and compatible else 0
    if wanted and actual and not compatible:
        flags.append(f"runtime mismatch: requested {wanted}, repository uses {actual}; inspect integration boundary")
    size = repo.get("size_kb")
    size_points = 5 if isinstance(size, int) and 0 <= size <= 1000 else 4 if isinstance(size, int) and 0 <= size <= 10000 else 2 if isinstance(size, int) and 0 <= size <= 50000 else 0
    usage = bool(re.search(r"\b(?:pip install|npm install|cargo add|usage|getting started|quick start|quickstart)\b", str(repo.get("readme") or ""), re.I))
    signals["integration"] = {"points": runtime + size_points + (5 if usage else 0), "max": 20, "reason": f"requested runtime {wanted or 'unspecified'}; observed {actual or 'unknown'}; size {size if size is not None else 'unknown'} KB; usage instructions {'claimed' if usage else 'not observed'}"}

    days = None
    try:
        if repo.get("pushed_at"):
            days = max(0, (at - datetime.fromisoformat(repo["pushed_at"].replace("Z", "+00:00"))).days)
    except (ValueError, TypeError):
        pass
    activity = 10 if days is not None and days <= 90 else 7 if days is not None and days <= 180 else 4 if days is not None and days <= 365 else 0
    signals["activity"] = {"points": 0 if repo.get("archived") or repo.get("disabled") else activity, "max": 10, "reason": f"last push {days} days ago" if days is not None else "last push unknown"}
    if days is None or days > 365:
        flags.append("activity stale or unknown; assess stability rather than assuming abandonment")

    closed = repo.get("recent_closed_issues")
    health = 5 if isinstance(closed, int) and closed >= 3 else 3 if isinstance(closed, int) and closed >= 1 else 0
    signals["maintenance"] = {"points": health, "max": 5, "reason": f"{closed} recently closed issues in a bounded sample" if closed is not None else "issue history unavailable"}
    if closed is None:
        flags.append("maintenance health unknown")
    stars = max(0, int(repo.get("stars") or 0))
    signals["stars"] = {"points": min(5, round(5 * math.log10(stars + 1) / 4)), "max": 5, "reason": f"{stars} stars; weak adoption signal, not task-fit evidence"}

    title = str(repo.get("full_name") or "").split("/")[-1].lower()
    role = "discovery_index" if title.startswith("awesome-") or re.search(r"\bcurated (?:list|collection)\b", str(repo.get("description") or ""), re.I) else "learning_material" if re.search(r"(?:^|[-_])(?:tutorials?|course|exercises)(?:$|[-_])", title) else "implementation_candidate"
    total = sum(signal["points"] for signal in signals.values())
    caps = []
    if kind == "weak_match":
        caps.append((25, "weak_relevance"))
    if role == "discovery_index":
        flags.append("resource index: follow its links to implementations")
        caps.append((40, "discovery_index"))
    if role == "learning_material":
        flags.append("learning material: verify whether it supplies reusable implementation or requested content")
    if repo.get("archived"):
        flags.append("archived repository; investigate a maintained alternative")
        caps.append((40, "archived"))
    if repo.get("disabled"):
        flags.append("disabled repository")
        caps.append((10, "disabled"))
    for limit, _ in caps:
        total = min(total, limit)
    excerpts = [{"source": "metadata", "text": metadata_text(repo)[:300]}]
    relevant = words(task) | set().union(*(words(value) for value in capabilities or []))
    for number, line in enumerate(str(repo.get("readme") or "").splitlines(), 1):
        if relevant & words(line):
            excerpts.append({"source": "README claim", "line": number, "text": line[:250]})
        if len(excerpts) >= 4:
            break
    known = sum(bool(repo.get(key)) for key in ("license", "language", "pushed_at", "readme")) + int(closed is not None) + int(size is not None)
    return {"full_name": repo.get("full_name"), "url": repo.get("url"), "score": total, "score_version": "relevance-v2", "signals": signals, "score_caps": [reason for _, reason in caps], "flags": flags,
            "features": {"task_coverage": round(task_fit, 3), "best_capability_coverage": best["coverage"], "runtime_match": bool(wanted and actual and compatible), "evidence_fields_present": known, "evidence_fields_total": 6},
            "match_kind": kind, "role": role, "matched_terms": matched, "capability_matches": matches, "evidence": excerpts,
            "description": repo.get("description", ""), "language": repo.get("language"), "size_kb": size, "matched_queries": repo.get("matched_queries", [])}


def score_all(data: dict, task: str, *, capabilities: list[str] | None = None, language: str | None = None) -> dict:
    repos = data.get("candidates", [])
    docs = [tokens(metadata_text(repo) + " " + str(repo.get("readme") or "")[:12000]) for repo in repos]
    count = len(docs)
    df = Counter(term for document in docs for term in set(document))
    idf = {term: math.log(1 + (count - frequency + 0.5) / (frequency + 0.5)) for term, frequency in df.items()}
    average = sum(map(len, docs)) / count if count else 1
    query = words(task) | set().union(*(words(value) for value in capabilities or []))
    ranked = []
    for repo, document in zip(repos, docs):
        result = score_one(repo, task, capabilities=capabilities, language=language, idf=idf)
        frequencies = Counter(document)
        bm25 = sum(idf.get(term, 0) * frequencies[term] * 2.2 / (frequencies[term] + 1.2 * (0.25 + 0.75 * len(document) / max(average, 1))) for term in query if frequencies[term])
        result["features"]["bm25"] = round(bm25, 4)
        ranked.append(result)
    ranked.sort(key=lambda item: (-item["score"], -item["signals"]["relevance"]["points"], -item["features"]["bm25"], str(item["full_name"])))
    return {"task": task, "capabilities": capabilities or [], "preferred_language": language or requested_language(task), "source": data.get("source"), "quality": data.get("quality"), "retrieved_at": data.get("retrieved_at"), "score_version": "relevance-v2", "ranking_note": "Heuristic features and BM25 tie-breaking, not a trained model or verified feature assessment; inspect sources and rerank against requirements.", "ranked": ranked}


def merge_evidence(inputs: list[dict]) -> dict:
    """Later observations update duplicate repositories; never count them twice."""
    found = {}
    for data in inputs:
        for candidate in data.get("candidates", []):
            name = str(candidate.get("full_name") or "").lower()
            if not name:
                continue
            previous = found.get(name, {})
            queries = list(dict.fromkeys([*previous.get("matched_queries", []), *candidate.get("matched_queries", [])]))
            found[name] = {**previous, **candidate, "matched_queries": queries}
    return {"source": "combined_evidence", "quality": "mixed_observations", "retrieved_at": max((str(item.get("retrieved_at", "")) for item in inputs), default=""), "candidates": list(found.values())}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, action="append", required=True, help="Search or inspection evidence; repeat across investigation rounds, newest last")
    parser.add_argument("--task", required=True)
    parser.add_argument("--capability", action="append", default=[], help="One task-derived reusable capability; repeat for each component")
    parser.add_argument("--language", help="Required implementation runtime, when material")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        data = merge_evidence([json.loads(path.read_text(encoding="utf-8")) for path in args.input])
        result = score_all(data, args.task, capabilities=args.capability, language=args.language)
    except (OSError, ValueError, TypeError) as exc:
        print(f"score_repo: {exc}", file=sys.stderr)
        return 1
    rendered = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    for item in result["ranked"]:
        print(f"{item['full_name']}: {item['score']}/100 ({item['match_kind']}) | flags: {', '.join(item['flags']) or 'none'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
