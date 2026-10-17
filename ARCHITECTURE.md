# Architecture: Find Before Build

This document describes the intended workflow and component contracts. [PROJECT_GUIDE.md](PROJECT_GUIDE.md) distinguishes tested behavior from work still in progress.

## Goal

Given “build a CSV cleaner,” the controller gathers a few relevant GitHub repositories, shows a sourced reuse decision, and then lets a model build and check a result in a chosen workspace. The judge should see the evidence, decision, file changes, and check outcome in one run.

```text
request + workspace
        |
        v
Python controller ----> find-before-build skill
        |                 search -> cache -> score
        v
visible reuse decision
        |
        v
model adapter (KoboldCpp or Gemma API)
        |
        v
coding engine: auto chooses Aider for one named file, native loop otherwise
        |
        v
run record + acceptance result
```

The controller owns stage gates and starts the coding engine only after printing the decision. Search scripts own factual repository retrieval and repeatable scoring. The model interprets task fit, integration effort, and code changes; its confidence is no substitute for evidence or a passing check. Auto mode uses Aider for a single named file when installed and the native loop otherwise. This choice reflects passing focused Aider edits and an integrated multi-file Aider failure. Either engine can be selected explicitly.

## Request flow

| Stage | Behavior | Visible result |
| --- | --- | --- |
| Prepare | Validate request, workspace, backend, and run limits | Configuration or actionable error |
| Discover | Generate 2–3 focused queries; fetch a bounded candidate set | Timestamped evidence and source URLs |
| Decide | Score candidates; assess reuse versus integration cost | `use_dependency`, `fork`, `borrow_approach`, or `build_fresh` |
| Gate | Show decision before code is written | Reasons, unknowns, and links |
| Implement | Run a bounded model/tool loop inside the workspace | File and tool trace |
| Verify | Run the configured acceptance check | Pass, fail, or unverified |
| Report | Aggregate timings, usage if provided, and changes | Human report and machine-readable record |

Discovery is required for a normal build. The evaluation runner may disable discovery for its control run. On a network failure, a timestamped cache may supply labelled evidence; otherwise the harness must surface the failure and avoid a fabricated recommendation.

## Portable discovery skill

`find-before-build/SKILL.md` uses the [Agent Skills specification](https://agentskills.io/specification), including `name` and `description` YAML frontmatter. The controller reads this file on discovery runs, checks the expected name metadata, and includes its instructions in the model's decision prompt. `scripts/search_repos.py` uses the GitHub API and optional `GITHUB_TOKEN`; it does not clone or run candidates. It records missing fields and retrieval times. `scripts/score_repo.py` applies the initial 100-point weights: license 25, recency 25, README fit 20, issue health 15, stars 15. Scores rank candidates but cannot resolve task fit or integration cost alone. A missing license is unknown, never silently permissive.

A decision should include a chosen action, repository if applicable, evidence URLs, reasons, integration steps, unknowns, and effort estimate. Only repositories present in the evidence set are eligible. Invalid model output should be retried within a fixed budget and then reported as a failure.

## Model adapters

`harness_core/backends.py` normalizes text, tool calls, and available usage counts. KoboldCpp uses its OpenAI-compatible `/v1/chat/completions` endpoint at the configured local URL. The loaded Hermes model completed a small native coding task after a retry. Gemma 4 uses Google's Gemini API and an API key; the `gemma-4-26b-a4b-it` route completed a live discovery-to-code run and a passing paired evaluation. Official docs also list `gemma-4-31b-it`: [Gemma through Gemini API](https://ai.google.dev/gemma/docs/core/gemma_on_gemini_api).

The user's prompt, README content, and tool results are untrusted data. They cannot expand the tool allowlist or workspace boundary. Tool arguments are validated before use; outputs, turns, and retries are bounded.

## Coding engines and workspace tools

Aider 0.86.2 is installed in an isolated Python 3.12 environment under `.cache/aider-venv` and made a passing local KoboldCpp edit in a separate test. Its [OpenAI-compatible endpoint support](https://aider.chat/docs/llms/openai-compat.html) fits KoboldCpp, and its [Gemini provider](https://aider.chat/docs/llms/gemini.html) provides a possible Gemma path that has not been live-tested through Aider. The controller passes its evidence and decision to Aider only after discovery. Aider is [Apache-2.0 licensed](https://github.com/Aider-AI/aider); it remains an external dependency, not code copied into this project. Aider edits files directly, outside the native file-tool boundary. The wrapper disables Git integration, automatic test/lint, and shell suggestions; use a dedicated target directory and task-defined checks to inspect the result.

An exploratory Aider discovery run included GitHub URLs in its editing prompt. Aider tried to fetch them and initiate optional Playwright setup, spent 144 seconds, and wrote no files. The wrapper now sends compact candidate name, score, license, and flag summaries to Aider without URLs; it disables URL detection and Playwright. Full source URLs still appear in the discovery console output and JSON record. This keeps repository page retrieval in the explicit GitHub search stage.

For the loaded small local Hermes model, `whole` edit format let a nested README code fence become a stray filename in a two-file integrated run. Two focused two-file edits passed with `diff` format, explicit positional targets, and without `--yes`, but a later integrated run changed only one of two target files despite Aider exiting 0. The controller now reports `partial_edit` when a target remains unchanged. A later one-file CSV task failed with `diff` because the model returned prose instead of SEARCH/REPLACE blocks; a one-file `whole` run created a working `greeting.py`. The wrapper selects `whole` for one file and `diff` for multiple files. It extracts up to eight filenames with common code/document extensions from the task; absent those, it selects existing top-level files or a two-file default. Missing targets are seeded empty after the visible decision and before the edit snapshot. This limits the edit scope and prevents placeholders from counting as model-written files, but the simple filename planner can miss a needed module.

The native loop uses these existing tools:

- `read_file(relative_path)`: bounded text from the selected workspace.
- `write_file(relative_path, content)`: create or replace only a file inside that workspace; reject traversal and symlink escapes.
- `run_command(argv)`: show the exact command and ask for confirmation before execution; return exit code and bounded output.

No downloaded repository code is executed as part of search or scoring. A dependency or fork choice still needs its license and integration work assessed. A check result must come from an actual command, not a model assertion.

## Repository layout

```text
Hack/
  README.md                 setup and demo
  LICENSE                   project license
  idea.md                   product concept and scope
  ARCHITECTURE.md           component contracts
  PROJECT_GUIDE.md          actual state, rationale, changes, limits
  requirements.txt          Python dependency note
  harness.py                CLI and stage controller
  eval.py                   paired comparison runner
  harness_core/             backend, workspace/tool, and decision modules
  find-before-build/
    SKILL.md                portable workflow
    scripts/search_repos.py  GitHub retrieval and cache
    scripts/score_repo.py    deterministic scoring
  tests/                   focused behavior checks
```

The controller and skill use Python's standard library where practical to reduce setup and make the demo easier to inspect. Aider supplies robust edit application in a separately installed environment, while our own code retains the investigation and decision logic. The local KoboldCpp adapter reuses the user's running server; the Gemma adapter covers the challenge API path with the same controller. GitHub API data is structured and linkable, but its unauthenticated rate limit and network availability constrain discovery.

## Evaluation design

Run the same neutral task, model, settings, clean starting state, and acceptance check twice: with discovery and without. The discovery arm deliberately adds repository evidence and a reuse decision to the model context. Record wall time including search, reported tokens if available, model-written lines, tool calls, and acceptance result. `time_to_working_seconds` is harness plus acceptance-check time for a passing arm and `null` on failure. Aider counts come from console output and can be rounded. Each run uses an isolated workspace. A one-task comparison is a demonstration, not proof that discovery generally improves coding. Missing measurements are explicit; failures remain in the report. Do not publish illustrative numbers as measured results.

The first passing paired comparison is [the Gemma calculator result](results/gemma-calculator-summary.json): both arms passed, while discovery took 28.618 seconds to a working result versus 11.410 seconds without discovery. The model chose `build_fresh` after examining five candidates. This small task shows search overhead, not an improvement claim. The reproducible task definition is [examples/calculator.eval.json](examples/calculator.eval.json).

## Local environment and limits

Observed on 17 October 2026: Windows, Python 3.14.3, Git 2.53, 15.7 GB RAM, RTX 5060 Laptop GPU with 8151 MiB VRAM. `F:\koboldcpp.exe` serves `F:\Hermes\Hermes-3-Llama-3.1-8B-Q4_K_M.gguf` on port 5001. `F:\Qwen\Qwen_Qwen3.5-4B-Q5_K_S.gguf` exists but has not been loaded or tested. The local GGUF files' exact provenance is unverified. Search works unauthenticated within GitHub's limits. A live Gemma 4 API run with a locally configured key completed the full discovery, decision, and native file-write path: a calculator task ended `finished` in 29.23 seconds with four model calls and 4,411 reported tokens, and an independent `add(2, 3) == 5` assertion passed. The earlier shorter Gemma smoke run reached its five-turn limit; the full run supersedes it as the demo result.

Aider can be installed with `uv venv .cache/aider-venv --python 3.12` followed by `uv pip install -r requirements-aider.txt --python .cache/aider-venv/Scripts/python.exe`; the requirement pins Aider 0.86.2. The separate environment accommodates Aider's Python support while the controller runs on Python 3.14. The project `.env` loader accepts only `GITHUB_TOKEN`, `GEMINI_API_KEY`, and `GOOGLE_API_KEY`, without replacing existing environment values; `.env` is git-ignored. The other candidate, mini-SWE-agent, has an appealing small MIT-licensed core and a confirmation mode, but its default bash-oriented prompts would need adaptation to Windows command execution. Aider is used for focused edits, while the original native loop provides the reliable multi-file route observed in the Gemma demo.

Images are outside the build scope. Public repository creation and hackathon submission are separate steps after implementation and review.
