"""Exercise the local UI protocol with a tiny stand-in harness subprocess."""

from __future__ import annotations

import json
import importlib.util
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ui import JobManager, create_server  # noqa: E402


FAKE_HARNESS = r'''import json
from pathlib import Path
import sys
workspace = Path(sys.argv[sys.argv.index("--workspace") + 1])
record = Path(sys.argv[sys.argv.index("--record") + 1])
print('@@EVENT:{"stage":"clarifying","detail":"Checking the request"}', flush=True)
print('@@QUESTION:{"question":"Which format?"}', flush=True)
answer = input().strip()
candidate = {"name":"example/reusable","url":"https://github.com/example/reusable","score":72,"license":"MIT","flags":[]}
decision = {"action":"dependency","repositories":["example/reusable"],"reason":"Useful component"}
print('@@EVENT:' + json.dumps({"stage":"choosing","detail":"Comparing candidates","candidates":[candidate],"quality":"live"}), flush=True)
print('@@EVENT:' + json.dumps({"stage":"deciding","detail":"Reuse decision made","decision":decision,"selected":["example/reusable"]}), flush=True)
print('@@EVENT:' + json.dumps({"stage":"coding","detail":"Writing output.txt","used":["example/reusable"]}), flush=True)
(workspace / "output.txt").write_text("Requested: " + answer, encoding="utf-8")
(workspace / "index.html").write_text("<h1>Generated preview is live</h1>", encoding="utf-8")
(workspace / "app.py").write_text('from flask import Flask\napp = Flask(__name__)\n@app.get("/")\ndef home(): return "Generated backend is live"\n', encoding="utf-8")
(workspace / "package.json").write_text(json.dumps({"scripts":{"start":"node server.js"}}), encoding="utf-8")
(workspace / "server.js").write_text('require("http").createServer((q,r)=>r.end("Generated Node app is live")).listen(process.env.PORT,"127.0.0.1");', encoding="utf-8")
(workspace / "node_modules").mkdir()
record.write_text(json.dumps({"status":"finished","written_files":["output.txt","index.html"],"discovery":{"candidates":[candidate],"quality":"live"},"decision":decision,"reuse":{"repositories":{"example/reusable":{}}}}), encoding="utf-8")
print("Built output.txt", flush=True)
'''


class UiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "harness.py").write_text(FAKE_HARNESS, encoding="utf-8")
        self.server = create_server(self.root, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        page = urlopen(self.base + "/").read().decode("utf-8")
        self.token = re.search(r"const token='([^']+)'", page).group(1)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, path: str, data: dict | None = None, token: str | None = None) -> tuple[int, dict]:
        body = json.dumps(data).encode("utf-8") if data is not None else None
        headers = {"X-UI-Token": self.token if token is None else token}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base + path, data=body, headers=headers)
        try:
            with urlopen(request) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            with exc:
                return exc.code, json.load(exc)

    def test_clarify_build_and_preview(self) -> None:
        status, started = self.request("/api/start", {"task": "Create a simple thing", "backend": "koboldcpp"})
        self.assertEqual(status, 200)
        job_id = started["id"]
        self.assertEqual(self.request("/api/current")[1]["id"], job_id)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            _, state = self.request("/api/state?id=" + job_id)
            if state["pending_question"]:
                break
            time.sleep(0.05)
        self.assertEqual(state["pending_question"], "Which format?")
        self.assertEqual(self.request("/api/answer", {"id": job_id, "answer": "JSON"})[0], 200)
        while time.monotonic() < deadline:
            _, state = self.request("/api/state?id=" + job_id)
            if not state["running"]:
                break
            time.sleep(0.05)
        self.assertFalse(state["running"])
        self.assertEqual(state["workspace"], str((self.root / "runs" / ("ui-" + job_id) / "workspace").resolve()))
        self.assertEqual(state["result"]["status"], "finished")
        self.assertEqual(state["stage"], "complete")
        self.assertEqual(state["candidates"][0]["name"], "example/reusable")
        self.assertEqual(state["selected"], ["example/reusable"])
        self.assertEqual(state["used"], ["example/reusable"])
        self.assertEqual(state["decision"]["reason"], "Useful component")
        self.assertIn("output.txt", state["files"])
        self.assertIn("index.html", state["files"])
        self.assertTrue(state["preview_url"].startswith("http://127.0.0.1:"))
        self.assertIn("Generated preview is live", urlopen(state["preview_url"]).read().decode("utf-8"))
        if importlib.util.find_spec("flask"):
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                _, state = self.request("/api/state?id=" + job_id)
                if any(item["status"] == "ready" for item in state["runtime_services"]):
                    break
                time.sleep(0.1)
            backend = next(item for item in state["runtime_services"] if item["name"] == "Flask backend")
            self.assertEqual(backend["status"], "ready")
            self.assertIn("Generated backend is live", urlopen(backend["url"]).read().decode("utf-8"))
        if shutil.which("npm.cmd" if sys.platform == "win32" else "npm"):
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                _, state = self.request("/api/state?id=" + job_id)
                if any(item["name"] == "Node app" and item["status"] == "ready" for item in state["runtime_services"]):
                    break
                time.sleep(0.1)
            node = next(item for item in state["runtime_services"] if item["name"] == "Node app")
            self.assertEqual(node["status"], "ready")
            self.assertIn("Generated Node app is live", urlopen(node["url"]).read().decode("utf-8"))
        self.assertEqual(self.request("/api/file?id=" + job_id + "&path=output.txt")[1]["content"], "Requested: JSON")
        self.assertEqual(self.request("/api/file?id=" + job_id + "&path=..%2Fharness.py")[0], 400)
        restored_manager = JobManager(self.root)
        restored = restored_manager.current()
        self.assertIsNotNone(restored)
        self.assertEqual(restored.id, job_id)
        self.assertIn("output.txt", restored.snapshot()["files"])
        self.assertIn("Generated preview is live", urlopen(restored.snapshot()["preview_url"]).read().decode("utf-8"))
        expected = int(bool(importlib.util.find_spec("flask"))) + int(bool(shutil.which("npm.cmd" if sys.platform == "win32" else "npm")))
        if expected:
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline and len(restored_manager.services) < expected:
                time.sleep(0.05)
        for preview in restored_manager.previews.values():
            preview.shutdown()
            preview.server_close()
        for process in restored_manager.services.values():
            if process.poll() is None:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                else:
                    process.terminate()
                process.wait(timeout=3)

    def test_served_browser_script_parses(self) -> None:
        if not shutil.which("node"):
            self.skipTest("Node.js is unavailable")
        page = urlopen(self.base + "/").read().decode("utf-8")
        script = re.search(r"<script>(.*?)</script>", page, re.S).group(1)
        script_file = self.root / "served-ui.js"
        script_file.write_text(script, encoding="utf-8")
        result = subprocess.run(["node", "--check", str(script_file)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_unauthorized_and_invalid_requests(self) -> None:
        self.assertEqual(self.request("/api/state?id=missing", token="wrong")[0], 403)
        self.assertEqual(self.request("/api/start", {"task": "x", "backend": "koboldcpp"})[0], 400)
        self.assertEqual(self.request("/api/start", {"task": "Build something", "backend": "unknown"})[0], 400)
        self.assertEqual(self.request("/api/answer", {"id": "missing", "answer": "Yes"})[0], 400)


if __name__ == "__main__":
    unittest.main()
