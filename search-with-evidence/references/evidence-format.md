# Optional evidence helper

The core skill works through the host's tools. `scripts/rank_results.py` is an optional Python 3.11+ standard-library helper for organizing already collected results. It makes no network requests and does not require an API key. Store inputs/outputs in the task's scratch directory.

```sh
python scripts/rank_results.py --input /task/scratch/evidence.json --output /task/scratch/ranking.json --top 5 --diversity 0.75
```

Replace the paths with actual task paths. Input:

```json
{
  "goal": "Find a beginner course with practical exercises",
  "interpretation": "Introductory learning, not an advanced certificate",
  "criteria": [
    {"id": "beginner", "weight": 3, "required": true},
    {"id": "exercises", "weight": 2, "required": false}
  ],
  "streams": [
    {
      "provider": "host-web-search",
      "family": "beginner-course",
      "query": "beginner course official syllabus exercises",
      "retrieved_at": "2026-10-23",
      "results": [
        {
          "url": "https://example.org/course",
          "title": "Illustrative course",
          "snippet": "Synthetic example, not an actual recommendation",
          "assessments": {
            "beginner": {
              "status": "supported",
              "evidence": [{"url": "https://example.org/course", "locator": "Prerequisites section", "note": "The inspected prerequisites specify no prior knowledge."}]
            },
            "exercises": {"status": "unknown"}
          }
        }
      ]
    }
  ]
}
```

Each stream is a ranked result list. Use query-family names for genuinely different information needs; don't relabel equivalent rewrites to manufacture agreement. `weight` is optional on streams (default 1) and criteria (default 1). The helper caps each candidate's fusion contribution within a family. Repeating the same provider/query adds observations, not another fusion vote. Different engines carrying the same source do not independently verify it.

Every supported or contradicted criterion needs an `evidence` entry with an observed HTTP(S) URL, a locator, and a note. The helper checks that those fields are present and structurally valid; the host must actually inspect and judge them. Evidence-free support/contradiction is downgraded to unknown. Statuses are `supported`, `claimed`, `unknown`, and `contradicted`; conflicting supported/contradicted observations remain `conflicted`.

Criteria score: supported = 1, claimed = 0.35, unknown/contradicted/conflicted = 0, weighted and scaled to 100. The 0.35 value is an uncalibrated design choice. Required contradictions cause rejection; required gaps cause a provisional result. An unassessed source receives zero or no score because evidence is missing, not because its actual quality is zero.

`ranked` is the evidence-based recommendation order. `inspection_queue` uses normalized RRF and lexical MMR to propose diverse pages to read next; it excludes rejected candidates but is not the final answer. `--diversity` is the MMR relevance weight: 1 means no redundancy penalty, lower values favor novelty. URL deduplication removes tracking parameters/fragments and normalizes host/default ports while preserving functional parameters and path case. An optional observed `canonical_id` can group known variants, such as the same DOI; don't infer identity from a similar title alone.

No helper output proves a source is truthful, that the internet was exhaustively searched, or that our results outperform another search system.
