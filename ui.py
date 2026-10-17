"""Local browser interface for the Find Before Build harness.

Run ``python ui.py`` and open the printed localhost URL. The UI starts the real
harness in an isolated workspace and relays its clarification questions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import partial
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
import argparse
import ast
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parent
MAX_BODY = 20_000
MAX_PREVIEW = 200_000


class LocalServer(ThreadingHTTPServer):
    # Windows can otherwise let two UI processes listen on the same port and
    # send requests to whichever copy happens to accept them.
    allow_reuse_address = False

    def server_close(self) -> None:
        manager = getattr(self, "manager", None)
        if manager is not None:
            for preview in manager.previews.values():
                preview.shutdown()
                preview.server_close()
            for process in manager.services.values():
                if process.poll() is None:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                    else:
                        process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
        super().server_close()


@dataclass
class Job:
    id: str
    task: str
    backend: str
    workspace: Path
    record: Path
    process: subprocess.Popen[str] | None = None
    lines: list[str] = field(default_factory=list)
    pending_question: str | None = None
    result: dict | None = None
    exit_code: int | None = None
    error: str | None = None
    stage: str = "starting"
    detail: str = "Starting the harness"
    candidates: list[dict] = field(default_factory=list)
    decision: dict | None = None
    selected: list[str] = field(default_factory=list)
    used: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    quality: str = "unknown"
    discovery_error: str = ""
    preview_url: str | None = None
    runtime_services: list[dict] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "id": self.id,
                "task": self.task,
                "backend": self.backend,
                "running": self.exit_code is None and self.error is None,
                "exit_code": self.exit_code,
                "error": self.error,
                "stage": self.stage,
                "detail": self.detail,
                "candidates": self.candidates,
                "decision": self.decision,
                "selected": self.selected,
                "used": self.used,
                "queries": self.queries,
                "quality": self.quality,
                "discovery_error": self.discovery_error,
                "pending_question": self.pending_question,
                "lines": self.lines[-400:],
                "result": self.result,
                "files": self.files(),
                "workspace": str(self.workspace.resolve()),
                "preview_url": self.preview_url,
                "runtime_services": [dict(item) for item in self.runtime_services],
            }

    def files(self) -> list[str]:
        return sorted(
            path.relative_to(self.workspace).as_posix()
            for path in self.workspace.rglob("*")
            if path.is_file() and not path.is_symlink()
            and not any(part.startswith(".") or part == "__pycache__" for part in path.relative_to(self.workspace).parts)
        )

    def answer(self, value: str) -> None:
        with self.lock:
            if self.pending_question is None or self.process is None or self.process.poll() is not None:
                raise ValueError("There is no unanswered question")
            assert self.process.stdin is not None
            try:
                self.process.stdin.write(value.replace("\r", " ").replace("\n", " ") + "\n")
                self.process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise ValueError("The harness is no longer accepting answers") from exc
            self.lines.append("You answered a clarification question.")
            self.pending_question = None


class JobManager:
    def __init__(self, root: Path = ROOT):
        self.root = root
        self.jobs: dict[str, Job] = {}
        self.previews: dict[str, ThreadingHTTPServer] = {}
        self.services: dict[str, subprocess.Popen] = {}
        self.lock = threading.Lock()
        self._restore_latest_completed()

    def _restore_latest_completed(self) -> None:
        runs = self.root / "runs"
        for run_dir in sorted(runs.glob("ui-*") if runs.exists() else [], key=lambda path: path.name, reverse=True):
            record = run_dir / "record.json"
            workspace = run_dir / "workspace"
            if not record.is_file() or not workspace.is_dir():
                continue
            try:
                result = json.loads(record.read_text(encoding="utf-8"))
                if not isinstance(result, dict):
                    continue
                decision = result.get("decision") or {}
                selected = decision.get("repositories") or ([decision["repository"]] if decision.get("repository") else [])
                discovery = result.get("discovery") or {}
                error = str(discovery.get("error", ""))
                job = Job(run_dir.name[3:], str(result.get("original_task") or result.get("task") or ""), str(result.get("backend") or ""), workspace, record)
                job.result = result
                job.exit_code = 0 if result.get("status") == "finished" else 2
                job.stage = "complete"
                job.detail = "Build ended: " + str(result.get("status", "unknown"))
                job.lines = ["Restored the latest completed build after the UI server restarted."]
                job.candidates = discovery.get("candidates") or []
                job.quality = discovery.get("quality") or ("unavailable" if discovery.get("status") == "unavailable" else "unknown")
                job.discovery_error = "GitHub API rate limit exceeded" if "rate limit" in error.lower() else error[:160]
                job.decision = decision
                job.selected = selected
                job.used = list((result.get("reuse") or {}).get("repositories") or {})
                job.queries = discovery.get("queries") or []
                self.jobs[job.id] = job
                self._start_preview(job)
                self._start_runtimes(job)
                break
            except (OSError, ValueError, TypeError, KeyError):
                continue

    def start(self, task: str, backend: str, no_questions: bool = False, web_app: bool = False) -> Job:
        task = task.strip()
        if not 3 <= len(task) <= 6000:
            raise ValueError("Enter a request between 3 and 6,000 characters")
        if backend not in {"koboldcpp", "gemma-api", "gemini-api"}:
            raise ValueError("Choose KoboldCpp, Gemma, or Gemini")
        if not isinstance(no_questions, bool) or not isinstance(web_app, bool):
            raise ValueError("Invalid build setting")
        with self.lock:
            if any(job.snapshot()["running"] for job in self.jobs.values()):
                raise ValueError("A build is already running. Finish it before starting another.")
            job_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)
            run_dir = self.root / "runs" / ("ui-" + job_id)
            workspace = run_dir / "workspace"
            workspace.mkdir(parents=True)
            job = Job(job_id, task, backend, workspace, run_dir / "record.json")
            self.jobs[job_id] = job

        effective_task = task + ("\n\nDelivery target: Build a browser-runnable web app with an HTTP entry point and a usable page. Include the frontend and backend needed for this request, with local setup instructions. Do not stop at a command-line-only interface." if web_app else "")
        command = [
            sys.executable, "-u", str(self.root / "harness.py"), effective_task,
            "--workspace", str(workspace), "--backend", backend,
            "--engine", "native", "--question-channel", "json",
            "--no-commands", "--record", str(job.record),
        ]
        command.append("--no-questions" if no_questions else "--clarify")
        if backend == "gemma-api":
            command += ["--model", "gemma-4-26b-a4b-it"]
        elif backend == "gemini-api":
            command += ["--model", "gemini-2.5-flash"]
        try:
            job.process = subprocess.Popen(
                command, cwd=self.root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
        except OSError as exc:
            with job.lock:
                job.error = str(exc)
            return job
        threading.Thread(target=self._read, args=(job,), daemon=True).start()
        return job

    def _start_preview(self, job: Job) -> None:
        if job.id in self.previews:
            return
        roots = [job.workspace] + [p for p in job.workspace.iterdir() if p.is_dir() and not p.is_symlink() and p.name not in {"node_modules", "__pycache__", "venv", ".venv", "templates"} and not p.name.startswith(".")]
        site = next((p for p in roots if (p / "index.html").is_file()), None)
        if site is None:
            return
        handler = partial(SimpleHTTPRequestHandler, directory=str(site))
        try:
            preview = LocalServer(("127.0.0.1", 0), handler)
        except OSError as exc:
            with job.lock:
                job.lines.append("Local preview could not start: " + str(exc))
            return
        self.previews[job.id] = preview
        job.preview_url = f"http://127.0.0.1:{preview.server_address[1]}/"
        threading.Thread(target=preview.serve_forever, daemon=True).start()

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _start_runtimes(self, job: Job) -> None:
        """Launch recognizable local web stacks after the generated build finishes."""
        if job.result is None or job.result.get("status") != "finished":
            return
        roots = [job.workspace] + [p for p in job.workspace.iterdir() if p.is_dir() and not p.is_symlink() and p.name not in {"node_modules", "__pycache__", "venv", ".venv"} and not p.name.startswith(".")]
        for project in roots:
            package = project / "package.json"
            if package.is_file():
                try:
                    manifest = json.loads(package.read_text(encoding="utf-8"))
                    scripts = manifest.get("scripts") or {}
                    deps = {**(manifest.get("dependencies") or {}), **(manifest.get("devDependencies") or {})}
                except (OSError, ValueError, TypeError):
                    scripts, deps = {}, {}
                if "dev" in scripts and ("vite" in deps or "next" in deps):
                    port = self._free_port()
                    args = ["npm.cmd" if os.name == "nt" else "npm", "run", "dev", "--"]
                    args += ["--hostname", "127.0.0.1", "--port", str(port)] if "next" in deps else ["--host", "127.0.0.1", "--port", str(port)]
                    self._launch_service(job, project, "Frontend", args, port, install_npm=True)
                elif "start" in scripts:
                    port = self._free_port()
                    args = ["npm.cmd" if os.name == "nt" else "npm", "run", "start"]
                    self._launch_service(job, project, "Node app", args, port, install_npm=True)
            for name in ("app.py", "main.py"):
                source = project / name
                if not source.is_file():
                    continue
                try:
                    tree = ast.parse(source.read_text(encoding="utf-8"))
                except (OSError, SyntaxError, UnicodeError):
                    continue
                imports = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
                imports |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
                if "fastapi" in imports:
                    port = self._free_port()
                    self._launch_service(job, project, "FastAPI backend", [sys.executable, "-m", "uvicorn", source.stem + ":app", "--host", "127.0.0.1", "--port", str(port)], port)
                elif "flask" in imports:
                    port = self._free_port()
                    self._launch_service(job, project, "Flask backend", [sys.executable, "-m", "flask", "--app", source.stem + ":app", "run", "--host", "127.0.0.1", "--port", str(port)], port)
                break

    def _launch_service(self, job: Job, project: Path, label: str, args: list[str], port: int, install_npm: bool = False) -> None:
        key = job.id + ":" + str(project) + ":" + label
        if key in self.services:
            return
        url = f"http://127.0.0.1:{port}/"
        item = {"name": label, "url": url, "status": "starting", "folder": str(project.relative_to(job.workspace))}
        with job.lock:
            job.runtime_services.append(item)

        def launch() -> None:
            if install_npm and not (project / "node_modules").is_dir():
                with job.lock:
                    item["status"] = "installing dependencies"
                npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
                if npm is None:
                    with job.lock:
                        item["status"] = "npm unavailable"
                    return
                try:
                    result = subprocess.run([npm, "install", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=project, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
                except (OSError, subprocess.TimeoutExpired):
                    result = None
                if result is None or result.returncode:
                    with job.lock:
                        item["status"] = "dependency install failed"
                    return
            log_file = job.record.parent / ("runtime-" + label.lower().replace(" ", "-") + ".log")
            env = {**os.environ, "HOST": "127.0.0.1", "PORT": str(port), "BROWSER": "none"}
            try:
                with log_file.open("wb") as log:
                    process = subprocess.Popen(args, cwd=project, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
                self.services[key] = process
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline and process.poll() is None:
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                            with job.lock:
                                item["status"] = "ready"
                            return
                    except OSError:
                        time.sleep(0.25)
                with job.lock:
                    item["status"] = "exited" if process.poll() is not None else "startup timed out"
            except OSError as exc:
                with job.lock:
                    item["status"] = "could not start: " + str(exc)

        threading.Thread(target=launch, daemon=True).start()

    def _read(self, job: Job) -> None:
        assert job.process is not None and job.process.stdout is not None
        try:
            for line in job.process.stdout:
                line = line.rstrip("\r\n")
                if line.startswith("@@EVENT:"):
                    try:
                        event = json.loads(line[len("@@EVENT:"):])
                        if isinstance(event, dict):
                            with job.lock:
                                for key in ("stage", "detail", "candidates", "decision", "selected", "used", "queries", "quality", "discovery_error"):
                                    if key in event:
                                        setattr(job, key, event[key])
                    except (json.JSONDecodeError, TypeError):
                        pass
                elif line.startswith("@@QUESTION:"):
                    try:
                        question = json.loads(line[len("@@QUESTION:"):])["question"]
                    except (json.JSONDecodeError, KeyError, TypeError):
                        question = "The harness asked for more detail."
                    with job.lock:
                        job.pending_question = str(question)
                        job.lines.append("Clarification requested: " + str(question))
                else:
                    with job.lock:
                        job.lines.append(line)
                        if len(job.lines) > 800:
                            del job.lines[:400]
                self._start_preview(job)
            exit_code = job.process.wait()
            result = None
            if job.record.exists():
                try:
                    result = json.loads(job.record.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    pass
            with job.lock:
                job.result = result
                job.exit_code = exit_code
                job.pending_question = None
                if result:
                    job.stage = "complete"
                    job.detail = "Build ended: " + str(result.get("status", "unknown"))
                    discovery = result.get("discovery") or {}
                    job.candidates = discovery.get("candidates", job.candidates)
                    job.quality = discovery.get("quality") or ("unavailable" if discovery.get("status") == "unavailable" else job.quality)
                    job.discovery_error = "GitHub API rate limit exceeded" if "rate limit" in str(discovery.get("error", "")).lower() else str(discovery.get("error", ""))[:160]
                    job.decision = result.get("decision", job.decision)
                    job.used = list((result.get("reuse") or {}).get("repositories") or {})
            self._start_preview(job)
            self._start_runtimes(job)
        except Exception as exc:  # keep the browser informed if the reader fails
            with job.lock:
                job.error = str(exc)
                job.pending_question = None

    def get(self, job_id: str) -> Job:
        with self.lock:
            try:
                return self.jobs[job_id]
            except KeyError as exc:
                raise ValueError("Unknown build") from exc

    def current(self) -> Job | None:
        with self.lock:
            return next(reversed(self.jobs.values()), None) if self.jobs else None


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Find Before Build</title><style>
:root{color-scheme:dark;font:15px/1.5 Inter,Segoe UI,Arial,sans-serif;background:#050b11;color:#e8f7ff;--cyan:#55e6f2;--line:#234554;--muted:#92aeba;--panel:#0b1821}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(circle at 74% -12%,#153846 0,transparent 34%),radial-gradient(circle at -10% 50%,#0d2732 0,transparent 30%),#050b11}body:before{content:"";position:fixed;inset:0;pointer-events:none;opacity:.1;background-image:linear-gradient(#4fd7e9 1px,transparent 1px),linear-gradient(90deg,#4fd7e9 1px,transparent 1px);background-size:44px 44px;mask-image:linear-gradient(to bottom,#000,transparent 90%)}
.shell{max-width:1450px;margin:auto;padding:24px clamp(18px,3vw,48px) 56px;position:relative}.topbar{display:flex;justify-content:space-between;align-items:center;gap:18px;border-bottom:1px solid var(--line);padding:4px 0 22px;margin-bottom:26px}.brand{display:flex;align-items:center;gap:15px}.mark{height:43px;width:43px;border:1px solid var(--cyan);display:grid;place-items:center;transform:rotate(45deg);box-shadow:0 0 24px #31ddea44}.mark span{transform:rotate(-45deg);font:700 12px Consolas,monospace;color:var(--cyan)}.eyebrow{font:700 10px Consolas,monospace;letter-spacing:.22em;text-transform:uppercase;color:var(--cyan)}.brandname{font-size:16px;font-weight:750;letter-spacing:.07em}.topmeta{display:flex;align-items:center;gap:10px;color:var(--muted);font:12px Consolas,monospace}.led{height:7px;width:7px;border-radius:50%;background:#6af0b8;box-shadow:0 0 11px #6af0b8}
.hero{display:grid;grid-template-columns:minmax(0,1fr) 210px;gap:28px;align-items:center;margin:0 0 24px}.hero h1{font-size:clamp(34px,4.2vw,58px);line-height:1.05;letter-spacing:-.045em;margin:8px 0 12px}.hero h1 span{color:var(--cyan)}.hero p{color:#abc3cc;max-width:650px;margin:0;font-size:16px}.orbit{height:166px;width:166px;margin:auto;border:1px solid #3bd9e244;border-radius:50%;display:grid;place-items:center;position:relative;box-shadow:0 0 65px #2dd7e516,inset 0 0 35px #2dd7e519}.orbit:before,.orbit:after{content:"";position:absolute;border:1px dashed #51e8ef77;border-radius:50%;inset:15px}.orbit:after{inset:31px;border-style:solid;border-color:#51e8ef55}.orbit b{font:700 13px Consolas,monospace;letter-spacing:.2em;color:var(--cyan);text-shadow:0 0 12px #3bd9e2}
section{background:linear-gradient(145deg,#10232d,#0a161e 70%);border:1px solid var(--line);border-radius:13px;padding:22px;margin:0 0 18px;box-shadow:0 14px 50px #0005;position:relative;overflow:hidden}section:before{content:"";position:absolute;top:0;left:0;height:2px;width:84px;background:var(--cyan);box-shadow:0 0 16px var(--cyan)}section h2{font-size:19px;letter-spacing:-.015em;margin:3px 0 13px}.sectionhead{display:flex;align-items:center;justify-content:space-between;gap:10px}.tag{font:11px Consolas,monospace;color:var(--cyan);letter-spacing:.13em;text-transform:uppercase}.hint,p{color:var(--muted)}.hint{margin:0 0 13px;font-size:13px}
textarea,input,select,button{font:inherit;border-radius:7px}textarea,input,select{color:#eafcff;background:#07131a;border:1px solid #315462;outline:none}textarea:focus,input:focus,select:focus{border-color:var(--cyan);box-shadow:0 0 0 3px #47ddea22}textarea{width:100%;min-height:126px;resize:vertical;padding:16px;line-height:1.55}input,select{padding:11px 13px}button{border:1px solid #46d9e6;background:#113944;color:#eaffff;padding:10px 16px;font-weight:700;cursor:pointer;transition:transform .16s,background .16s,box-shadow .16s}button:hover:not(:disabled){transform:translateY(-2px);background:#155463;box-shadow:0 0 21px #2bd5eb33}button:disabled{opacity:.45;cursor:default}.primary{background:var(--cyan);color:#03212a;border-color:var(--cyan);box-shadow:0 0 20px #40d8e43d}.primary:hover:not(:disabled){background:#93faff;color:#031a22}.row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.row input{flex:1;min-width:180px}.controls{display:flex;justify-content:space-between;gap:14px;align-items:end;flex-wrap:wrap;margin-top:13px}.controls label{display:block;margin:0 0 6px;color:var(--muted);font-size:12px}.controls select{min-width:220px}.controls .row{justify-content:flex-end}
.dashboard{display:grid;grid-template-columns:minmax(0,1fr) minmax(330px,1fr);gap:18px;align-items:stretch}.dashboard section{margin:0 0 18px;min-height:300px}.hidden{display:none!important}#status{font:700 11px Consolas,monospace;letter-spacing:.15em;text-transform:uppercase;color:#a9f8c7;border:1px solid #34765a;padding:5px 9px;border-radius:30px}#stage{font-size:26px;font-weight:700;margin:24px 0 4px}#detail,#repoNote{color:#9db8c1;font-size:13px}.steps{display:grid;grid-template-columns:repeat(5,1fr);gap:6px;padding:0;margin:20px 0;list-style:none}.steps li{border-top:2px solid #365360;padding:10px 2px;color:#7898a6;font:10px Consolas,monospace;text-transform:uppercase;letter-spacing:.08em}.steps li.active{border-color:var(--cyan);color:var(--cyan);text-shadow:0 0 10px #54ecf1}.steps li.done{border-color:#57cba1;color:#8cedc2}.decision{border-left:2px solid var(--cyan);padding:9px 12px;margin:16px 0;background:#0b2530;color:#cdebf0;font-size:13px}
.controls .fastoption{display:flex;align-items:center;gap:7px;margin-top:9px;font-size:12px;color:#abcbd2}.fastoption input{margin:0;padding:0;accent-color:var(--cyan);width:15px;height:15px;flex:none}
.repo{border:1px solid #264754;border-radius:9px;padding:11px 13px;margin:9px 0;background:#081721}.repo.chosen{border-color:#68efbc;background:#0c302c;box-shadow:inset 3px 0 #68efbc}.repo a{font-weight:700;color:#83eaf2;text-decoration:none}.repo a:hover{text-decoration:underline}.repo small{display:block;color:#9fbac3;margin-top:4px;font:11px Consolas,monospace}.badge{display:inline-block;border:1px solid #68efbc;border-radius:15px;padding:2px 8px;color:#b2fbd5;font:10px Consolas,monospace;margin-left:7px}.repo p{margin:7px 0 0;font-size:11px;line-height:1.5}.reposlist{max-height:390px;overflow:auto;padding-right:4px}
details{margin-top:21px;border-top:1px solid var(--line);padding-top:13px}summary{cursor:pointer;color:var(--cyan);font:12px Consolas,monospace}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#061018;color:#b4cdd7;padding:15px;border:1px solid #1f3b48;border-radius:8px;max-height:340px;overflow:auto;font:12px/1.5 Consolas,monospace}#logs{font-size:11px}#questionText{color:#e3f8ff;font-size:18px}.outputgrid{display:grid;grid-template-columns:minmax(230px,.35fr) minmax(0,.65fr);gap:18px}.filelist{list-style:none;padding:0;margin:10px 0}.filelist li{margin:5px 0;padding:7px 9px;border:1px solid #244351;border-radius:5px}.filelist a{color:#79e8f3;text-decoration:none;overflow-wrap:anywhere}.filelist a:hover{text-decoration:underline}.pathrow{display:flex;gap:10px;align-items:center;flex-wrap:wrap}#outputPath{display:block;flex:1;min-width:250px;overflow-wrap:anywhere;color:#bff7f5;background:#07131a;border:1px solid #284753;border-radius:7px;padding:11px;font:12px Consolas,monospace}.outputlabel{margin:6px 0 3px;font:11px Consolas,monospace;text-transform:uppercase;letter-spacing:.1em;color:var(--muted)}h3{font-size:13px;text-transform:uppercase;letter-spacing:.1em;color:#a4c4ce;margin:15px 0 8px}
.previewlaunch{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:15px 0;padding:12px 14px;border:1px solid var(--cyan);border-radius:8px;background:#0a2730}.previewlaunch a{color:#08161c;background:var(--cyan);padding:9px 13px;border-radius:5px;font-weight:700;text-decoration:none}.previewlaunch code{color:#a9e8ef;overflow-wrap:anywhere}.decision:empty{display:none}
@media(max-width:900px){.hero{grid-template-columns:1fr}.orbit{display:none}.dashboard,.outputgrid{grid-template-columns:1fr}}@media(max-width:560px){.shell{padding:17px 13px 35px}.topmeta{display:none}.controls{align-items:stretch}.controls .row{justify-content:stretch}.controls .row>*{flex:1}.controls select{width:100%}.steps li{font-size:9px}section{padding:17px}}
@media(prefers-reduced-motion:reduce){button{transition:none}}
</style></head><body>
<main class="shell"><header class="topbar"><div class="brand"><div class="mark" aria-hidden="true"><span>F/B</span></div><div><div class="eyebrow">Project intelligence / local console</div><div class="brandname">FIND BEFORE BUILD</div></div></div><div class="topmeta"><span class="led" aria-hidden="true"></span><span>LOCAL SYSTEM READY</span></div></header>
<div class="hero"><div><div class="eyebrow">01 / DISCOVER &nbsp; 02 / DECIDE &nbsp; 03 / BUILD</div><h1>Build smarter.<br><span>Start with what exists.</span></h1><p>Describe your idea. The harness checks GitHub for reusable work, explains its choice, then builds with your selected model.</p></div><div class="orbit" aria-hidden="true"><b>F / B</b></div></div>
<section><div class="sectionhead"><h2>Mission brief</h2><span class="tag">Input / 001</span></div><p class="hint">Be specific about the platform, features, and what should work when the build is done.</p><label class="eyebrow" for="task">Your request</label><textarea id="task" placeholder="Build a web calculator with keyboard input and a clean interface..."></textarea>
<div class="controls"><div><label for="backend">Model</label><select id="backend"><option value="gemini-api">Gemini 2.5 Flash</option><option value="gemma-api">Gemma 4 API</option><option value="koboldcpp">Local model (KoboldCpp)</option></select><label class="fastoption"><input id="webApp" type="checkbox" checked> Build a web app with a localhost link</label><label class="fastoption"><input id="noQuestions" type="checkbox"> Fast mode: build with sensible defaults, without pausing for questions</label></div><div class="row"><button id="lastBuild" type="button" class="hidden">View last build</button><button id="newRequest" type="button" class="hidden">New request</button><button id="start" type="button" class="primary">Find and build →</button></div></div></section>
<section id="question" class="hidden"><div class="sectionhead"><h2>Clarification needed</h2><span class="tag">Human input</span></div><p id="questionText"></p><div class="row"><input id="answer" aria-label="Your answer" placeholder="Type your answer"><button id="sendAnswer" type="button" class="primary">Continue →</button></div></section>
<div class="dashboard"><section><div class="sectionhead"><h2>Build telemetry</h2><span id="status" role="status">Ready</span></div><ol class="steps" id="steps"><li>Clarify</li><li>Search</li><li>Choose</li><li>Build</li><li>Finish</li></ol><div id="stage">Ready for a request</div><p id="detail">Launch a mission to see each stage in real time.</p><details><summary>Open detailed activity log</summary><pre id="logs">Your build log will appear here.</pre></details></section>
<section><div class="sectionhead"><h2>Repository intelligence</h2><span class="tag">GitHub / scan</span></div><p id="repoNote">Search results will appear here. A green outline marks a repository chosen for reuse.</p><div id="decisionReason" class="decision"></div><div id="repos" class="reposlist"></div></section></div>
<section><div class="sectionhead"><h2>Build artifacts</h2><span class="tag">Local / output</span></div><p class="outputlabel">Saved folder</p><div class="pathrow"><code id="outputPath">Start a build to see its folder.</code><button id="copyPath" type="button" disabled>Copy path</button></div><div id="previewLaunch" class="previewlaunch hidden"><a id="openApp" href="#" target="_blank" rel="noopener noreferrer">Open app →</a><code id="previewUrl"></code></div><div id="runtimeServices"></div><div class="outputgrid"><div><h3>Files</h3><ul id="files" class="filelist"><li>Start a build to see files.</li></ul></div><div><h3>Preview</h3><pre id="preview">Select a file.</pre></div></div></section></main>
<script>
const token='__TOKEN__';let job=null;let busy=false;let lastQuestion=null;let lastBuildId=null;
const $=id=>document.getElementById(id);
async function api(path,options={}){let r=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-UI-Token':token,...options.headers}});let j=await r.json();if(r.status===403&&j.error==='Open this UI from its localhost page'&&!sessionStorage.getItem('fb-reloaded')){sessionStorage.setItem('fb-reloaded','1');location.reload();throw Error('Session restarted; reloading the page')}if(!r.ok)throw Error(j.error||'Request failed');sessionStorage.removeItem('fb-reloaded');return j}
const stages=['clarifying','searching','deciding','coding','complete'];
function showProgress(s){let index=stages.indexOf(s.stage);if(s.stage==='choosing')index=2;if(s.stage==='starting')index=-1;for(let [n,li] of [...$('steps').children].entries()){li.className=n<index?'done':n===index?'active':''}let names={starting:'Starting',clarifying:'Clarifying the request',searching:'Searching GitHub',choosing:'Comparing repositories',deciding:'Making a reuse decision',coding:'Building the project',complete:'Run finished'};$('stage').textContent=names[s.stage]||'Working';$('detail').textContent=s.detail||''}
function showRepos(s){let repos=$('repos');repos.replaceChildren();let selected=new Set(s.selected||[]),used=new Set(s.used||[]),decision=s.decision;
let note=s.quality==='metadata_only'?'GitHub limited README or issue checks; these rankings use metadata only.':s.quality==='cached_unverified'?'Fresh GitHub search failed; showing recent related cached results. Verify them before reuse.':s.quality==='unavailable'?'GitHub discovery failed: '+(s.discovery_error||'unknown cause')+'. No repositories were checked in this run.':'A green outline marks a repository chosen for reuse.';$('repoNote').textContent=note;
$('decisionReason').textContent=decision?(decision.action==='build_fresh'?'Decision: build fresh. No repository was chosen. ':'Decision: '+decision.action+'. ')+(decision.reason||''):'';
for(let item of s.candidates||[]){let card=document.createElement('div');card.className='repo'+(selected.has(item.name)?' chosen':'');let title=document.createElement('a');if(/^https:\/\/github\.com\//.test(item.url||'')){title.href=item.url;title.target='_blank';title.rel='noopener noreferrer'}title.textContent=item.name;card.append(title);if(selected.has(item.name)){let badge=document.createElement('span');badge.className='badge';badge.textContent=used.has(item.name)?'Source loaded':'Chosen for reuse';card.append(badge)}let meta=document.createElement('small');meta.textContent='Score '+item.score+' · License '+item.license;card.append(meta);if(item.flags?.length){let flags=document.createElement('p');flags.textContent=item.flags.join(' · ');card.append(flags)}repos.append(card)}
if(!s.candidates?.length){let empty=document.createElement('p');empty.textContent=s.quality==='unavailable'?'No repository results; discovery failed.':s.stage==='searching'||s.stage==='clarifying'?'Waiting for GitHub results...':'No repository results recorded.';repos.append(empty)}}
async function start(){if(busy)return;try{let j=await api('/api/start',{method:'POST',body:JSON.stringify({task:$('task').value,backend:$('backend').value,no_questions:$('noQuestions').checked,web_app:$('webApp').checked})});job=j.id;lastBuildId=job;busy=true;$('start').disabled=true;$('lastBuild').classList.add('hidden');$('newRequest').classList.add('hidden');$('previewLaunch').classList.add('hidden');$('status').textContent='Starting...';$('logs').textContent='';$('files').replaceChildren();$('outputPath').textContent='Loading folder path...';poll()}catch(e){$('status').textContent=e.message}}
async function sendAnswer(){try{await api('/api/answer',{method:'POST',body:JSON.stringify({id:job,answer:$('answer').value})});$('answer').value='';$('question').classList.add('hidden');lastQuestion=null}catch(e){$('status').textContent=e.message}}
async function poll(){if(!job)return;let refreshServices=false;try{let s=await api('/api/state?id='+encodeURIComponent(job));let label=s.pending_question?'Waiting for your answer':s.running?'Running':(s.result?.status|| (s.error?'Error':'Exited '+s.exit_code));$('status').textContent=label+(s.error?' - '+s.error:'');$('logs').textContent=s.lines.join('\n');$('logs').scrollTop=$('logs').scrollHeight;
$('outputPath').textContent=s.workspace||'Folder path unavailable';$('copyPath').disabled=!s.workspace;
if(s.preview_url){$('previewLaunch').classList.remove('hidden');$('openApp').href=s.preview_url;$('previewUrl').textContent=s.preview_url}else{$('previewLaunch').classList.add('hidden')}
let services=$('runtimeServices');services.replaceChildren();for(let item of s.runtime_services||[]){let row=document.createElement('p'),link=document.createElement('a');link.href=item.url;link.target='_blank';link.rel='noopener noreferrer';link.textContent=item.name+' → '+item.url;row.append(link,document.createTextNode(' · '+item.status));services.append(row)}
refreshServices=(s.runtime_services||[]).some(item=>item.status==='starting'||item.status==='installing dependencies');
showProgress(s);showRepos(s);
if(s.pending_question){$('stage').textContent='Paused for clarification';$('detail').textContent='The build will continue after you answer the question above.'}
if(s.pending_question){$('question').classList.remove('hidden');$('questionText').textContent=s.pending_question;if(lastQuestion!==s.pending_question){$('answer').focus();lastQuestion=s.pending_question}}else{$('question').classList.add('hidden');lastQuestion=null}
let list=$('files');list.replaceChildren();for(let name of s.files){let li=document.createElement('li'),a=document.createElement('a');a.href='#';a.textContent=name;a.onclick=async e=>{e.preventDefault();try{let f=await api('/api/file?id='+encodeURIComponent(job)+'&path='+encodeURIComponent(name));$('preview').textContent=f.content}catch(err){$('preview').textContent=err.message}};li.append(a);list.append(li)}
if(!s.running){busy=false;$('start').disabled=false;$('newRequest').classList.remove('hidden')}}catch(e){$('status').textContent=e.message}if(busy||refreshServices)setTimeout(poll,1000)}
$('start').onclick=start;$('sendAnswer').onclick=sendAnswer;$('answer').onkeydown=e=>{if(e.key==='Enter')sendAnswer()};
$('copyPath').onclick=async()=>{try{await navigator.clipboard.writeText($('outputPath').textContent);$('copyPath').textContent='Copied';setTimeout(()=>$('copyPath').textContent='Copy path',1800)}catch(e){$('copyPath').textContent='Select path above to copy'}};
$('newRequest').onclick=()=>{if(busy)return;job=null;$('task').value='';$('status').textContent='Ready';$('stage').textContent='Standing by';$('detail').textContent='Describe your project to begin.';$('logs').textContent='';$('repoNote').textContent='Repository search has not started.';$('decisionReason').textContent='';$('repos').replaceChildren();$('files').innerHTML='<li>Start a build to see files.</li>';$('preview').textContent='Select a file.';$('outputPath').textContent='Start a build to see its folder.';$('copyPath').disabled=true;$('previewLaunch').classList.add('hidden');$('runtimeServices').replaceChildren();$('question').classList.add('hidden');$('newRequest').classList.add('hidden');$('lastBuild').classList.toggle('hidden',!lastBuildId);for(let li of $('steps').children)li.className=''};
$('lastBuild').onclick=()=>{if(!lastBuildId||busy)return;job=lastBuildId;busy=true;$('start').disabled=true;$('lastBuild').classList.add('hidden');poll()};
api('/api/current').then(current=>{if(job)return;if(current.id){lastBuildId=current.id;if(current.running){job=current.id;busy=true;$('start').disabled=true;poll()}else{$('lastBuild').classList.remove('hidden')}}}).catch(()=>{});
</script></body></html>"""


def create_server(root: Path = ROOT, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    manager = JobManager(root)
    token = secrets.token_urlsafe(24)

    class Handler(BaseHTTPRequestHandler):
        def _local_host(self) -> bool:
            supplied = self.headers.get("Host", "")
            port_number = self.server.server_address[1]
            return supplied in {f"127.0.0.1:{port_number}", f"localhost:{port_number}"}

        def _send(self, status: int, data: dict | str, content_type: str = "application/json") -> None:
            payload = (json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else data).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'")
            self.end_headers()
            self.wfile.write(payload)

        def _authorized(self) -> bool:
            return self.headers.get("X-UI-Token") == token

        def do_GET(self) -> None:
            if not self._local_host():
                self._send(403, {"error": "This UI is available only on localhost"})
                return
            path = urlsplit(self.path)
            if path.path == "/favicon.ico":
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if path.path == "/":
                self._send(200, PAGE.replace("__TOKEN__", token), "text/html")
                return
            if not self._authorized():
                self._send(403, {"error": "Open this UI from its localhost page"})
                return
            if path.path == "/api/current":
                current = manager.current()
                self._send(200, {"id": current.id if current else None, "running": current.snapshot()["running"] if current else False})
                return
            args = parse_qs(path.query)
            try:
                job = manager.get(args.get("id", [""])[0])
                if path.path == "/api/state":
                    self._send(200, job.snapshot())
                elif path.path == "/api/file":
                    name = args.get("path", [""])[0]
                    target = (job.workspace / name).resolve()
                    if name not in job.files() or not target.is_relative_to(job.workspace.resolve()) or not target.is_file():
                        raise ValueError("Invalid output file")
                    if target.stat().st_size > MAX_PREVIEW:
                        raise ValueError("File is too large to preview")
                    self._send(200, {"content": target.read_text(encoding="utf-8", errors="replace")})
                else:
                    self._send(404, {"error": "Not found"})
            except ValueError as exc:
                self._send(400, {"error": str(exc)})

        def do_POST(self) -> None:
            if not self._local_host():
                self._send(403, {"error": "This UI is available only on localhost"})
                return
            if not self._authorized():
                self._send(403, {"error": "Open this UI from its localhost page"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY:
                    raise ValueError("Invalid request size")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Expected a JSON object")
                if self.path == "/api/start":
                    job = manager.start(str(body.get("task", "")), str(body.get("backend", "")), body.get("no_questions", False), body.get("web_app", False))
                    self._send(200, {"id": job.id})
                elif self.path == "/api/answer":
                    answer = body.get("answer", "")
                    if not isinstance(answer, str) or len(answer) > 3000:
                        raise ValueError("Answer is too long")
                    manager.get(str(body.get("id", ""))).answer(answer)
                    self._send(200, {"ok": True})
                else:
                    self._send(404, {"error": "Not found"})
            except (ValueError, json.JSONDecodeError) as exc:
                self._send(400, {"error": str(exc)})

        def log_message(self, fmt: str, *args: object) -> None:
            pass

    server = LocalServer((host, port), Handler)
    server.manager = manager
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = create_server(port=args.port)
    print(f"Open http://127.0.0.1:{server.server_address[1]}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
