# Research basis and evaluation

## Techniques retained

- **Query expansion and relevance feedback:** vary synonyms and use useful retrieved terminology to revise queries while retaining the original information need. [Stanford IR textbook](https://nlp.stanford.edu/IR-book/html/htmledition/relevance-feedback-and-pseudo-relevance-feedback-1.html). LLM-generated expansion can help retrieval, as studied in [Query2doc](https://aclanthology.org/2023.emnlp-main.585/), but generated pseudo-documents are search hypotheses, never evidence. This skill prefers observed terminology and checks for intent drift.
- **Reciprocal rank fusion:** `RRF(d) = sum(1 / (60 + rank(d)))` combines differently scaled ranked lists without comparing their raw engine scores. [Cormack, Clarke, and Buettcher, SIGIR 2009](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf). Our helper caps contribution within a query family to limit repetition bias. That cap is our design adaptation, not a result established by the paper.
- **MMR:** choose an inspection candidate for its relevance and its difference from already selected candidates: `lambda * relevance - (1 - lambda) * redundancy`. [Goldstein and Carbonell, 1998](https://aclanthology.org/X98-1025/). Our helper uses token-set Jaccard overlap, not learned semantic similarity. Its inspection queue supplements the recommendation ranking; diversity does not make a source trustworthy.
- **BM25:** term-frequency saturation and document-length normalization are useful lexical retrieval features. [Stanford IR overview](https://nlp.stanford.edu/IR-book/html/htmledition/probabilistic-information-retrieval-1.html). The specialized repository helper uses BM25 as a tie-breaker; the general helper does not claim to provide an index or embedding model.
- **Semantic reranking:** the host model compares inspected content with a criterion matrix. Its ratings must cite evidence. They remain model judgments, not trained/calibrated ranking probabilities or independent verification.

## What is not new

Search providers and agent skills already implement query decomposition, extraction, fusion, and model reranking in various forms. For example, [Google describes query fan-out](https://blog.google/products-and-platforms/products/search/ai-mode-search/), while [last30days](https://github.com/mvanhorn/last30days-skill) includes a substantive query-planning/fusion/reranking pipeline. This skill's combination is an engineering design to test, not a claim of inventing those algorithms or beating those products.

## How to evaluate usefulness

Use the same host model, tools, tasks, and comparable search effort with and without the skill. Include ambiguous entity/role requests, novice course selection, specialist repository search, and literature discovery. Save actual retrieved pools and a log of source coverage. Test the default agent against the new workflow and ablate interpretation checking, query-family capping, and evidence gating separately.

Have an independent assessor grade result relevance without seeing which method produced it. Report nDCG@k for graded relevance, precision@k for qualifying results, required-criterion coverage, the proportion of decisive claims supported by inspected sources, time, and tool calls. Record disagreements and avoid scoring our own preferred answer as ground truth. On the open web, full recall is usually unknown because the complete relevant set is unknown. [Stanford on IR evaluation](https://nlp.stanford.edu/IR-book/html/htmledition/information-retrieval-system-evaluation-1.html).

Synthetic fixtures test failure-handling invariants: repeated queries should not inflate votes; missing evidence should not become support; hard failures should not win; duplicates should preserve functional URL parameters; contradictory observations should remain visible. Those checks do not establish search-quality improvement against real users, commercial search engines, or other agents. A prospective comparison remains required.
