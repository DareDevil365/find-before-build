# Find Before Build

A small coding harness that investigates reusable GitHub projects before asking an AI model to build a new one. It shows a sourced decision, then hands off editing to Aider or the native bounded tool loop in a selected workspace. The local path uses an open-weight model through KoboldCpp; a second adapter calls Gemma 4 through the Gemini API.

This is a hackathon prototype. Search and scoring are implemented, but a useful reuse decision still depends on the task and available evidence. No speed or quality improvement is claimed without a measured comparison.

A [recorded Gemini 2.5 Flash browser demo](demo%27s%20outputs/working-gemini-2.5-flash/demo.webm) shows the harness finding candidates, choosing `build_fresh` for a small counter app, writing the [generated page](demo%27s%20outputs/working-gemini-2.5-flash/index.html), and clicking its buttons. See the [demo notes](demo%27s%20outputs/README.md) for the measured run and limits.

The [study-buddy example](demo%27s%20outputs/study-buddy-web/PROJECT_GUIDE.md) shows a browser adapter around a Gemini-generated math quiz. Its original model output was terminal-only, so the adapter and Gemini chat endpoint were added afterward; the guide distinguishes those parts.

Observed so far: the official `skills-ref` validator accepted the skill; live GitHub search and scoring ran; the standard-library test suite passes with one Windows symlink case skipped. In a small local Hermes smoke task, the harness wrote `hello.py` and `README.md` in three model calls, 24.09 seconds, and 2,231 reported tokens after a retry. An earlier no-retry attempt produced no files. A full discovery-to-code run with Gemma 4 completed and passed an independent behavior check; details below.

Aider 0.86.2 completed focused edits through local KoboldCpp and passed independent assertions. It sometimes fails to apply all edits on multi-file tasks, so automatic engine selection uses it for one explicitly named file and uses the native tool loop for larger tasks. Aider remains available explicitly with `--engine aider`. The harness reports incomplete Aider edits instead of treating its exit code alone as success.

## Quick start

For the browser interface, run `python ui.py` and open `http://127.0.0.1:8765/`. Enter a build request, choose Gemini Flash (the default), Gemma 4, or the local model, answer clarification questions when they appear, and inspect the live stage, GitHub candidates, reuse decision, activity log, and output files. The Gemini Flash option uses `gemini-2.5-flash` through the same API adapter and is labelled separately from Gemma in demos. The default Web app checkbox adds an explicit browser delivery requirement; uncheck it for CLI or other projects. Fast mode skips model clarification questions and uses sensible defaults. Otherwise, each clarification pauses the build until answered, and the UI now labels that wait clearly. A green repository card means the decision selected that source; "Source loaded" means it was cloned for inspection, not necessarily imported into the output. The UI starts the same harness in a fresh workspace under ignored `runs/ui-*`; it allows one active build and disables model-requested shell commands. It binds to localhost, checks the request host and a per-process token, and reconnects to an active job after a page refresh. Completed runs open through View last build. Generated static pages and recognized Flask, FastAPI, Vite, Next.js, or npm apps get localhost links in Build artifacts. Files and records remain on disk afterward.

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

The harness prints its reuse decision before any coding write. Use `--clarify` to let the chosen model ask up to three material questions before discovery; the native loop may also call `ask_user` while coding. Use `--no-questions` for unattended runs. The Aider path chooses up to eight target filenames from the task, or existing top-level files, or `main.py` and `README.md` (`index.html` for a website). It seeds missing targets empty after the decision so unchanged placeholders do not count as model edits. This narrow file plan may miss needed modules; name intended files in the task. The Aider prompt receives compact candidate names, scores, licenses, and flags. Source URLs remain in the console and run record; they are omitted from Aider's edit prompt because one exploratory run triggered URL scraping and optional Playwright setup instead of editing. URL detection and Playwright are disabled in the wrapper. The wrapper also disables Aider's Git integration, automatic test/lint, and shell suggestions, and stores chat/input histories in ignored `.cache/aider-history/` instead of the target workspace. It instructs the model not to run commands. Aider does not use the native per-command confirmation boundary. With `--engine native`, the model can inspect and import files from selected repositories. `run_command` asks `Run it? [y/N]` before execution; `--no-commands` denies it. The selected workspace is created if absent, so choose a dedicated directory for a first run.

Other useful options:

```powershell
python harness.py --help
python harness.py "Build a CSV cleaner" --workspace .\demo-workspace --offline
python harness.py "Build a CSV cleaner" --workspace .\demo-workspace --engine native --backend koboldcpp --base-url http://127.0.0.1:5001 --max-turns 8
```

`--offline` uses only a previously cached GitHub result. `--no-discovery` exists for the evaluation control arm. Aider asks KoboldCpp's `/v1/models` endpoint for the loaded model ID when `--model` is omitted; to use a different GGUF, load it in KoboldCpp itself first. On the development machine, Hermes 3 8B was loaded and a Qwen 3.5 4B GGUF was present but untested.

You may put `GITHUB_TOKEN`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`, or numbered `GEMINI_API_KEY_N` names in this project's `.env` file instead of setting environment variables in every shell. The loader accepts only those credential-name patterns and does not replace values already set in the environment. `.env` is git-ignored; keep it private and do not put tokens in task prompts or run records.

For parallel Gemma runs, `.env` can also contain `GEMINI_API_KEY_1` and `GEMINI_API_KEY_2`. Start separate workspaces with `--api-key-env GEMINI_API_KEY_1` and `--api-key-env GEMINI_API_KEY_2`; the option takes the environment variable **name**, never its value. Without the option, the harness uses the first available supported key. API quotas and latency may still limit concurrent requests.

## Gemma 4 backend

Set `GEMINI_API_KEY` or `GOOGLE_API_KEY` in your environment, then run:

```powershell
python harness.py "Build a CSV cleaner" --workspace .\gemma-demo --backend gemma-api --model gemma-4-26b-a4b-it --record .\runs\gemma.json
```

The adapter uses Google's `generateContent` REST API and normalizes text, function calls, and reported token counts. It was implemented from the [Gemma on Gemini API documentation](https://ai.google.dev/gemma/docs/core/gemma_on_gemini_api). A live full run with `gemma-4-26b-a4b-it`, `--engine native`, `--no-commands`, and `--max-turns 8` searched GitHub, scored candidates, and displayed a `build_fresh` decision before code. Gemma wrote `calculator.py` and `README.md`; the harness finished with exit 0 in 29.23 seconds, four model calls, 4,411 reported tokens, and 16 added lines. An independent `add(2, 3) == 5` assertion passed. The record is under `runs/gemma-full.json` (ignored by Git). This verifies the full API, discovery, decision, and file-write path for one small task; it does not establish a speed or quality advantage over the control arm.

The same REST adapter can call a Gemini model by passing its model ID, for example `--model gemini-2.5-flash`. It preserves function-call thought signatures in later requests as required by Google's [thought-signature documentation](https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures). Gemini and Gemma runs are distinct evaluations, not interchangeable speed measurements.

## Reusable skill

The standalone workflow lives in [`find-before-build/SKILL.md`](find-before-build/SKILL.md). Normal harness runs load its instructions into the decision model prompt; the scripts perform the actual retrieval and scoring. They can also be used directly:

```powershell
python find-before-build\scripts\search_repos.py --query "python csv cleaner" --query "csv normalize cli" --max-results 5 --output candidates.json
python find-before-build\scripts\score_repo.py --input candidates.json --task "Build a CSV cleaner" --output scored.json
```

Search stores timestamped evidence in a local cache and never runs candidate code. A model search plan breaks a build request into capabilities and two or three repository queries; a deterministic fallback keeps the search bounded if the plan is invalid. Scoring gives reasons and flags for license, recency, README fit, issue health, and stars. The resulting score ranks candidates; it does not prove a project works or that integrating it will be cheap. The [Agent Skills specification](https://agentskills.io/specification) defines the skill format.

The model can select a dependency, fork, reusable approach, or a combination only from shortlist entries whose license and reported size allow source inspection. Search records which query matched each candidate. The coding loop requires an implementation file for build requests, checks generated Python/JSON syntax and local HTML asset references, and labels incomplete output `partial_edit`. These structural checks cannot prove the requested behavior works; use the acceptance commands in `eval.py` or manual browser checks for that.

When the decision selects one or more repositories, the native engine shallow-clones up to three verified permissively licensed shortlist entries into ignored `.cache/repo-clones/`. The model can list and read their source files, then import a small file into its workspace for editing. Import writes `THIRD_PARTY_SOURCES.md` and a copy of the source license; cloned code is never executed automatically. This enables component reuse and a `combine` decision, but it does not guarantee that independently written projects will integrate correctly. An explicit `--engine aider` run receives candidate summaries without these source tools. In a live smoke check, the harness pulled `apify/crawlee-python` at a pinned commit for inspection; no integration result is claimed from that clone alone.

The [four-request discovery audit](results/discovery-audit.json) compared actual search metadata for a CSV cleaner, PDF merger, Markdown-to-slides converter, and courier comparison. It exposed missed component searches and an unrelated popular scraper outranking a courier component. The planner now keeps relevant domain queries, and scoring caps repositories with no task-domain overlap. When GitHub rate limits README and issue checks, the result is labelled `metadata_only`; missing maintenance evidence is not treated as proof of quality. Candidate repositories still need source inspection and task-specific tests. If a task includes an exact official HTTPS page, the native model can call `inspect_url` to read bounded HTML table structure; it cannot follow redirects or inspect arbitrary model-supplied URLs. A complete courier-price scraper has not passed end-to-end checks, so this audit shows improved discovery rather than a finished courier app.

The [cross-domain audit](results/cross-domain-audit.json) records live search results for a calculator web app, a platform game, and an AI study buddy. It identifies plausible component repositories and the build failures that led to the completion checks. These candidates are not claimed as working integrations.

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

`harness.py` owns the stage sequence and record. `ui.py` wraps it in a local browser interface and relays questions through the harness's JSON question channel. `harness_core/clarify.py` handles prebuild questions; `harness_core/reuse.py` clones eligible selected repositories for source inspection. Auto mode selects Aider for focused one-file edits and the native tool loop for larger tasks; the native path uses `harness_core/backends.py` for KoboldCpp or Gemini and `harness_core/workspace.py` for allowed tools. `find-before-build/scripts/` retrieves and scores repository evidence. `eval.py` runs the paired comparison with the same engine in both arms. [PROJECT_GUIDE.md](PROJECT_GUIDE.md) records architecture, setup, rationale, validation, and known limits; [ARCHITECTURE.md](ARCHITECTURE.md) gives component contracts, and [idea.md](idea.md) gives the product concept.

Images are outside this project scope. The repository is licensed under [MIT](LICENSE); candidate repositories keep their own licenses.
