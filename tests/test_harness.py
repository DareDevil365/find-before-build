"""Regression checks for truthful executor status and bounded query terms."""

import unittest
from unittest.mock import patch

from harness import choose_decision, classify_aider_result, delivery_issues, implementation_written, plan_search, queries_for, select_gemini_api_key, select_gemini_api_keys, structural_issues, workspace_context
from harness_core.backends import GemmaAPI, Reply, RotatingGeminiAPI


class HarnessTests(unittest.TestCase):
    def test_workspace_context_is_bounded_and_skips_secret_files(self):
        context = workspace_context({"app.py": "print('ready')", ".env": "SECRET=hidden", "large.js": "x" * 9000}, limit=100)
        self.assertIn("app.py", context)
        self.assertNotIn("SECRET", context)
        self.assertNotIn("large.js", context)

    def test_named_gemini_keys_select_independent_runs(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY_1": "first-test-key", "GEMINI_API_KEY_2": "second-test-key", "GEMINI_API_KEY_5": "fifth-test-key", "GEMINI_API_KEY_3": "third-test-key"}, clear=True):
            self.assertEqual(select_gemini_api_key("GEMINI_API_KEY_2"), "second-test-key")
            self.assertEqual(select_gemini_api_key(None), "first-test-key")
            with self.assertRaises(RuntimeError):
                select_gemini_api_key("UNTRUSTED_KEY")
            self.assertEqual(select_gemini_api_keys(None), ["first-test-key", "second-test-key", "third-test-key", "fifth-test-key"])
            self.assertEqual(select_gemini_api_keys("GEMINI_API_KEY_2"), ["second-test-key"])

    def test_api_key_rotation_on_quota_only(self):
        used = []
        events = []

        def chat(client, messages, tools=None):
            used.append(client.api_key)
            if client.api_key == "first-test-key":
                raise RuntimeError("Model API HTTP 429: quota exceeded")
            return Reply(text="ready")

        with patch.object(GemmaAPI, "chat", chat):
            backend = RotatingGeminiAPI("gemini-2.5-flash", ["first-test-key", "second-test-key"], ["GEMINI_API_KEY_1", "GEMINI_API_KEY_2"], lambda *event: events.append(event))
            self.assertEqual(backend.chat([{"role": "user", "content": "test"}]).text, "ready")
            self.assertEqual(used, ["first-test-key", "second-test-key"])
            self.assertEqual([(event[0], event[1]) for event in events], [("GEMINI_API_KEY_1", "limited_429"), ("GEMINI_API_KEY_2", "success")])
            backend.chat([{"role": "user", "content": "again"}])
            self.assertEqual(used[-1], "second-test-key")
        with patch.object(GemmaAPI, "chat", side_effect=RuntimeError("Model API HTTP 429: quota exceeded")):
            with self.assertRaisesRegex(RuntimeError, "No configured Gemini key/model"):
                RotatingGeminiAPI("gemini-2.5-flash", ["first-test-key", "second-test-key"]).chat([])

    def test_gemini_falls_back_when_old_model_unavailable_for_key(self):
        events = []

        def chat(client, messages, tools=None):
            if client.model == "gemini-2.5-flash":
                raise RuntimeError("Model API HTTP 404: This model is no longer available")
            return Reply(text="ready", usage={"total_tokens": 8})

        with patch.object(GemmaAPI, "chat", chat):
            backend = RotatingGeminiAPI("gemini-2.5-flash", ["test-key"], ["GEMINI_API_KEY_2"], lambda *event: events.append(event), fallback_model="gemini-3.8-flash")
            self.assertEqual(backend.chat([]).text, "ready")
            self.assertEqual([(event[1], event[3]) for event in events], [("model_unavailable_404", "gemini-2.5-flash"), ("success", "gemini-3.8-flash")])
            self.assertEqual(backend.models_used, {"gemini-3.8-flash"})

    def test_temporary_high_demand_moves_to_next_key(self):
        events = []

        def chat(client, messages, tools=None):
            if client.api_key == "first-test-key":
                raise RuntimeError("Model API HTTP 503: high demand")
            return Reply(text="ready")

        with patch.object(GemmaAPI, "chat", chat):
            backend = RotatingGeminiAPI("gemini-2.5-flash", ["first-test-key", "second-test-key"], ["GEMINI_API_KEY_1", "GEMINI_API_KEY_2"], lambda *event: events.append(event))
            self.assertEqual(backend.chat([]).text, "ready")
            self.assertEqual(events[0][1], "unavailable_503")

    def test_preferred_model_is_tried_across_keys_before_fallback(self):
        calls = []

        def chat(client, messages, tools=None):
            calls.append((client.api_key, client.model))
            if client.api_key == "first-test-key" and client.model == "gemini-2.5-flash":
                raise RuntimeError("Model API HTTP 404: model unavailable")
            return Reply(text="ready")

        with patch.object(GemmaAPI, "chat", chat):
            backend = RotatingGeminiAPI("gemini-2.5-flash", ["first-test-key", "second-test-key"], fallback_model="gemini-3.8-flash")
            self.assertEqual(backend.chat([]).text, "ready")
            self.assertEqual(calls, [("first-test-key", "gemini-2.5-flash"), ("second-test-key", "gemini-2.5-flash")])

    def test_empty_api_reply_is_recorded_distinctly_from_success(self):
        events = []
        with patch.object(GemmaAPI, "chat", return_value=Reply(usage={"prompt_tokens": 15, "total_tokens": 15})):
            RotatingGeminiAPI("gemini-2.5-flash", ["test-key"], on_event=lambda *event: events.append(event)).chat([])
        self.assertEqual((events[0][1], events[0][2]["total_tokens"]), ("empty_reply", 15))

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
        self.assertTrue(delivery_issues(task, {"index.html": "<script>fetch('/chat')</script>"}))
        valid = {"index.html": "<script>fetch('/chat', {body: JSON.stringify({message: text})})</script>", "app.py": "from flask import Flask, request\napp = Flask(__name__)\n@app.get('/')\ndef home(): return 'page'\n@app.post('/chat')\ndef chat(): return request.json['message']"}
        self.assertEqual(delivery_issues(task, valid), [])
        broken = dict(valid, **{"app.py": "from flask import Flask, request\napp = Flask(__name__)\n@app.post('/chat')\ndef chat(): return request.json['user_input']\ndef quiz(): pass"})
        issues = delivery_issues(task, broken)
        self.assertTrue(any("GET /" in issue for issue in issues))
        self.assertTrue(any("placeholder" in issue for issue in issues))
        self.assertTrue(any("user_input" in issue for issue in issues))
        contract = {"index.html": "<script>fetch('/quiz', {body: JSON.stringify({answer: guess})}).then(r => r.json()).then(data => show(data.correct))</script>",
                    "app.py": "from flask import Flask, request, jsonify\napp = Flask(__name__)\n@app.get('/')\ndef home(): return 'page'\n@app.post('/quiz')\ndef quiz():\n    data = request.json\n    difficulty = data.get('difficulty')\n    return jsonify({'questions': []})\n"}
        issues = delivery_issues(task, contract)
        self.assertTrue(any("frontend sends" in issue for issue in issues))
        self.assertTrue(any("frontend reads response" in issue for issue in issues))

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
