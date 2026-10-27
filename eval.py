"""Run a reproducible, task-defined two-arm harness evaluation.

The same task and seed files run in separate workspaces. One arm uses repository
discovery; the other passes --no-discovery. Acceptance checks come from the
task spec and run after generation in each arm's workspace.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parent


def _relative_path(value: str) -> Path:
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts or path == Path("."):
        raise ValueError(f"Seed file path must be relative and stay in the workspace: {value!r}")
    return path


def load_spec(path: Path) -> dict:
    spec = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict) or not isinstance(spec.get("task"), str) or not spec["task"].strip():
        raise ValueError("Spec needs a nonempty task string")
    checks = spec.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ValueError("Spec needs at least one acceptance check")
    for command in checks:
        if not isinstance(command, list) or not command or any(not isinstance(arg, str) or not arg for arg in command):
            raise ValueError("Each acceptance check must be a nonempty JSON argv array")
    seeds = spec.get("seed_files", {})
    if not isinstance(seeds, dict):
        raise ValueError("seed_files must map relative paths to UTF-8 text")
    for name, content in seeds.items():
        _relative_path(name)
        if not isinstance(content, str):
            raise ValueError("Every seed file must contain UTF-8 text")
    return spec


def prepare_workspace(path: Path, seed_files: dict[str, str]) -> None:
    if path.exists():
        raise FileExistsError(f"Evaluation workspace already exists: {path}")
    path.mkdir(parents=True)
    for name, content in seed_files.items():
        target = (path / _relative_path(name)).resolve()
        if not target.is_relative_to(path.resolve()):
            raise ValueError(f"Seed file escapes workspace: {name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def run_check(argv: list[str], workspace: Path, timeout: int) -> dict:
    started = time.monotonic()
    try:
        result = subprocess.run(argv, cwd=workspace, capture_output=True, text=True, timeout=timeout, shell=False, errors="replace")
        return {
            "argv": argv,
            "passed": result.returncode == 0,
            "exit_code": result.returncode,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "stdout": result.stdout[-4000:],
            "stderr": result.stderr[-4000:],
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"argv": argv, "passed": False, "exit_code": None, "elapsed_seconds": round(time.monotonic() - started, 3), "error": str(exc)}


def _write_count(record: dict) -> int:
    files = record.get("written_files")
    if isinstance(files, list):
        return len(files)
    return sum(1 for item in record.get("tool_log", []) if item.get("tool") == "write_file" and item.get("status") == "ok")


def run_arm(*, label: str, spec: dict, workspace: Path, output: Path, backend: str, engine: str, model: str | None, max_turns: int, timeout: int) -> dict:
    workspace, output = workspace.resolve(), output.resolve()
    prepare_workspace(workspace, spec.get("seed_files", {}))
    record_path = output / f"{label}-record.json"
    argv = [sys.executable, str(ROOT / "harness.py"), spec["task"], "--workspace", str(workspace), "--backend", backend, "--engine", engine, "--no-commands", "--no-questions", "--record", str(record_path), "--max-turns", str(max_turns)]
    if model:
        argv.extend(["--model", model])
    if label == "baseline":
        argv.append("--no-discovery")
    started = time.monotonic()
    try:
        result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=timeout, shell=False, errors="replace")
        harness_exit = result.returncode
        stdout, stderr = result.stdout[-6000:], result.stderr[-6000:]
    except (OSError, subprocess.TimeoutExpired) as exc:
        harness_exit, stdout, stderr = None, "", str(exc)
    wall_seconds = round(time.monotonic() - started, 3)
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        record = {}
    checks = [run_check(command, workspace, int(spec.get("check_timeout_seconds", 60))) for command in spec["checks"]]
    usage = record.get("token_usage") or {}
    acceptance_passed = harness_exit == 0 and all(check["passed"] for check in checks)
    return {
        "label": label,
        "workspace": str(workspace),
        "harness_exit_code": harness_exit,
        "harness_stdout": stdout,
        "harness_stderr": stderr,
        "wall_seconds": wall_seconds,
        "reported_model_seconds": record.get("elapsed_seconds"),
        "effective_engine": record.get("engine"),
        "model_calls": record.get("model_calls"),
        "token_usage": usage,
        "write_count": _write_count(record),
        "model_written_lines": record.get("model_written_lines"),
        "acceptance_passed": acceptance_passed,
        "time_to_working_seconds": round(wall_seconds + sum(check["elapsed_seconds"] for check in checks), 3) if acceptance_passed else None,
        "checks": checks,
        "record_path": str(record_path) if record else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path, help="JSON task spec: task, checks (argv arrays), optional seed_files")
    parser.add_argument("--backend", choices=("koboldcpp", "gemma-api"), default="koboldcpp")
    parser.add_argument("--engine", choices=("auto", "native", "aider"), default="auto", help="Coding executor; both arms use the same engine selection")
    parser.add_argument("--model", help="Use the same model for both arms")
    parser.add_argument("--output", type=Path, help="New evaluation directory (default: runs/eval-UTC timestamp)")
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--timeout", type=int, default=600, help="Maximum harness seconds per arm")
    args = parser.parse_args()
    try:
        spec = load_spec(args.spec)
        if args.max_turns < 1 or args.timeout < 1:
            raise ValueError("--max-turns and --timeout must be positive")
        output = (args.output or ROOT / "runs" / ("eval-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))).resolve()
        if output.exists():
            raise FileExistsError(f"Output directory already exists: {output}")
        output.mkdir(parents=True)
        (output / "task-spec.json").write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        arms = []
        for label in ("baseline", "discovery"):
            print(f"Running {label} arm...", flush=True)
            arms.append(run_arm(label=label, spec=spec, workspace=output / label, output=output, backend=args.backend, engine=args.engine, model=args.model, max_turns=args.max_turns, timeout=args.timeout))
        report = {
            "task": spec["task"],
            "backend": args.backend,
            "engine": args.engine,
            "model": args.model,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "method": "Same task, model, seed files and acceptance checks; separate workspaces; baseline first. Discovery arm adds repository evidence and a reuse decision. Model commands disabled; checks run externally after each arm.",
            "arms": arms,
            "limitations": ["Two runs are exploratory, not a statistical result.", "Baseline-first order and live GitHub/API latency can affect timing.", "Token usage depends on backend reporting."],
        }
        report_path = output / "report.json"
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        for arm in arms:
            print(f"{arm['label']}: acceptance={arm['acceptance_passed']} wall={arm['wall_seconds']}s tokens={arm['token_usage'].get('total_tokens', 'unknown')} writes={arm['write_count']}")
        print(f"Report: {report_path}")
        return 0 if all(arm["acceptance_passed"] for arm in arms) else 1
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"eval: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
