"""Regression checks for truthful executor status and bounded query terms."""

import unittest
from unittest.mock import patch

from harness import choose_decision, classify_aider_result, delivery_issues, implementation_written, plan_search, queries_for, select_gemini_api_key, structural_issues
from harness_core.backends import Reply


class HarnessTests(unittest.TestCase):
    def test_named_gemini_keys_select_independent_runs(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY_1": "first-test-key", "GEMINI_API_KEY_2": "second-test-key"}, clear=True):
            self.assertEqual(select_gemini_api_key("GEMINI_API_KEY_2"), "second-test-key")
            self.assertEqual(select_gemini_api_key(None), "first-test-key")
            with self.assertRaises(RuntimeError):
                select_gemini_api_key("UNTRUSTED_KEY")

    def test_decision_excludes_sources_that_cannot_be_inspected(self):
        class Backend:
            def chat(self, messages):
                import json
                evidence = json.loads(messages[1]["content"])["evidence"]
                self.names = [item["name"] for item in evidence]
                return Reply(text='{"action":"build_fresh","reason":"No useful fit"}')

        ranked = [
            {"full_name": "demo/too-large", "url": "https://github.com/demo/too-large", "score": 80, "signals": {"license": {"reason": "MIT"}}, "flags": [], "description": "", "size_kb": 60_000},
            {"full_name": "demo/usable", "url": "https://github.com/demo/usable", "score": 70, "signals": {"license": {"reason": "MIT"}}, "flags": [], "description": "", "size_kb": 1_000},
        ]
        backend = Backend()
        choose_decision(backend, "Build a game", {"ranked": ranked}, "")
        self.assertEqual(backend.names, ["demo/usable"])

    def test_partial_aider_edit_is_not_success(self):
        before = {"calculator.py": "", "README.md": ""}
        after = {"calculator.py": "def add(a,b): return a+b\n", "README.md": ""}
        self.assertEqual(classify_aider_result("finished", before, after, list(before), "Applied edit to calculator.py"), "partial_edit")

    def test_no_aider_edit_is_not_success(self):
        before = {"greeting.py": ""}
        self.assertEqual(classify_aider_result("finished", before, before, list(before), ""), "no_files_written")

    def test_build_requires_implementation_beyond_readme(self):
        readme = [{"tool": "write_file", "status": "ok", "path": "README.md"}]
        self.assertFalse(implementation_written(readme, "Build a playable platform game"))
        self.assertTrue(implementation_written(readme + [{"tool": "write_file", "status": "ok", "path": "index.html"}], "Build a playable platform game"))
        self.assertTrue(implementation_written(readme, "Write a README for this library"))

    def test_static_validation_catches_missing_web_assets_and_python_syntax(self):
        files = {"index.html": '<script src="script.js"></script>', "app.py": "def broken(:\n"}
        issues = structural_issues(files)
        self.assertTrue(any("script.js" in issue for issue in issues))
        self.assertTrue(any("Python syntax" in issue for issue in issues))
        files["script.js"] = "document.body.textContent = 'ok'"
        files["app.py"] = "print('ok')\n"
        self.assertEqual(structural_issues(files), [])

    def test_web_delivery_requires_a_browser_entry_point(self):
        task = "Build a browser-runnable study buddy web app"
        self.assertTrue(delivery_issues(task, {"main.py": "print('quiz')"}))
        self.assertEqual(delivery_issues(task, {"templates/index.html": "<h1>Quiz</h1>", "app.py": "from flask import Flask"}), [])
        self.assertEqual(delivery_issues("Build a command line CSV tool", {"main.py": "print('done')"}), [])

    def test_task_search_terms_drop_scaffolding(self):
        first, second, *_ = queries_for("Build a small Python module named csv_cleaner.py to normalize CSV")
        self.assertIn("csv", first)
        self.assertIn("cleaner", first)
        self.assertNotIn("module", first)
        self.assertIn("python", second)

    def test_fallback_search_has_whole_product_and_component_terms(self):
        for task in ("Build a parcel courier price scraper", "Merge PDF files with bookmarks", "Create a CSV cleaner"):
            with self.subTest(task=task):
                queries = queries_for(task)
                self.assertGreaterEqual(len(queries), 2)
                self.assertLessEqual(len(queries), 3)
                self.assertNotEqual(queries[0], queries[1])
                self.assertTrue(all(len(query.split()) <= 5 for query in queries))

    def test_fallback_keeps_product_terms_across_domains(self):
        examples = {
            "Build a scientific calculator web app that runs on localhost. Include trig functions.": ("scientific", "calculator"),
            "Build a small original side-scrolling platform game, playable at localhost.": ("platform", "game"),
            "Build a local AI study buddy web app. It should chat with a student.": ("study", "buddy"),
        }
        for task, expected in examples.items():
            with self.subTest(task=task):
                whole = queries_for(task)[0]
                self.assertTrue(all(word in whole for word in expected), whole)

    def test_model_plan_adds_whole_product_when_only_components_given(self):
        class Backend:
            def chat(self, messages):
                return Reply(text='{"capabilities":["PDF parsing","bookmark writing"],"queries":["pypdf library","document tree writer"]}')

        plan, reply = plan_search(Backend(), "Build a PDF merger with bookmarks")
        self.assertIsNotNone(reply)
        self.assertEqual(plan["source"], "model")
        self.assertIn("pdf", plan["queries"][0])
        self.assertIn("pypdf library", plan["queries"])

    def test_model_plan_keeps_distinct_component_queries(self):
        class Backend:
            def chat(self, messages):
                return Reply(text='{"capabilities":["Markdown parsing","slide rendering"],"queries":["markdown slides converter","marp core","reveal js slides"]}')

        plan, _ = plan_search(Backend(), "Convert Markdown into slides")
        self.assertEqual(plan["queries"], ["markdown slides converter", "marp core", "reveal js slides"])


if __name__ == "__main__":
    unittest.main()
