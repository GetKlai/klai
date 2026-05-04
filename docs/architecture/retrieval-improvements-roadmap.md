# Knowledge Retrieval Improvements — Roadmap

> Strategy doc for getting Klai's RAG stack from "industry-standard baseline" to "production-grade for paying customers".
> Companion to [knowledge-retrieval-flow.md](knowledge-retrieval-flow.md), which describes what runs today.
> Created 2026-05-04 after holistic review of current code + 2026 RAG best-practice research.

---

## Where we stand today

The current retrieval stack is healthy. Building blocks that already exist on `main`:

| Layer | Implementation | File |
|---|---|---|
| Embedding | BGE-M3 (dense + sparse + late-interaction in one model) | `klai-knowledge-ingest/knowledge_ingest/embeddings/` |
| Reranker | Infinity (cross-encoder, GPU-01) | tunnelled via `gpu-tunnel-key` |
| Vector store | Qdrant — 3-leg RRF (`vector_chunk` + `vector_questions` + `vector_sparse`) | `klai-retrieval-api/retrieval_api/services/search.py` |
| Knowledge graph | Graphiti / FalkorDB — entity & relationship extraction | `klai-knowledge-ingest/knowledge_ingest/graph.py` |
| Query injection | LiteLLM pre-call hook | `deploy/litellm/klai_knowledge.py` |
| Tenant scoping | Per-org and per-KB filters at query time | `_scope_filter()` in `search.py` |
| Web crawl enrichment | crawl4ai + anchor_texts + links_to (SPEC-CRAWLER-005) | `klai-knowledge-ingest/knowledge_ingest/adapters/crawler.py` |
| Taxonomy classification | Ingest-time tagging into `taxonomy_node_ids` payload | `taxonomy_classifier.py` |

Compared to the typical "naive RAG" pipelines that fail at 72-80% of enterprise pilots, this is well above baseline. The architecture is the right shape for a multi-tenant SaaS — hybrid retrieval with reranker is the foundation that 2026 research consistently identifies as highest-ROI.

## Where the gaps are

Three operational gaps prevent the stack from being "good enough for paying customers":

1. **No measurement.** There is no end-to-end retrieval-quality metric. Without RAGAS or an equivalent harness, every optimisation is anecdote-driven.
2. **No query understanding.** The raw user query goes 1-on-1 to `retrieve_body["query"]`. Vague or under-specified questions produce vague matches. The litellm-hook misses a query-rewriting step.
3. **Chunks not self-contained.** Anchor_texts and links_to provide some context for crawled pages, but most KB chunks are still naked text segments without enough surrounding context for embedding precision. Anthropic's contextual-retrieval pattern fixes this.

A separate latent gap: **query-time taxonomy filtering is wired in retrieval-api but never invoked by the hook**. The classifier exists at ingest time only; query-time classify-call has no caller. SPEC-PORTAL-PROFILES-001's closed predecessor (PR #90) attempted this for the FROZEN klai-focus stack and never landed for Knowledge.

## Why prioritise improvements before launch

Three reasons:

1. **Pre-launch is the cheapest moment.** Adding `chunk_context` to ingest now means re-embedding the (small) existing corpus once. Doing it after launch means coordinating with active customers.
2. **Metrics before features.** Without RAGAS in place, week 4's question *"did parent-child chunking help?"* has no answer. Build the harness first, then optimise.
3. **Industry-standard expectation.** Customers comparing Klai to alternatives (Glean, Perplexity Enterprise, Notion AI) have a baseline expectation that the answer relates to the question. Without query rewriting, edge-case queries will feel "dumber" than the competition.

## The roadmap — three tiers

Each tier has a dedicated SPEC. Tier 1 must land before launch; Tier 2 should land before significant customer growth; Tier 3 is conditional on what RAGAS metrics reveal.

### Tier 1 — measure + low-complexity wins (target: pre-launch)

| SPEC | Scope | Expected impact |
|---|---|---|
| [SPEC-RAG-EVAL-001](../../.moai/specs/SPEC-RAG-EVAL-001/spec.md) | Install RAGAS evaluation harness; nightly metrics on representative query set per KB type | Baseline visibility; precondition for Tier 2/3 |
| [SPEC-RAG-CONTEXTUAL-001](../../.moai/specs/SPEC-RAG-CONTEXTUAL-001/spec.md) | Anthropic-pattern contextual retrieval — pre-pend chunk-specific summary before embedding | -49% retrieval-failure-rate (Anthropic published) |
| [SPEC-RAG-QUERY-REWRITE-001](../../.moai/specs/SPEC-RAG-QUERY-REWRITE-001/spec.md) | LiteLLM-hook layer that rewrites/expands user queries via `klai-fast` before retrieve | +15-25% precision on vague queries |

### Tier 2 — moderate complexity, after Tier 1 metrics confirm need

| SPEC | Scope | Expected impact |
|---|---|---|
| [SPEC-RAG-PARENT-CHILD-001](../../.moai/specs/SPEC-RAG-PARENT-CHILD-001/spec.md) | Parent-child chunking: child for matching, parent for LLM context | Better matching precision + better context-window utilisation on long docs |
| [SPEC-RAG-TAXONOMY-001](../../.moai/specs/SPEC-RAG-TAXONOMY-001/spec.md) | Wire query-time taxonomy classifier + retrieval filter + coverage fallback | Cleaner top-K on large categorised KBs (>1000 chunks) |

### Tier 3 — only on data, not preemptively

These are powerful but expensive — wait until RAGAS metrics show specific failure modes that match each technique's strength.

| Idea | When to consider | Source |
|---|---|---|
| HyDE (Hypothetical Document Embeddings) | RAGAS shows low context-precision on short technical queries | [HyDE — Pondhouse](https://www.pondhouse-data.com/blog/advanced-rag-hypothetical-document-embeddings) |
| GraphRAG community-summaries | Users ask cross-document synthesis questions ("what changed between Q1 and Q3 reports") | [Microsoft GraphRAG](https://microsoft.github.io/graphrag/) |
| Agentic RAG with query decomposition | Complex multi-hop queries surface in production traces | [Agentic RAG Patterns 2026](https://www.digitalapplied.com/blog/agentic-rag-patterns-multi-step-reasoning-guide) |

## Sequencing

```
Week 1   ─ SPEC-RAG-EVAL-001              (RAGAS harness)
            └─ baseline metrics captured
Week 2-3 ─ SPEC-RAG-CONTEXTUAL-001        (parallel work, no shared files)
         ─ SPEC-RAG-QUERY-REWRITE-001
            └─ measure delta vs baseline
Week 4   ─ DECIDE on Tier 2 priority based on metrics:
            • long-doc precision low? → SPEC-RAG-PARENT-CHILD-001 first
            • large-KB ruis high?     → SPEC-RAG-TAXONOMY-001 first
            • both fine?              → focus on launch, defer Tier 2
```

## Constraints we will not break

- **Reranker stays.** The Infinity cross-encoder is the highest-ROI single component in the current stack. None of the improvements remove it.
- **Per-tenant scoping stays at retrieval time.** Adding query rewriting or HyDE must not bypass the `_scope_filter()` that enforces `org_id`. Verified in every SPEC's acceptance criteria.
- **No regression on cost.** Tier 1+2 collectively must add < 30% per-query token cost. Agentic RAG (Tier 3) is the budget exception, used only on opt-in flows.

## How to measure success

After Tier 1 lands, the published deltas should be measurable in RAGAS metrics:
- Context precision: +10-25%
- Faithfulness: stable or up (contextual retrieval reduces hallucination by giving the LLM cleaner context)
- Latency p95: +200-500ms (acceptable; LiteLLM hook pre-call already adds ~300ms)
- Token cost per query: +10-15% (mostly from query-rewriting pass)

If RAGAS does NOT show movement after Tier 1, we have either a measurement bug or a lower ceiling on the current corpus than expected — both are useful signals before further investment.

## Sources

Research input that fed this roadmap (May 2026):

- [Optimizing RAG with Hybrid Search & Reranking — Superlinked](https://superlinked.com/vectorhub/articles/optimizing-rag-with-hybrid-search-reranking)
- [Contextual Retrieval — Anthropic](https://www.anthropic.com/news/contextual-retrieval)
- [9 advanced RAG techniques — Meilisearch](https://www.meilisearch.com/blog/rag-techniques)
- [RAG Production Guide 2026 — Lushbinary](https://lushbinary.com/blog/rag-retrieval-augmented-generation-production-guide/)
- [Parent-child Retrieval — Dify](https://dify.ai/blog/introducing-parent-child-retrieval-for-enhanced-knowledge)
- [Document Chunking 9 Strategies Tested — LangCopilot](https://langcopilot.com/posts/2025-10-11-document-chunking-for-rag-practical-guide)
- [RAGAS metrics — official docs](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/)
- [BGE-M3 documentation](https://bge-model.com/bge/bge_m3.html)
- [Microsoft GraphRAG](https://microsoft.github.io/graphrag/)

---

**Status:** roadmap accepted 2026-05-04. SPECs in `.moai/specs/SPEC-RAG-*/` are open for implementation.
