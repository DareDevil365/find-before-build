"""Behavioral checks for portable search fusion and criterion evidence gates."""

import copy
import importlib.util
from pathlib import Path
import unittest


path = Path(__file__).resolve().parents[1] / "search-with-evidence" / "scripts" / "rank_results.py"
spec = importlib.util.spec_from_file_location("rank_results", path)
ranking = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ranking)


def observed(status, url="https://example.org/item"):
    return {"status": status, "evidence": [{"url": url, "locator": "Relevant section", "note": "Synthetic inspected observation"}]}


def stream(query, results, family="core", provider="web"):
    return {"provider": provider, "family": family, "query": query, "results": results}


class SearchSkillTests(unittest.TestCase):
    def test_url_normalization_preserves_functional_identity(self):
        self.assertEqual(ranking.canonical_url("https://EXAMPLE.org:443/course?id=42&utm_source=x#fees"), "https://example.org/course?id=42")
        self.assertNotEqual(ranking.canonical_url("https://example.org/course?id=42"), ranking.canonical_url("https://example.org/course?id=43"))
        self.assertNotEqual(ranking.canonical_url("https://example.org/Paper"), ranking.canonical_url("https://example.org/paper"))
        with self.assertRaises(ValueError):
            ranking.canonical_url("https://user:secret@example.org/")

    def test_near_duplicate_queries_cannot_buy_more_votes(self):
        item = {"url": "https://example.org/a", "title": "Course"}
        data = {"streams": [stream("beginner course", [item])]}
        before = ranking.rank_results(data)["ranked"][0]["rrf"]
        data["streams"] += [stream("beginner class", [item]), stream("beginner course", [item]), stream("entry-level teaching", [item])]
        after = ranking.rank_results(data)
        self.assertEqual(after["ranked"][0]["rrf"], before)
        self.assertEqual(len(after["ranked"]), 1)
        self.assertTrue(after["warnings"])
        data["streams"].append(stream("practice assignments", [item], family="practice"))
        self.assertGreater(ranking.rank_results(data)["ranked"][0]["rrf"], before)

    def test_evidence_gates_override_search_rank_and_leave_unknown_provisional(self):
        data = {"criteria": [{"id": "beginner", "required": True, "weight": 3}], "streams": [stream("course", [
            {"url": "https://example.org/advanced", "assessments": {"beginner": observed("contradicted")}},
            {"url": "https://example.org/unknown", "assessments": {"beginner": {"status": "supported"}}},
            {"url": "https://example.org/beginner", "assessments": {"beginner": observed("supported")}},
        ])]}
        result = ranking.rank_results(data)
        self.assertEqual([item["eligibility"] for item in result["ranked"]], ["eligible", "provisional", "rejected"])
        self.assertEqual(result["ranked"][0]["score"], 100)
        self.assertEqual(result["ranked"][1]["unverified_support"], ["beginner"])
        self.assertNotIn("https://example.org/advanced", [item["url"] for item in result["inspection_queue"]])

    def test_conflicts_and_duplicate_observations_remain_visible(self):
        positive = {"url": "https://example.org/course", "assessments": {"free": observed("supported")}}
        negative = copy.deepcopy(positive)
        negative["assessments"]["free"] = observed("contradicted")
        data = {"criteria": [{"id": "free", "required": True}], "streams": [stream("free course", [positive]), stream("course cost", [negative], family="cost")]}
        item = ranking.rank_results(data)["ranked"][0]
        self.assertEqual(item["criteria"]["free"]["status"], "conflicted")
        self.assertEqual(item["eligibility"], "provisional")
        self.assertEqual(len(item["observations"]), 2)

    def test_mmr_prefers_a_complementary_inspection(self):
        data = {"streams": [stream("search", [
            {"url": "https://example.org/a", "title": "Beginner python course", "snippet": "Beginner python course"},
            {"url": "https://example.org/b", "title": "Beginner python course", "snippet": "Beginner python course"},
            {"url": "https://example.org/c", "title": "Practical exercises and projects"},
        ])]}
        queue = ranking.rank_results(data, top=2, diversity=0.5)["inspection_queue"]
        self.assertEqual([item["url"] for item in queue], ["https://example.org/a", "https://example.org/c"])

    def test_invalid_scores_and_unknown_criteria_are_rejected(self):
        with self.assertRaises(ValueError):
            ranking.rank_results({"criteria": [{"id": "a", "weight": float("nan")}], "streams": []})
        with self.assertRaises(ValueError):
            ranking.rank_results({"streams": [stream("query", [{"url": "https://example.org/a", "assessments": {"made-up": {"status": "supported"}}}])]})


if __name__ == "__main__":
    unittest.main()
