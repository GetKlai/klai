# Should Klai wire `chunk_type` into retrieval, or stop classifying it?

> Research memo · 2026-06-08 · audience: Klai engineering
> **Verification status:** compiled by an automated web-research pass (4 angles,
> WebSearch + WebFetch). The *direction* aligns with well-established RAG practice
> (document-level metadata beats inferred per-chunk taxonomy; query-side routing is
> cheaper than chunk-side; hybrid + reranker is the converged stack). The specific
> arXiv IDs and benchmark numbers below were **not independently re-verified** —
> spot-check any citation before quoting it externally.
>
> Question: the ingest pipeline runs an LLM classification on **every** chunk
> (`chunk_type ∈ {procedural, conceptual, reference, warning, example}`), stores it
> in the Qdrant payload, and **no retrieval consumer reads it**. We pay LLM tokens
> plus a retry round-trip per chunk for a label nobody consumes. Wire it in, or kill it?

## 1. TL;DR

Per-chunk **type** labeling is closer to a fad than a durable retrieval lever. The single best controlled study that includes an actual per-chunk "chunk type" signal (SRAG, [arXiv:2603.26670](https://arxiv.org/html/2603.26670)) finds that **removing chunk type in isolation produces no statistically significant change** — the value is compositional and redundant with other metadata, not standalone. The metadata that *demonstrably* moves retrieval in rigorous studies is **cheap parser/structure-derived fields** (document type, section, fiscal year) and **document-level content_type** — which Klai already consumes for evidence-tier weighting — not an LLM-inferred instructional taxonomy. Recommendation: **stop classifying `chunk_type` at ingest and reclaim the per-chunk LLM cost** (option c). It is an unread label whose closest measured analogue scores below noise, and it carries the same classifier-noise and multiplicative-compounding risk as the assertion-mode weighting work — without that work's document-level evidence base.

## 2. Current value — what `chunk_type` is worth in production RAG today

**Honest answer: there is essentially no isolated measured evidence that a per-chunk content-TYPE label improves retrieval.** The structure-aware-chunktype angle searched specifically for this and the strongest result is a null result:

- **SRAG / Structured RAG ([arXiv:2603.26670](https://arxiv.org/html/2603.26670))** is the only controlled study that attaches a per-chunk "chunk type" signal alongside semantic tags, topics, sentiment, and KG triples. The full bundle scored 94.35 vs 72.36 for plain RAG (~30% gain, p=2e-13). But the ablation is the load-bearing finding: *"removing individual metadata components does not lead to statistically significant performance changes when considered in isolation,"* attributed to "partial redundancy among metadata signals." Chunk type was among the larger-effect signals when ablated as a group — but **not significant alone**. For a team deciding whether to LLM-label `chunk_type` *specifically*, this means the marginal benefit of that one field is below noise.

- **The rigorous metadata-ablation studies don't even use a per-chunk content-type field.** RAGMATE-10K / "Utilizing Metadata for Better RAG" ([arXiv:2601.11863](https://arxiv.org/html/2601.11863v1)) doubled Context@5 (33.33% → 63.33%) — but its schema is **entirely document-level** (`company_name`, `form_type`, `section`, `fiscal_year_end`, `SIC_code`), with "no field distinguishing different types of chunk content within sections." The authors found global identifiers "drive document accuracy" while section cues "primarily aid chunk-level localization." The proven lever is document-level metadata, which maps to Klai's existing `content_type` weighting — not to `chunk_type`.

- **What *is* measured for per-chunk LLM work points the other way.** Anthropic's Contextual Retrieval ([anthropic.com/news/contextual-retrieval](https://www.anthropic.com/news/contextual-retrieval)) — the one LLM-per-chunk technique with strong numbers (35-49% retrieval-failure reduction) — adds a **context sentence**, not a **type label** ("This chunk is from an SEC filing on ACME Q2 2023..."). It is "the opposite of type-labeling," and even its gains come mostly from pairing with BM25 + reranker, not the LLM step alone.

- **What practitioners recommend `chunk_type` for is filtering/routing, not similarity.** Azure's RAG enrichment guide ([learn.microsoft.com](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/rag/rag-enrichment-phase)) and Databricks ([docs.databricks.com](https://docs.databricks.com/aws/en/generative-ai/tutorials/ai-cookbook/quality-rag-chain)) both list per-chunk type/tags as metadata fields — but explicitly as **filter keys to narrow the search space**, with **zero measured ranking deltas**, and Azure itself warns to "test to determine the effect" and "calculate the cost of augmenting." Regal.ai's RAG playbook recommends type-style chunk titles ("Procedure: Password Reset...") purely for applicability disambiguation, with "no measured improvements or controlled studies presented."

**Strength of evidence verdict:** weak-to-null for `chunk_type` as a retrieval-quality lever. The only honest positive use case is *type-specific query filtering* (how-to vs risk), and even that is advice, not measured gain.

## 3. Future value — where this is heading

The trajectory does **not** make a *stored, eagerly-computed* `chunk_type` more valuable. Two forces dominate:

- **Routing is moving to the query side, not the chunk side.** Intent/complexity routing is a real, mostly-positive technique (Adaptive-RAG, [arXiv:2403.14403](https://arxiv.org/html/2403.14403v1): F1 46.94 vs 21.12 no-retrieval), but the type is attached to the **query**, retrieved per-request — not pre-stamped on every chunk. The cost-and-alternatives angle is blunt: query-time routing is "one LLM-or-heuristic call per query," and "classifying N queries is vastly cheaper than LLM-labeling M chunks (M >> N) for the same routing value" ([jxnl.co](https://jxnl.co/writing/2025/09/11/data-organization-and-query-routing-for-rag-systems/)). A keyword router hit 82% accuracy at <1ms vs an ML classifier's 84% at 200ms — the routing value lives at query time, cheaply.

- **The honest routing benchmarks reject naive type-matching.** RAGRouter-Bench ([arXiv:2604.03455](https://arxiv.org/html/2604.03455)) — the most rigorous 2026 routing study — concludes "routing is a query-corpus interaction problem, not a query-only problem." Matching query-type → chunk-type is exactly the query-only heuristic it warns against. Even the strongest routers (RAGRouter, [arXiv:2505.23052](https://arxiv.org/html/2505.23052v1)) gain only +3.61% by modeling LLM-knowledge interaction, not by type-matching.

- **Production consensus is converging away from per-chunk LLM enrichment.** The durable 2025-2026 stack is hybrid (BM25 + vector) + cross-encoder reranker ([InfoQ](https://www.infoq.com/articles/vector-search-hybrid-retrieval-rag/)), with metadata used as a pre-filter. Per-chunk LLM type-classification "is not part of the converged stack." The "RAG isn't a modeling problem" essay ([datalakehousehub.com](https://datalakehousehub.com/blog/2026-01-rag-isnt-the-problem/)) attributes RAG failure to data hygiene, not to missing per-chunk cleverness.

Emerging query-aware chunk selection (SmartChunk, [arXiv:2602.22225](https://arxiv.org/abs/2602.22225); SRAG's compositional metadata) could *eventually* give a stored chunk taxonomy a role — but published quantitative deltas are thin-to-absent, and the direction is **chunk granularity/abstraction**, not the procedural/conceptual instructional taxonomy Klai computes. **Net: `chunk_type` gets less valuable as routing centralizes at query time and rerankers absorb the quality budget.**

## 4. The three options for Klai

### (a) Wire `chunk_type` into retrieval — query-intent → type routing/boost

- **What it takes:** a query-side intent classifier (the actual value-add), a routing/boost layer in the retrieval consumer that maps query intent → preferred `chunk_type`, payload-index on `chunk_type` in Qdrant for filterable HNSW, and — critically — an **end-to-end ablation on Klai's own corpus**, because every honest source says gains are corpus-dependent and must be measured locally ([RAGRouter-Bench](https://arxiv.org/html/2604.03455); [metadata angle](https://arxiv.org/html/2510.24402v1)).
- **Expected payoff:** unproven for the type label specifically. Best case is modest filtering gains on type-specific queries (how-to vs risk). The closest measured analogue (SRAG chunk-type ablation) is **not significant in isolation**.
- **Risk:** high and well-documented. Over-filtering is a *measured* failure mode — FinanceBench ([arXiv:2510.24402](https://arxiv.org/html/2510.24402v1)) shows metadata-driven chunk expansion dropping Claim Recall 47.7% → 41.1% and *raising* hallucination 14.7% → 22.2%. Low-cardinality filters (and `chunk_type` has only 5 values) "fragment the HNSW graph... causing lower accuracy" per [Qdrant](https://qdrant.tech/articles/vector-search-filtering/). Plus the classifier is imperfect (Adaptive-RAG's was ~54% accurate and *lost* to always-multi-step on multi-hop), so a hard route on a noisy label can suppress correct chunks. You keep paying the ingest cost **and** add query-side cost and regression risk.

### (b) Keep classifying, but only for analytics/UI

- **What it takes:** nothing new on the retrieval path; surface `chunk_type` distributions in dashboards or source-detail UI.
- **Expected payoff:** marginal product/observability value (e.g. "this KB is 60% reference, 5% procedural"). No retrieval impact.
- **Risk:** you keep paying the full per-chunk LLM token + retry round-trip cost for a non-retrieval purpose. This only makes sense if a concrete UI/analytics consumer is committed and judged worth that recurring ingest cost — which today does not exist. Otherwise it's the status quo with a rationalization.

### (c) Stop classifying — drop the cost

- **What it takes:** remove the `chunk_type` classification call from the ingest pipeline; optionally backfill-drop the payload field. Low effort, well-scoped, reversible.
- **Expected payoff:** immediate elimination of the per-chunk LLM token cost **and** the retry round-trip per chunk — the most expensive part, since it's M chunks not N queries. Faster, cheaper ingest.
- **Risk:** lowest. If a measured need appears later, query-side intent routing (cheap, per-query) is the better re-entry point than re-stamping every chunk — and document-level `content_type`, which Klai already has and uses, covers the proven document-level lever. The only thing lost is a label no consumer reads.

## 5. Recommendation — Option (c): stop classifying `chunk_type`

**Drop the per-chunk `chunk_type` classification at ingest and reclaim the cost.**

The evidence base is decisive in the skeptical direction: the one rigorous study that includes the exact signal (SRAG chunk-type ablation, [arXiv:2603.26670](https://arxiv.org/html/2603.26670)) finds **no statistically significant standalone effect**; the rigorous metadata wins come from cheap document-level fields ([arXiv:2601.11863](https://arxiv.org/html/2601.11863v1)) that Klai already consumes via `content_type`; and the converged production stack ([InfoQ](https://www.infoq.com/articles/vector-search-hybrid-retrieval-rag/)) doesn't include per-chunk LLM type-classification at all. We are paying the most expensive form of LLM enrichment (M chunks, with a retry round-trip) for the least-proven label, consumed by nobody. That is a pure cost with no measured return.

**Tie-in to the assertion-mode weighting work.** Both `chunk_type` and assertion-mode are per-chunk LLM labels, and they share the two failure modes that should make Klai cautious about *any* per-chunk LLM signal entering the scoring path:

1. **Multiplicative compounding.** When a per-chunk label feeds a score multiplier, a wrong label doesn't just fail to help — it actively suppresses or inflates a chunk. SRAG explains why the *bundle* helps but components don't: "compensatory effects" across signals. A single multiplier on a noisy label has no such compensation; it compounds directly into the final ranking.
2. **Classifier noise.** Adaptive-RAG's router was ~54% accurate ([arXiv:2403.14403](https://arxiv.org/html/2403.14403v1)); FinanceBench showed metadata-driven expansion *raising* hallucination ([arXiv:2510.24402](https://arxiv.org/html/2510.24402v1)). Per-chunk classifiers are imperfect, and a 5-value label on a low-cardinality filter fragments HNSW ([Qdrant](https://qdrant.tech/articles/vector-search-filtering/)).

The crucial distinction: **assertion-mode weighting has a document-level evidence-tier rationale that `chunk_type` lacks.** Klai's existing `content_type` → evidence-tier weighting is exactly the document-level, structure-derived signal the rigorous studies validate ([arXiv:2601.11863](https://arxiv.org/html/2601.11863v1)). `chunk_type` is the inferred, per-chunk, retrieval-unconsumed taxonomy with a null ablation. So the recommendation is asymmetric on purpose: **keep investing the per-chunk-LLM-label scrutiny in the assertion-mode weighting work where there's a document-level evidence base and a real consumer; kill `chunk_type` where there is neither.** If you ever want type-aware retrieval, do it at query time (cheap, per-query, [jxnl.co](https://jxnl.co/writing/2025/09/11/data-organization-and-query-routing-for-rag-systems/)) and measure end-to-end on Klai's corpus before storing anything.

## 6. Sources

**Structure-aware / chunk-type (most directly on-question):**
- SRAG / Structured RAG — per-chunk chunk-type ablation, null-in-isolation: https://arxiv.org/html/2603.26670
- RAGMATE-10K, "Utilizing Metadata for Better RAG" — document-level metadata wins, no chunk-type field: https://arxiv.org/html/2601.11863v1
- RDR2, structure-aware (layout, not content taxonomy): https://arxiv.org/abs/2510.04293
- Azure RAG enrichment guidance (advice, no deltas): https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/rag/rag-enrichment-phase

**Cost & alternatives:**
- Anthropic Contextual Retrieval (context sentence ≠ type label; $1.02/M tokens): https://www.anthropic.com/news/contextual-retrieval
- InfoQ — hybrid + reranker as the converged stack: https://www.infoq.com/articles/vector-search-hybrid-retrieval-rag/
- jxnl.co — query-time routing cheaper than per-chunk enrichment: https://jxnl.co/writing/2025/09/11/data-organization-and-query-routing-for-rag-systems/

**Metadata filtering / rerank (downsides + over-filtering):**
- FinanceBench — measured over-filtering, recall drop + hallucination spike: https://arxiv.org/html/2510.24402v1
- Multi-Meta-RAG — Hits up but MAP down (not a universal win): https://arxiv.org/html/2406.13213v1
- Qdrant — low-cardinality filters fragment HNSW: https://qdrant.tech/articles/vector-search-filtering/

**Intent / complexity routing (query-side, not chunk-side):**
- Adaptive-RAG — complexity routing, imperfect classifier (~54%): https://arxiv.org/html/2403.14403v1
- RAGRouter-Bench — "query-corpus interaction, not query-only": https://arxiv.org/html/2604.03455
- RAGRouter — modest +3.61% from modeling LLM-knowledge interaction: https://arxiv.org/html/2505.23052v1
- AMAQA — metadata-as-signal end-to-end gains (document-level): https://arxiv.org/abs/2505.13557
