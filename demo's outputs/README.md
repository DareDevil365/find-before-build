# Recorded browser demo

The [working Gemini 2.5 Flash recording](working-gemini-2.5-flash/demo.webm) shows Find Before Build searching GitHub, choosing to build a small app from scratch, generating `index.html`, then opening and using the result. The generated app is in [working-gemini-2.5-flash/index.html](working-gemini-2.5-flash/index.html).

The harness reported `finished` after 34.46 seconds and five model calls. A browser check clicked Increment twice (count 2), Decrement once (count 1), and Reset (count 0). This is one working demo, not a general reliability or speed claim. The shortlist was based on GitHub metadata because README and issue enrichment was rate-limited. The model selected no repository for this simple, dependency-free request.

The first Gemini 3.8 Flash attempt returned empty coding replies and no files. Its video and raw run record remain in the local `failed-gemini-3.8-flash` folder for diagnosis and are excluded from the public repository. Raw records for both attempts remain local because they may contain request-specific network details.
