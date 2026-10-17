# Find Before Build

A coding harness that checks open-source repositories before asking a model to write a new implementation. It gathers linked evidence, scores candidates transparently, makes a visible reuse decision, and then runs a bounded coding workflow.

The question is simple: **Will reusing an existing project save work for this specific task?** A popular repository can still be the wrong choice if its license, dependencies, or integration work outweigh the benefit.

## Why build it

Coding agents often start generating code without checking whether a maintained implementation already exists. For small local models, unnecessary code costs time, tokens, and debugging effort. Search also has a cost, so the investigation is bounded and its time is recorded. Whether this improves outcomes remains task-dependent: the first paired calculator run passed in both arms and showed search overhead rather than a speedup.

## User experience

For a request such as “build a CSV cleaner,” the harness:

1. Generates a few focused GitHub queries and collects a bounded set of candidates.
2. Records license, recency, README, issue, and popularity evidence with source links and retrieval times.
3. Scores candidates, then chooses `use_dependency`, `fork`, `borrow_approach`, or `build_fresh` for the actual task.
4. Shows the decision before writing code.
5. Uses a local coding model or Gemma 4 through the Gemini API to work inside a selected workspace.
6. Reports the files, command results, checks, elapsed time, and available usage counts.

Repository metadata is evidence, not an instruction to execute code. Missing information stays unknown; the harness does not invent a license or a successful check.

## Build scope

- A portable `find-before-build` skill with `SKILL.md`, GitHub evidence retrieval, a timestamped cache, and deterministic candidate scoring.
- A Python controller with explicit discovery, decision, implementation, and reporting stages. The controller wraps Aider for code editing after its own decision gate, with the native tool loop available as a fallback.
- Local model access through the user's existing KoboldCpp server. Hermes 3 8B is loaded; a Qwen 3.5 4B GGUF is available for a separate trial.
- A switchable Gemma 4 adapter using the Gemini API. Live operation needs an API key.
- Workspace-confined read/write tools and a command tool that asks before running a command.
- A paired evaluation using the same task, model, settings, starting state, and acceptance check with and without discovery. Report actual measured results and `N/A` for unavailable metrics.

The initial scoring weights are license 25, recency 25, README fit 20, issue health 15, and stars 15. They help rank candidates; task fit and integration effort still determine the final choice. These weights are initial design choices, not tuned or validated findings.

Image generation and image reading are outside this project scope.

## Hackathon fit

The project is intended to demonstrate an original harness with open-weight AI, a standards-shaped agent skill, and a Gemma 4 Gemini API integration. Challenge eligibility and submission depend on the final implementation and evidence; this document makes no submission claim.

- [Event](https://www.mlh.com/events/hacktoberfest-hack-day-zuarinagar-x-seds-celestia)
- [Open-source AI challenge](https://www.mlh.com/events/hacktoberfest-hack-day-zuarinagar-x-seds-celestia/challenges/01a0d987-0c41-12ea-13ac-5b85755dc2f3)
- [Gemma challenge](https://www.mlh.com/events/hacktoberfest-hack-day-zuarinagar-x-seds-celestia/challenges/01a03b87-be22-9180-5e2d-b3beeb66666d)

See [ARCHITECTURE.md](ARCHITECTURE.md) for the workflow and [PROJECT_GUIDE.md](PROJECT_GUIDE.md) for implemented state and limits.
