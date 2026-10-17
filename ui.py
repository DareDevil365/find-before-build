"""Local browser interface for the Find Before Build harness.

Run ``python ui.py`` and open the printed localhost URL. The UI starts the real
harness in an isolated workspace and relays its clarification questions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import json
from pathlib import Path
import secrets
import subprocess
import sys
import threading
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parent
MAX_BODY = 20_000
MAX_PREVIEW = 200_000


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
                "pending_question": self.pending_question,
                "lines": self.lines[-400:],
                "result": self.result,
                "files": self.files(),
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
        self.lock = threading.Lock()

    def start(self, task: str, backend: str) -> Job:
        task = task.strip()
        if not 3 <= len(task) <= 6000:
            raise ValueError("Enter a request between 3 and 6,000 characters")
        if backend not in {"koboldcpp", "gemma-api"}:
            raise ValueError("Choose KoboldCpp or Gemma API")
        with self.lock:
            if any(job.snapshot()["running"] for job in self.jobs.values()):
                raise ValueError("A build is already running. Finish it before starting another.")
            job_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)
            run_dir = self.root / "runs" / ("ui-" + job_id)
            workspace = run_dir / "workspace"
            workspace.mkdir(parents=True)
            job = Job(job_id, task, backend, workspace, run_dir / "record.json")
            self.jobs[job_id] = job

        command = [
            sys.executable, "-u", str(self.root / "harness.py"), task,
            "--workspace", str(workspace), "--backend", backend,
            "--engine", "native", "--clarify", "--question-channel", "json",
            "--no-commands", "--record", str(job.record),
        ]
        if backend == "gemma-api":
            command += ["--model", "gemma-4-26b-a4b-it"]
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

    @staticmethod
    def _read(job: Job) -> None:
        assert job.process is not None and job.process.stdout is not None
        try:
            for line in job.process.stdout:
                line = line.rstrip("\r\n")
                if line.startswith("@@QUESTION:"):
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


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Find Before Build</title><style>
:root{color-scheme:dark;font:16px/1.45 system-ui,sans-serif;background:#111827;color:#f9fafb}
body{max-width:1000px;margin:0 auto;padding:28px 18px}h1{margin-bottom:5px}p{color:#bac4d2}
section{background:#1f2937;border:1px solid #374151;border-radius:12px;padding:18px;margin:16px 0}
textarea,input,select,button{box-sizing:border-box;font:inherit;border-radius:7px;padding:10px;border:1px solid #4b5563}
textarea,input,select{color:#fff;background:#111827}textarea{width:100%;min-height:120px;resize:vertical}
button{background:#4f46e5;color:white;border:0;cursor:pointer}button:disabled{opacity:.5;cursor:default}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.row input{flex:1;min-width:180px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#0b1220;padding:15px;border-radius:8px;max-height:390px;overflow:auto}
ul{padding-left:22px}li{margin:4px 0}a{color:#93c5fd}.hidden{display:none}
#status{font-weight:700}#questionText{color:#fff}
</style></head><body>
<h1>Find Before Build</h1><p>Describe what you want. The harness clarifies the request, searches GitHub for reusable work, then asks Gemma or your local model to build it.</p>
<section><label for="task">Your request</label><textarea id="task" placeholder="Build a parcel courier price comparison tool for India..."></textarea>
<div class="row"><label for="backend">Model</label><select id="backend"><option value="koboldcpp">Local model (KoboldCpp)</option><option value="gemma-api">Gemma 4 API</option></select><button id="start">Find and build</button></div></section>
<section id="question" class="hidden"><h2>Clarification</h2><p id="questionText"></p><div class="row"><input id="answer" placeholder="Your answer"><button id="sendAnswer">Continue</button></div></section>
<section><h2>Progress</h2><div id="status">Ready</div><pre id="logs">Your build log will appear here.</pre></section>
<section><h2>Output files</h2><ul id="files"><li>Start a build to see files.</li></ul><h3>File preview</h3><pre id="preview">Select a file.</pre></section>
<script>
const token='__TOKEN__';let job=null;let busy=false;let lastQuestion=null;
const $=id=>document.getElementById(id);
async function api(path,options={}){let r=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-UI-Token':token,...options.headers}});let j=await r.json();if(!r.ok)throw Error(j.error||'Request failed');return j}
async function start(){if(busy)return;try{let j=await api('/api/start',{method:'POST',body:JSON.stringify({task:$('task').value,backend:$('backend').value})});job=j.id;busy=true;$('start').disabled=true;$('status').textContent='Starting...';$('logs').textContent='';$('files').replaceChildren();poll()}catch(e){$('status').textContent=e.message}}
async function sendAnswer(){try{await api('/api/answer',{method:'POST',body:JSON.stringify({id:job,answer:$('answer').value})});$('answer').value='';$('question').classList.add('hidden');lastQuestion=null}catch(e){$('status').textContent=e.message}}
async function poll(){if(!job)return;try{let s=await api('/api/state?id='+encodeURIComponent(job));let label=s.running?'Running':(s.result?.status|| (s.error?'Error':'Exited '+s.exit_code));$('status').textContent=label+(s.error?' - '+s.error:'');$('logs').textContent=s.lines.join('\n');$('logs').scrollTop=$('logs').scrollHeight;
if(s.pending_question){$('question').classList.remove('hidden');$('questionText').textContent=s.pending_question;if(lastQuestion!==s.pending_question){$('answer').focus();lastQuestion=s.pending_question}}else{$('question').classList.add('hidden');lastQuestion=null}
let list=$('files');list.replaceChildren();for(let name of s.files){let li=document.createElement('li'),a=document.createElement('a');a.href='#';a.textContent=name;a.onclick=async e=>{e.preventDefault();try{let f=await api('/api/file?id='+encodeURIComponent(job)+'&path='+encodeURIComponent(name));$('preview').textContent=f.content}catch(err){$('preview').textContent=err.message}};li.append(a);list.append(li)}
if(!s.running){busy=false;$('start').disabled=false}}catch(e){$('status').textContent=e.message}if(busy)setTimeout(poll,1000)}
$('start').onclick=start;$('sendAnswer').onclick=sendAnswer;$('answer').onkeydown=e=>{if(e.key==='Enter')sendAnswer()};
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
            if path.path == "/":
                self._send(200, PAGE.replace("__TOKEN__", token), "text/html")
                return
            if not self._authorized():
                self._send(403, {"error": "Open this UI from its localhost page"})
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
                    job = manager.start(str(body.get("task", "")), str(body.get("backend", "")))
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

    return ThreadingHTTPServer((host, port), Handler)


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
