# Find Before Build

A small coding harness that investigates reusable GitHub projects before asking an AI model to build a new one. It shows a sourced decision, then hands off editing to Aider or the native bounded tool loop in a selected workspace. The local path uses an open-weight model through KoboldCpp; a second adapter calls Gemma 4 through the Gemini API.

This is a hackathon prototype. Search and scoring are implemented, but a useful reuse decision still depends on the task and available evidence. No speed or quality improvement is claimed without a measured comparison.

Observed so far: the official `skills-ref` validator accepted the skill; live GitHub search and scoring ran; 16 unit tests pass (one Windows symlink case skipped). In a small local Hermes smoke task, the harness wrote `hello.py` and `README.md` in three model calls, 24.09 seconds, and 2,231 reported tokens after a retry. An earlier no-retry attempt produced no files. A full discovery-to-code run with Gemma 4 completed and passed an independent behavior check; details below.

Aider 0.86.2 completed focused edits through local KoboldCpp and passed independent assertions. It sometimes fails to apply all edits on multi-file tasks, so automatic engine selection uses it for one explicitly named file and uses the native tool loop for larger tasks. Aider remains available explicitly with `--engine aider`. The harness reports incomplete Aider edits instead of treating its exit code alone as success.

## Quick start

Requirements: Python 3.11+, a running KoboldCpp server with a chat-capable model at `http://127.0.0.1:5001`, and network access to GitHub for fresh discovery. The native Python runtime uses only the standard library. `GITHUB_TOKEN` is optional and raises GitHub's API allowance. Keep tokens and prompts out of committed files.

To prepare the Aider engine, install it in a separate Python 3.12 environment (with [uv](https://docs.astral.sh/uv/)):

```powershell
uv venv .cache/aider-venv --python 3.12
uv pip install -r requirements-aider.txt --python .cache/aider-venv/Scripts/python.exe
```

The observed setup installed Aider 0.86.2, pinned in `requirements-aider.txt`. Its [official installation guide](https://aider.chat/docs/install.html) also uses an isolated Python 3.12 environment. `--engine auto` is the default; it uses Aider for one named file when installed and the native loop otherwise. Set `--engine aider` or `--engine native` to choose explicitly. Use `--aider-path PATH` for a separate Aider installation.

```powershell
cd path\to\Hack
python harness.py "Build a CSV cleaner" --workspace .\demo-workspace --record .\runs\demo.json
```

The harness prints its reuse decision before any coding write. The Aider path chooses up to eight target filenames from the task, or existing top-level files, or `main.py` and `README.md` (`index.html` for a website). It seeds missing targets empty after the decision so unchanged placeholders do not count as model edits. This narrow file plan may miss needed modules; name intended files in the task. The Aider prompt receives compact candidate names, scores, licenses, and flags. Source URLs remain in the console and run record; they are omitted from Aider's edit prompt because one exploratory run triggered URL scraping and optional Playwright setup instead of editing. URL detection and Playwright are disabled in the wrapper. The wrapper also disables Aider's Git integration, automatic test/lint, and shell suggestions, and stores chat/input histories in ignored `.cache/aider-history/` instead of the target workspace. It instructs the model not to run commands. Aider does not use the native per-command confirmation boundary. With `--engine native`, the model may request `list_files`, `read_file`, `write_file`, or `run_command`; the latter prints an exact command and asks `Run it? [y/N]` before execution. `--no-commands` denies native model-requested commands. The selected workspace is created if absent, so choose a dedicated directory for a first run.

Other useful options:

```powershell
python harness.py --help
python harness.py "Build a CSV cleaner" --workspace .\demo-workspace --offline
python harness.py "Build a CSV cleaner" --workspace .\demo-workspace --engine native --backend koboldcpp --base-url http://127.0.0.1:5001 --max-turns 8
```

`--offline` uses only a previously cached GitHub result. `--no-discovery` exists for the evaluation control arm. Aider asks KoboldCpp's `/v1/models` endpoint for the loaded model ID when `--model` is omitted; to use a different GGUF, load it in KoboldCpp itself first. On the development machine, Hermes 3 8B was loaded and a Qwen 3.5 4B GGUF was present but untested.

You may put `GITHUB_TOKEN`, `GEMINI_API_KEY`, or `GOOGLE_API_KEY` in this project's `.env` file instead of setting environment variables in every shell. The loader accepts only those three names and does not replace values already set in the environment. `.env` is git-ignored; keep it private and do not put tokens in task prompts or run records.

## Gemma 4 backend

Set `GEMINI_API_KEY` or `GOOGLE_API_KEY` in your environment, then run:

```powershell
python harness.py "Build a CSV cleaner" --workspace .\gemma-demo --backend gemma-api --model gemma-4-26b-a4b-it --record .\runs\gemma.json
```

The adapter uses Google's `generateContent` REST API and normalizes text, function calls, and reported token counts. It was implemented from the [Gemma on Gemini API documentation](https://ai.google.dev/gemma/docs/core/gemma_on_gemini_api). A live full run with `gemma-4-26b-a4b-it`, `--engine native`, `--no-commands`, and `--max-turns 8` searched GitHub, scored candidates, and displayed a `build_fresh` decision before code. Gemma wrote `calculator.py` and `README.md`; the harness finished with exit 0 in 29.23 seconds, four model calls, 4,411 reported tokens, and 16 added lines. An independent `add(2, 3) == 5` assertion passed. The record is under `runs/gemma-full.json` (ignored by Git). This verifies the full API, discovery, decision, and file-write path for one small task; it does not establish a speed or quality advantage over the control arm.

## Reusable skill

The standalone workflow lives in [`find-before-build/SKILL.md`](find-before-build/SKILL.md). Normal harness runs load its instructions into the decision model prompt; the scripts perform the actual retrieval and scoring. They can also be used directly:

```powershell
python find-before-build\scripts\search_repos.py --query "python csv cleaner" --query "csv normalize cli" --max-results 5 --output candidates.json
python find-before-build\scripts\score_repo.py --input candidates.json --task "Build a CSV cleaner" --output scored.json
```

Search stores timestamped evidence in a local cache and never runs candidate code. Scoring gives reasons and flags for license, recency, README fit, issue health, and stars. The resulting score ranks candidates; it does not prove a project works or that integrating it will be cheap. The [Agent Skills specification](https://agentskills.io/specification) defines the skill format.

## Paired evaluation

Create `task.json` in this folder:

```json
{
  "task": "Build a CSV cleaner CLI in Python",
  "seed_files": {},
  "checks": [["python", "-m", "compileall", "-q", "."]]
}
```

Then run:

```powershell
python eval.py task.json --backend koboldcpp
```

The runner creates fresh baseline and discovery workspaces, uses the same task, model, seed files, and checks, and saves records plus `report.json` under `runs/eval-*`. Discovery adds repository evidence and a reuse decision by design. It disables model-requested commands, then runs the acceptance commands from the task spec. `time_to_working_seconds` includes harness time and acceptance checks only when all checks pass; it is `null` on failure. Aider model-call and token counts are parsed from its console output and may be rounded. The compilation example is only a smoke check; replace it with task-specific behavior checks before interpreting a result. Baseline always runs first, and live API conditions may differ between arms. Report failures and unavailable metrics rather than estimating savings.

An actual paired run on 17 October 2026 used `gemma-4-26b-a4b-it` through the Gemini API with the native engine. The task asked for `calculator.py` with `add(a, b)` and a usage example in `README.md`. Both arms passed behavior and README checks. The discovery arm searched five live GitHub candidates, then correctly chose `build_fresh` because extracting a trivial function would cost more than writing it.

| Arm | Acceptance | Time to working result | API tokens reported | Files changed | Added lines |
| --- | --- | ---: | ---: | ---: | ---: |
| No discovery | Pass | 11.410 s | 1,965 | 2 | 15 |
| Find before build | Pass | 28.618 s | 4,390 | 2 | 13 |

The measured result is overhead for this small task, with no acceptance gain. It demonstrates that the harness can reject unnecessary reuse and carry the decision into code, but it does **not** show a speedup. The shareable, path-free result is [`results/gemma-calculator-summary.json`](results/gemma-calculator-summary.json), using [`examples/calculator.eval.json`](examples/calculator.eval.json) as the task spec. The detailed local report remains ignored at `runs/eval-20261017T132317Z/report.json`. This is one baseline-first comparison; live network and API variation prevent broader conclusions.

## Project map

`harness.py` owns the stage sequence and record. Auto mode selects Aider for focused one-file edits and the native tool loop for larger tasks; the native path uses `harness_core/backends.py` for KoboldCpp or Gemini and `harness_core/workspace.py` for allowed tools. `find-before-build/scripts/` retrieves and scores repository evidence. `eval.py` runs the paired comparison with the same engine in both arms. [PROJECT_GUIDE.md](PROJECT_GUIDE.md) records architecture, setup, rationale, validation, and known limits; [ARCHITECTURE.md](ARCHITECTURE.md) gives component contracts, and [idea.md](idea.md) gives the product concept.

Images are outside this project scope. The repository is licensed under [MIT](LICENSE); candidate repositories keep their own licenses.
