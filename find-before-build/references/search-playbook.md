# Search and comparison playbook

## Use the host agent's tools

Prefer a connected GitHub tool for repository search, directory listings, file reads, releases, and issues. Use ordinary web search for alternate terminology and primary repository links, and a browser when useful evidence is only available there. Authenticate through the host's existing connection when needed; the skill does not require its own account or model service. Tool availability and external access still belong to the host/provider.

Search is iterative rather than a fixed three-query pipeline. Run independent queries together when the host supports it. Inspect a shortlist deeply, then expand only where evidence could change the recommendation. A failed request is an unavailable observation, not a negative result. Report cached and incomplete results honestly; do not discard successful queries because another request failed.

## Optional standalone helpers

The scripts need Python 3.11+ and only the standard library. Run them from this skill directory, or use absolute script paths. Evidence files belong in the task's working or scratch directory, not in the installed skill. GitHub public requests work without a dedicated key; `GITHUB_TOKEN` is optional. If the REST route is unavailable, use the host's other enabled tools or report the evidence gap.

```sh
python scripts/search_repos.py --query "pdf merge bookmarks" --query "pdf manipulation language:Python" --metadata-only --max-results 20 --output /task/scratch/search-1.json
python scripts/score_repo.py --input /task/scratch/search-1.json --task "Merge PDFs and preserve bookmarks" --capability "PDF merge" --capability "bookmark preservation" --language Python --output /task/scratch/ranking-1.json
```

Substitute a real task scratch path. `--query` accepts GitHub qualifiers. The helper adds `archived:false` unless an archived qualifier was supplied, retains best-match ordering, interleaves query results, and reports the exact executed queries, failures, total counts, incomplete-result indicators, and zero-result follow-up suggestions. Per-call limits control output size; additional calls are available throughout the investigation.

After a search has actually returned an owner/name, inspect it:

```sh
python scripts/inspect_repo.py OWNER/REPOSITORY --file src/relevant_module.py --file tests/relevant_test.py --output /task/scratch/inspection.json
```

Use observed source paths, not the placeholder paths above. The inspector resolves a branch/tag to a commit, lists the root, and reads README/license/package files plus requested source/test files. It returns commit-specific URLs without cloning or executing code. `--path src` lists a discovered directory; a later `--path src/module.py --find parse` returns source plus bounded passages with line links. Reinvoke it with observed paths or a chosen `--ref` for a release. Individual files are limited to 1 MB, displayed content to 12,000 characters, and matching passage windows to eight; truncation and unavailable evidence are explicit. A root license and current metadata are observations, not a complete legal assessment of every vendored file.

Combine multiple search rounds and later inspections; newest observations go last:

```sh
python scripts/score_repo.py --input /task/scratch/search-1.json --input /task/scratch/search-2.json --input /task/scratch/inspection.json --task "Merge PDFs and preserve bookmarks" --capability "bookmark preservation" --language Python --output /task/scratch/final-features.json
```

Duplicate owner/name records merge once. Later fields replace earlier fields; query provenance is combined. Do not merge unrelated tasks just to inflate candidate counts. Preserve timestamps and source provenance when using connectors instead of these JSON helpers.

## Candidate feature vector

The `relevance-v2` helper uses task-derived features, not a list of favored repositories:

- Task and per-capability token coverage, weighted by term rarity in the current candidate set. Name/description/topics count fully; README-only matches count 0.4 because mentions can be incidental.
- BM25 retrieval similarity with document-length normalization, exposed as a tie-breaker. It is lexical retrieval, not embeddings or learned semantic ranking.
- Observed language compatibility, repository size as a rough integration proxy, and claimed usage instructions. Repo size does not measure the installed dependency footprint.
- License, push age, sampled recent issue closures, and a small logarithmic star signal.
- Score caps for weak relevance, disabled/archived projects, and resource indexes. Caps and every score component are returned, so a summed score does not conceal a rejection rule.
- Evidence-field completeness. Unknown values remain unknown; a low score caused by missing observations is a reason to inspect, not proof that a repository is poor.

Scores from different candidate sets are not calibrated probabilities: IDF depends on the compared corpus. Compare candidates for the same task and criteria. The host agent performs the semantic rerank after reading source. Never claim the helper was trained or that a score predicts success.

## The agent's final comparison

Create a small matrix that addresses the task's hardest parts:

| Requirement | Importance | Candidate A | Candidate B |
| --- | --- | --- | --- |
| Required behavior | High | Supported by source permalink | README claim only |
| Integration boundary | High | Known API/example | Unknown setup/compatibility |
| Acceptance behavior | High | Existing test read; not run | No supporting test located |
| License and maintenance | Contextual | Observations plus gaps | Observations plus gaps |

Search specifically for the leading candidate's weak point. For example, if an application looks complete but its bookmark handling is unknown, inspect the relevant implementation/tests and compare a PDF specialist. If an index is the best result, follow its component links. If a dependency is in another runtime, identify a real service/CLI interface before assuming it can be dropped into the requested stack.

When multiple repositories are needed, evaluate marginal coverage: which important requirement does the second component add, and what integration cost does it introduce? Avoid combining overlapping frameworks merely because both scored well. Compare the final proposal against a small fresh implementation; reject reuse when its actual cost exceeds its benefit.

GitHub query semantics: [repository search qualifiers](https://docs.github.com/en/search-github/searching-on-github/searching-for-repositories) and [REST repository search](https://docs.github.com/en/rest/search/search#search-repositories). GitHub's default scope does not include README contents, so use `in:readme` deliberately when metadata searches miss implementations.
