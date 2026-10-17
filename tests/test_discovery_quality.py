"""Task-diverse regression checks for repository search and ranking quality."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import unittest

from harness import plan_search, queries_for
from harness_core.backends import Reply
from harness_core.clarify import fallback_questions
from score_repo import score_all


class DiscoveryQualityTests(unittest.TestCase):
    def test_fallback_queries_cover_diverse_products(self):
        cases = [
            ("Create a CSV cleaner CLI", "csv"),
            ("Build a PDF merger with bookmarks", "pdf"),
            ("Make a Markdown to slides converter", "markdown"),
            ("Compare Indian parcel courier prices with a scraper", "courier"),
        ]
        for task, expected in cases:
            with self.subTest(task=task):
                queries = queries_for(task)
                self.assertTrue(1 <= len(queries) <= 3)
                self.assertTrue(any(expected in query for query in queries))

    def test_model_plan_keeps_specific_component_and_adds_product(self):
        class Backend:
            def chat(self, messages):
                return Reply(text='{"capabilities":["courier rate API","HTML extraction"],"queries":["shiprocket courier api","web scraper python","shipping rate comparison"]}')

        plan, _ = plan_search(Backend(), "Compare Indian parcel courier prices with a scraper")
        self.assertIn("shiprocket courier api", plan["queries"])
        self.assertTrue(any("scrap" in query for query in plan["queries"]))
        self.assertTrue(any("parcel" in query for query in plan["queries"]))

    def test_model_plans_cover_whole_request_and_component_for_other_domains(self):
        cases = [
            ("Merge PDF documents with bookmarks", '["pypdf library","pdf metadata bookmarks"]', "pdf"),
            ("Convert Markdown files into slide decks", '["marp core","reveal js slides"]', "markdown"),
            ("Clean CSV column names and duplicate rows", '["pandas dataframe","csv parser library"]', "csv"),
        ]
        for task, query_json, domain in cases:
            with self.subTest(task=task):
                class Backend:
                    def chat(self, messages):
                        return Reply(text='{"capabilities":["input parsing","output generation"],"queries":' + query_json + "}")

                plan, _ = plan_search(Backend(), task)
                self.assertTrue(any(domain in query for query in plan["queries"]))
                self.assertTrue(any(query in plan["queries"] for query in json.loads(query_json)))

    def test_fallback_questions_are_domain_neutral(self):
        self.assertEqual(fallback_questions("Merge all PDFs"), fallback_questions("Compare all courier rates"))
        self.assertEqual(fallback_questions("Build a CSV cleaner"), ["What should the tool take as input, and what result should it produce?"])

    def test_unrelated_high_popularity_does_not_beat_courier_component(self):
        common = {
            "license": "MIT",
            "pushed_at": "2026-10-01T00:00:00Z",
            "archived": False,
            "readme": "",
            "recent_closed_issues": 0,
            "open_issues": 0,
        }
        relevant = {
            **common,
            "full_name": "example/shiprocket-courier-api",
            "description": "Indian parcel courier rate API for comparing shipping prices",
            "stars": 10,
        }
        unrelated = {
            **common,
            "full_name": "example/basketball-web-scraper",
            "description": "Web scraper for basketball statistics",
            "stars": 100_000,
            "recent_closed_issues": 10,
        }
        ranked = score_all({"candidates": [relevant, unrelated]}, "Compare Indian parcel courier prices with a scraper")["ranked"]
        self.assertEqual(ranked[0]["full_name"], relevant["full_name"])


if __name__ == "__main__":
    unittest.main()
