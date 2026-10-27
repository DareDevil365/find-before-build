"""Evidence and ranking tests; all GitHub responses are local mocks."""

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "find-before-build" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


score_repo = load_script("score_repo")
search_repos = load_script("search_repos")


class ScoringTests(unittest.TestCase):
    NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)

    def test_language_inference_does_not_confuse_the_verb_go(self):
        self.assertIsNone(score_repo.requested_language("Go through repositories and find a PDF library"))
        self.assertEqual(score_repo.requested_language("Write a PDF tool in Go"), "go")

    def test_fit_and_runtime_outrank_popularity_and_resource_indexes(self):
        common = {"license": "MIT", "pushed_at": "2026-10-01T00:00:00Z", "size_kb": 500, "recent_closed_issues": 3, "readme": "Usage: install the package"}
        specialist = {**common, "full_name": "demo/pdf-bookmarks", "description": "PDF merge and bookmark preservation", "language": "Python", "stars": 5}
        wrong_runtime = {**specialist, "full_name": "demo/pdf-java", "language": "Java", "stars": 100000}
        index = {**specialist, "full_name": "demo/awesome-pdf", "description": "Curated list of PDF merge and bookmark tools", "stars": 100000}
        unrelated = {**common, "full_name": "demo/weather", "description": "Weather forecasting", "language": "Python", "stars": 100000}
        ranked = score_repo.score_all({"candidates": [unrelated, wrong_runtime, index, specialist]}, "PDF merge bookmark", language="Python")["ranked"]
        self.assertEqual(ranked[0]["full_name"], specialist["full_name"])
        self.assertLessEqual(next(item for item in ranked if item["full_name"] == unrelated["full_name"])["score"], 25)
        self.assertEqual(next(item for item in ranked if item["full_name"] == index["full_name"])["role"], "discovery_index")
        self.assertTrue(all("bm25" in item["features"] for item in ranked))

    def test_component_fit_and_evidence_updates_are_explicit(self):
        repo = {"full_name": "demo/math-parser", "description": "Math expression parser", "license": "MIT"}
        result = score_repo.score_one(repo, "Build a scientific calculator", self.NOW, capabilities=["math expression parser"])
        self.assertEqual(result["match_kind"], "component_match")
        self.assertEqual(result["capability_matches"][0]["coverage"], 1)
        merged = score_repo.merge_evidence([{"candidates": [{**repo, "matched_queries": ["math parser"]}]}, {"candidates": [{"full_name": "DEMO/math-parser", "license": None, "matched_queries": ["expression evaluator"]}]}])
        self.assertEqual(len(merged["candidates"]), 1)
        self.assertIsNone(merged["candidates"][0]["license"])
        self.assertEqual(len(merged["candidates"][0]["matched_queries"]), 2)

    def test_transparent_signal_weights_and_cautions(self):
        repo = {
            "full_name": "example/cleaner", "url": "https://github.com/example/cleaner",
            "description": "CSV cleaner", "readme": "Normalize CSV files",
            "license": "MIT", "pushed_at": "2026-10-01T00:00:00Z",
            "recent_closed_issues": 3, "open_issues": 1, "stars": 100,
        }
        result = score_repo.score_one(repo, "Build a CSV cleaner", self.NOW)
        self.assertEqual(result["signals"]["license"]["points"], 15)
        self.assertEqual(result["signals"]["activity"]["points"], 10)
        self.assertEqual(result["signals"]["maintenance"]["points"], 5)
        self.assertTrue(0 <= result["score"] <= 100)
        self.assertEqual(result["flags"], [])

    def test_unknown_license_and_history_are_flagged_not_assumed_good(self):
        result = score_repo.score_one({"full_name": "x/y", "stars": 0}, "CSV cleaner", self.NOW)
        self.assertEqual(result["signals"]["license"]["points"], 0)
        self.assertEqual(result["signals"]["maintenance"]["points"], 0)
        self.assertTrue(any("license" in flag for flag in result["flags"]))
        self.assertTrue(any("maintenance" in flag for flag in result["flags"]))

    def test_ranked_by_explainable_score(self):
        strong = {"full_name": "x/strong", "license": "MIT", "pushed_at": "2026-10-01T00:00:00Z", "readme": "CSV cleaner", "recent_closed_issues": 3, "stars": 100}
        weak = {"full_name": "x/weak", "license": None, "pushed_at": "2020-01-01T00:00:00Z", "readme": "", "recent_closed_issues": None, "stars": 0}
        ranked = score_repo.score_all({"candidates": [weak, strong]}, "CSV cleaner")["ranked"]
        self.assertEqual([item["full_name"] for item in ranked], ["x/strong", "x/weak"])


class SearchTests(unittest.TestCase):
    def test_one_failed_query_preserves_success_and_marks_incomplete_coverage(self):
        def fetch(path, token, timeout=12):
            if "broken" in path:
                raise RuntimeError("GitHub HTTP 422: invalid qualifier")
            return {"items": [{"full_name": "demo/usable", "language": "Python", "topics": ["pdf"]}], "total_count": 40, "incomplete_results": True}
        with tempfile.TemporaryDirectory() as directory, patch.object(search_repos, "fetch_json", side_effect=fetch):
            result = search_repos.search(["broken", "pdf"], 10, Path(directory), enrich=False)
        self.assertEqual(result["candidates"][0]["full_name"], "demo/usable")
        self.assertEqual(result["coverage"], "partial")
        self.assertEqual([run["status"] for run in result["query_runs"]], ["error", "ok"])
        self.assertIn("archived:false", result["query_runs"][1]["executed_query"])
        self.assertEqual(result["query_runs"][1]["total_count"], 40)

    def test_qualifiers_do_not_create_false_related_cache_overlap(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            saved = {"queries": ["weather forecasting language:Python archived:false"], "retrieved_at": search_repos.now_iso(), "candidates": [{"full_name": "demo/weather"}]}
            (cache / "weather.json").write_text(json.dumps(saved))
            self.assertIsNone(search_repos.related_cached_result(cache, ["pdf bookmarks language:Python archived:false"], 10, "failed"))

    def test_rate_limit_uses_only_recent_related_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp)
            saved = {"queries": ["javascript web calculator", "math expression parser javascript"], "retrieved_at": search_repos.now_iso(), "source": "live", "candidates": [{"full_name": "demo/expression-parser", "license": "MIT"}]}
            (cache / "prior.json").write_text(json.dumps(saved), encoding="utf-8")
            with patch.object(search_repos, "fetch_json", side_effect=RuntimeError("GitHub HTTP 403: rate limit exceeded")):
                result = search_repos.search(["calculator interface javascript", "math expression parser"], 9, cache)
                self.assertEqual(result["source"], "related_cache")
                self.assertEqual(result["quality"], "cached_unverified")
                self.assertEqual(result["candidates"][0]["full_name"], "demo/expression-parser")
                with self.assertRaisesRegex(RuntimeError, "rate limit"):
                    search_repos.search(["PDF merge", "bookmark writer"], 9, cache)

    def test_queries_both_contribute_and_offline_reuses_timestamped_cache(self):
        calls = []

        def fake_fetch(path, token, timeout=12):
            calls.append(path)
            if "/search/repositories?" in path:
                first = "first" if "first" in path else "second"
                return {"items": [{"full_name": f"demo/{first}", "html_url": f"https://github.com/demo/{first}", "stargazers_count": 4, "license": {"spdx_id": "MIT"}}]}
            if path.endswith("/readme"):
                return {"encoding": "base64", "content": "UkVBRE1F"}
            if "/issues?" in path:
                return []
            raise AssertionError(path)

        with tempfile.TemporaryDirectory() as temp, patch.object(search_repos, "fetch_json", side_effect=fake_fetch):
            cache = Path(temp)
            live = search_repos.search(["first", "second"], 2, cache)
            self.assertEqual({candidate["full_name"] for candidate in live["candidates"]}, {"demo/first", "demo/second"})
            self.assertEqual({candidate["full_name"]: candidate["matched_queries"] for candidate in live["candidates"]}, {"demo/first": ["first"], "demo/second": ["second"]})
            self.assertEqual(live["source"], "live")
            self.assertTrue(live["retrieved_at"])
            self.assertTrue(all(candidate["readme"] == "README" for candidate in live["candidates"]))
            before = len(calls)
            offline = search_repos.search(["first", "second"], 2, cache, offline=True)
            self.assertEqual(offline["source"], "cache")
            self.assertEqual(offline["retrieved_at"], live["retrieved_at"])
            self.assertEqual(len(calls), before)

    def test_no_cache_offline_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(RuntimeError, "No cached"):
                search_repos.search(["csv"], cache_dir=Path(temp), offline=True)

    def test_empty_query_does_not_hide_other_query_results(self):
        def fake_fetch(path, token, timeout=12):
            if "/search/repositories?" in path:
                return {"items": []} if "empty" in path else {"items": [{"full_name": "demo/match", "license": {"spdx_id": "MIT"}}]}
            if path.endswith("/readme"):
                return {"encoding": "base64", "content": "UkVBRE1F"}
            return []

        with tempfile.TemporaryDirectory() as temp, patch.object(search_repos, "fetch_json", side_effect=fake_fetch):
            result = search_repos.search(["empty", "match"], 2, Path(temp))
        self.assertEqual([item["full_name"] for item in result["candidates"]], ["demo/match"])


if __name__ == "__main__":
    unittest.main()
