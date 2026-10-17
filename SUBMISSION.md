# Hackathon submission draft

**Title:** Find Before Build

**One-line pitch:** A coding harness that checks maintained open-source repositories before asking a local or Gemma 4 model to write new code.

**Description:** AI coding agents often start from scratch even when a reusable project already exists. Find Before Build packages a portable Agent Skill and a small Python harness that searches GitHub, scores candidate repositories with visible reasons and license flags, then asks the model to choose dependency, fork, borrow, or build fresh. The decision is shown before coding. The harness can use a local open-weight model through KoboldCpp or Gemma 4 through the Gemini API; focused one-file edits can use Aider, while larger tasks use the built-in bounded tool loop. A paired evaluation runner measures both paths with the same task and acceptance checks.

**Categories:** Best Open-Source AI Project; Best Use of Gemma 4.

**Evidence to show:**

1. Run `python harness.py "Build a CSV cleaner CLI" --workspace .\demo-workspace --backend koboldcpp --engine native --record .\runs\local-demo.json` with KoboldCpp already serving a local GGUF model. Point out the candidate list, license flags, and decision before file writes.
2. Run `python harness.py "Create calculator.py with add(a, b) and README.md with an example" --workspace .\gemma-demo --backend gemma-api --engine native --no-commands --record .\runs\gemma-demo.json` with `GEMINI_API_KEY` in the ignored `.env`. Do not display the key or `.env`.
3. Show the real paired result in `README.md`: both Gemma arms passed the calculator check. Discovery took 28.618 seconds and 4,390 reported tokens versus 11.410 seconds and 1,965 tokens without discovery. This simple task demonstrates overhead and a correct build-fresh decision, not a speedup.
4. Show `find-before-build/SKILL.md`, `PROJECT_GUIDE.md`, `LICENSE`, and the passing unit test command `python -m unittest discover -s tests -q`.

**Known limits:** The deterministic search and score are only screening signals. Aider can miss multi-file edits, so automatic mode selects the native loop for larger tasks. Results cover small tasks; broader quality or speed claims need more evaluation. Image features were removed from this build.

**Before submitting:** Add the public GitHub URL to the event form, keep the repository public with its MIT license, and use the event's Submit a Project button before the deadline. This file is a draft for the human submission; it is not evidence that the event form has been sent.
