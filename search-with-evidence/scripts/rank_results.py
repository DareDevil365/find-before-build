"""Fuse observed search lists, audit criterion evidence, and diversify inspection.

This local helper makes no network requests and has no model/API dependency.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def canonical_url(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Result URL must be a string")
    parsed = urlsplit(value.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Results require an observed HTTP(S) URL without credentials")
    host = parsed.hostname.lower()
    if ":" in host:
        host = "[" + host + "]"
    if parsed.port and (parsed.scheme.lower(), parsed.port) not in {("http", 80), ("https", 443)}:
        host += ":" + str(parsed.port)
    query = [(key, item) for key, item in parse_qsl(parsed.query, keep_blank_values=True) if not key.lower().startswith("utm_") and key.lower() not in {"gclid", "fbclid"}]
    return urlunsplit((parsed.scheme.lower(), host, parsed.path or "/", urlencode(sorted(query)), ""))


def terms(text: str) -> set[str]:
    return set(re.findall(r"\w{3,}", text.lower()))


def finite_number(value, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be a finite number from {minimum} to {maximum}")
    return float(value)


def has_evidence(judgment: dict) -> bool:
    evidence = judgment.get("evidence") or []
    if not isinstance(evidence, list):
        raise ValueError("Criterion evidence must be a list")
    for item in evidence:
        if not isinstance(item, dict):
            continue
        if not isinstance(item.get("locator"), str) or not item["locator"].strip():
            continue
        if not isinstance(item.get("note"), str) or not item["note"].strip():
            continue
        try:
            canonical_url(item.get("url", ""))
            return True
        except (ValueError, TypeError, AttributeError):
            continue
    return False


def rank_results(data: dict, *, top: int = 5, diversity: float = 0.75) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("streams"), list):
        raise ValueError("Input needs a streams list")
    if not isinstance(top, int) or isinstance(top, bool) or top < 1:
        raise ValueError("top must be positive")
    diversity = finite_number(diversity, "diversity", 0, 1)
    criteria = data.get("criteria", [])
    if not isinstance(criteria, list):
        raise ValueError("criteria must be a list")
    criterion_ids = set()
    for criterion in criteria:
        if not isinstance(criterion, dict) or not isinstance(criterion.get("id"), str) or not criterion["id"].strip() or criterion["id"] in criterion_ids:
            raise ValueError("Criteria require unique nonempty string IDs")
        criterion_ids.add(criterion["id"])
        finite_number(criterion.get("weight", 1), "criterion weight", 0.001, 1000)
        if not isinstance(criterion.get("required", False), bool):
            raise ValueError("required must be a boolean")
    candidates = {}
    seen_streams = set()
    warnings = []
    for stream in data["streams"]:
        if not isinstance(stream, dict) or not isinstance(stream.get("results"), list):
            raise ValueError("Every stream needs a results list")
        if not all(isinstance(stream.get(field), str) and stream[field].strip() for field in ("provider", "query", "family")):
            raise ValueError("Every stream needs provider, query, and query-family strings")
        signature = (stream["provider"].casefold(), " ".join(stream["query"].casefold().split()))
        weight = finite_number(stream.get("weight", 1), "stream weight", 0.001, 10)
        for position, item in enumerate(stream["results"], 1):
            if not isinstance(item, dict):
                raise ValueError("Result entries must be objects")
            url = canonical_url(item.get("url", ""))
            key = item.get("canonical_id") or url
            if not isinstance(key, str):
                raise ValueError("canonical_id must be a string")
            if key not in candidates:
                candidates[key] = {"id": key, "url": url, "title": str(item.get("title") or url), "snippet": str(item.get("snippet") or ""), "observations": [], "assessments": defaultdict(list), "family_scores": {}, "unverified_support": []}
            candidate = candidates[key]
            candidate["observations"].append({"provider": stream["provider"], "query": stream["query"], "family": stream["family"], "rank": position, "url": item["url"], "retrieved_at": stream.get("retrieved_at")})
            # Re-running a query cannot buy another vote. Similar rewrites in a
            # single family contribute only their strongest observation.
            if signature not in seen_streams:
                candidate["family_scores"][stream["family"]] = max(candidate["family_scores"].get(stream["family"], 0), weight / (60 + position))
            judgments = item.get("assessments", {})
            if not isinstance(judgments, dict):
                raise ValueError("assessments must map criterion IDs to judgments")
            for criterion_id, judgment in judgments.items():
                if criterion_id not in criterion_ids:
                    raise ValueError(f"Assessment refers to unknown criterion {criterion_id}")
                if not isinstance(judgment, dict) or judgment.get("status") not in {"supported", "claimed", "unknown", "contradicted"}:
                    raise ValueError("Judgment status must be supported, claimed, unknown, or contradicted")
                status = judgment["status"]
                if status in {"supported", "contradicted"} and not has_evidence(judgment):
                    status = "unknown"
                    candidate["unverified_support"].append(criterion_id)
                candidate["assessments"][criterion_id].append({**judgment, "status": status})
        if signature in seen_streams:
            warnings.append("Repeated provider/query ignored for fusion: " + stream["query"])
        seen_streams.add(signature)
    ranked = []
    total_weight = sum(criterion.get("weight", 1) for criterion in criteria)
    for candidate in candidates.values():
        states = {}
        contribution = 0
        missing = []
        failed = []
        for criterion in criteria:
            judgments = candidate["assessments"].get(criterion["id"], [])
            statuses = {judgment["status"] for judgment in judgments}
            state = "conflicted" if {"supported", "contradicted"} <= statuses else "contradicted" if "contradicted" in statuses else "supported" if "supported" in statuses else "claimed" if "claimed" in statuses else "unknown"
            states[criterion["id"]] = {"status": state, "weight": criterion.get("weight", 1), "required": criterion.get("required", False), "observations": judgments}
            contribution += criterion.get("weight", 1) * (1 if state == "supported" else 0.35 if state == "claimed" else 0)
            if criterion.get("required") and state == "contradicted":
                failed.append(criterion["id"])
            elif criterion.get("required") and state != "supported":
                missing.append(criterion["id"])
        eligibility = "rejected" if failed else "provisional" if missing or not criteria else "eligible"
        score = round(100 * contribution / total_weight, 2) if total_weight else None
        ranked.append({key: value for key, value in candidate.items() if key not in {"assessments", "family_scores"}} | {"score": score, "rrf": round(sum(candidate["family_scores"].values()), 8), "query_families": sorted(candidate["family_scores"]), "eligibility": eligibility, "failed_requirements": failed, "required_evidence_gaps": missing, "criteria": states})
    band = {"eligible": 0, "provisional": 1, "rejected": 2}
    ranked.sort(key=lambda item: (band[item["eligibility"]], -(item["score"] or 0), -item["rrf"], item["id"]))
    remaining = [item for item in ranked if item["eligibility"] != "rejected"]
    selected = []
    highest_rrf = max((item["rrf"] for item in remaining), default=1) or 1
    while remaining and len(selected) < top:
        def inspection_value(item):
            text = terms(item["title"] + " " + item["snippet"])
            similarity = max((len(text & prior["terms"]) / max(1, len(text | prior["terms"])) for prior in selected), default=0)
            retrieval = item["rrf"] / highest_rrf
            value = diversity * retrieval - (1 - diversity) * similarity
            return value
        chosen = max(remaining, key=inspection_value)
        selected.append({"id": chosen["id"], "url": chosen["url"], "priority": round(inspection_value(chosen), 6), "terms": terms(chosen["title"] + " " + chosen["snippet"])})
        remaining.remove(chosen)
    return {"goal": data.get("goal"), "interpretation": data.get("interpretation"), "ranked": ranked, "inspection_queue": [{key: value for key, value in item.items() if key != "terms"} for item in selected], "warnings": warnings,
            "method": "Query-family-capped RRF for discovery; evidence-weighted criteria for recommendations; lexical MMR for inspection diversity. These scores are not learned probabilities. Human/model evidence judgments are inputs, not independently verified by this helper."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--diversity", type=float, default=0.75)
    args = parser.parse_args()
    try:
        output = rank_results(json.loads(args.input.read_text(encoding="utf-8")), top=args.top, diversity=args.diversity)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(1, f"rank_results: {exc}\n")
    text = json.dumps(output, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
