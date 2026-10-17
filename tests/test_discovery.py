"""Evidence and ranking tests; all GitHub responses are local mocks."""

from datetime import datetime, timezone
import importlib.util
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

    def test_transparent_signal_weights_and_cautions(self):
        repo = {
            "full_name": "example/cleaner", "url": "https://github.com/example/cleaner",
            "description": "CSV cleaner", "readme": "Normalize CSV files",
            "license": "MIT", "pushed_at": "2026-10-01T00:00:00Z",
            "recent_closed_issues": 3, "open_issues": 1, "stars": 100,
        }
        result = score_repo.score_one(repo, "Build a CSV cleaner", self.NOW)
        self.assertEqual(result["signals"]["license"]["points"], 25)
        self.assertEqual(result["signals"]["activity"]["points"], 25)
        self.assertEqual(result["signals"]["maintenance"]["points"], 15)
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
