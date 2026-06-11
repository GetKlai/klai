# Knowledge Retrieval Improvements — Roadmap

> Strategy doc for getting Klai's RAG stack from "industry-standard baseline" to "production-grade for paying customers".
> Companion to [knowledge-retrieval-flow.md](knowledge-retrieval-flow.md), which describes what runs today.
> Created 2026-05-04 after holistic review of current code + 2026 RAG best-practice research.
> Updated 2026-05-05: **Tier 1 + Tier 2 SHIPPED**. Measured impact on Voys-support: precision +61%, recall +154%, faithfulness moved from broken to 0.81.
> Updated 2026-06-11: GAP-EVAL-01/02 are closed in code. Scored RAGAS suites require full `reference_answer`, `expected_chunks` canaries hard-fail before fuzzy scoring, and the new `rag_eval_canary_dropped` alert catches dropped canaries. Old baselines are not comparable; run live canary debug and recapture `baseline-v5` before using the new numbers as decision gates.

---

## TL;DR — measured impact on Voys-support (chat suite, 30 queries)

| Metric | baseline-v4 (pre-stack) | post_pr_abcdefg_v1 (full stack live) | Δ |
|---|---|---|---|
| `context_precision` | 0.231 | **0.372** | **+0.141 (+61%)** |
| `context_recall` | 0.253 | **0.642** | **+0.389 (+154%)** |
| `faithfulness` | NaN (judge-truncation) | **0.812** | first measurable |
| `answer_relevance` | 0.706 | 0.711 | +0.005 |

n = 30 queries, n_faithfulness_measured = 30/30 (100% — was 0/30 on baseline due to klai-fast 3072-token truncation, then 5/8 on klai-medium with default 1024-token cap, now fully measurable after raising the heavy-LLM cap to 8192).

**Variants live on Voys today:** contextual_v1 (document-summary chunks), parent-child_v1 (small children for matching, large parents for context expansion), query_rewrite_v1 (`QUERY_REWRITE_MODEL` rewrite — default `mistral-small-2603` — in litellm-hook), taxonomy_v1 (multi-KB classifier in litellm-hook — currently no-ops on Voys-support since the KB has 0 curated taxonomy nodes; the periodic re-cluster that would create nodes is unscheduled — GAP-TAX-01).

**Caveat:** the eval harness calls retrieval-api directly and bypasses the litellm-hook, so query_rewrite_v1 + taxonomy_v1 contribute zero to the measured deltas above. The +61% / +154% / 0.81 numbers come from contextual_v1 + parent-child_v1 + the rebuild_kb backfill alone. Hook-level features will move metrics on the chat-completion path (real users) but are out of reach of this harness.

---

## Where we stand today

The current retrieval stack is healthy. Building blocks that already exist on `main`:

| Layer | Implementation | File |
|---|---|---|
| Embedding | BGE-M3 (dense + sparse + late-interaction in one model) | `klai-knowledge-ingest/knowledge_ingest/embeddings/` |
| Reranker | Infinity-compatible cross-encoder service | environment-specific endpoint |
| Vector store | Qdrant — 3-leg RRF (`vector_chunk` + `vector_questions` + `vector_sparse`) | `klai-retrieval-api/retrieval_api/services/search.py` |
| Knowledge graph | Graphiti / FalkorDB — entity & relationship extraction | `klai-knowledge-ingest/knowledge_ingest/graph.py` |
| Query injection | LiteLLM pre-call hook | `deploy/litellm/klai_knowledge.py` |
| Tenant scoping | Per-org and per-KB filters at query time | `_scope_filter()` in `search.py` |
| Web crawl enrichment | crawl4ai + anchor_texts + links_to (SPEC-CRAWLER-005) | `klai-knowledge-ingest/knowledge_ingest/adapters/crawler.py` |
| Contextual chunks | Anthropic-pattern document_summary + context_prefix per chunk (SPEC-RAG-CONTEXTUAL-001) | `klai-knowledge-ingest/knowledge_ingest/contextual.py` |
| Parent-child chunks | Small children for matching, large parents for LLM context (SPEC-RAG-PARENT-CHILD-001) | `klai-knowledge-ingest/knowledge_ingest/chunker.py` + `qdrant_store.upsert_enriched_chunks` |
| Query-time rewrite + classify | Single-LLM-call rewrite + taxonomy_node_ids classify in the litellm-hook (SPEC-RAG-QUERY-REWRITE-001 + SPEC-RAG-TAXONOMY-001 multi-KB) | `deploy/litellm/klai_knowledge.py::_rewrite_and_classify` |
| Multi-KB taxonomy lookup | Single-trip multi-KB tree + binary coverage signal, Redis-cached at hook layer | `klai-retrieval-api/retrieval_api/services/taxonomy_lookup.py` |

This is well above naive-RAG baseline.

---

## Baseline + measurement layer (SHIPPED 2026-05-05)

SPEC-RAG-EVAL-001 closed the measurement gap. The harness runs against Voys's production retrieval stack and writes per-query metrics to `knowledge.rag_eval_results`, tagged with a `variant` column for A/B comparison against the captured baselines.

### Voys baseline → post-stack on chat suite (30 queries)

| Metric | `baseline-v4` (2026-05-05 morning) | `post_pr_abcdefg_v1` (2026-05-05 evening) | Δ |
|---|---|---|---|
| `context_precision` | 0.231 | **0.372** | +0.141 (+61%) |
| `context_recall` | 0.253 | **0.642** | +0.389 (+154%) |
| `faithfulness` | NaN (broken) | **0.812** | first measurable |
| `answer_relevance` | 0.706 | 0.711 | +0.005 |

`baseline-v4` was captured after the metric-fix PRs (#312 routed faithfulness to klai-medium, #321 dropped the role-based klai-pipeline alias) but BEFORE any of the Tier 1/2 retrieval features landed. It is the clean reference point against which every post-stack variant is compared. Earlier `baseline` / `smoke-test-v3` rows captured before the metric fixes are kept in the table only as historical evidence and should not be used for comparisons.

### Why `faithfulness` was unmeasurable on baseline-v4

Two distinct truncation regressions had to be fixed in series before faithfulness could be measured at all:
1. PR #312 routed faithfulness from klai-fast (3072-token output) to klai-medium (much larger output). That fixed the original 28/30 NaN class.
2. PR C (RAGAS migrate to `ragas.metrics.collections`) triggered a second class: RAGAS' `InstructorBaseRagasLLM` defaults to `max_tokens=1024` regardless of the underlying model's capacity. On the new collections-API code path 5 of 8 Voys queries truncated mid-JSON with `The output is incomplete due to a max_tokens length limit`.
3. PR G raised the heavy-LLM cap to 8192 on the Faithfulness path only, leaving light metrics at the default. After PR G all 30/30 queries produce a faithfulness score.

### What's in the harness

- `klai-knowledge-ingest/knowledge_ingest/eval/` — `ragas_runner.py`, `suite_loader.py`, `retrieval_client.py`, `judge_client.py`, `store.py`. Uses RAGAS 0.4.3's `ragas.metrics.collections` per-metric `ascore()` API (parallel via `asyncio.gather`, per-metric fail-open via `_safe_ascore`).
- `deploy/postgres/migrations/014_rag_eval_results.sql` — storage table + 2 indexes
- `klai-knowledge-ingest/knowledge_ingest/eval/suites/{chat,knowledge_org}.yaml` — 67 hand-curated Voys queries with mix-tags (easy_lookup / vague_pronoun / multi_doc_synthesis / long_tail / edge_case / brand_bridging), full `reference_answer` fields for RAGAS, and `expected_chunks` canaries where applicable
- `deploy/grafana/provisioning/dashboards/rag-quality.json` — 4 metric panels + failed-row count, 7-day moving average, `$variant` template variable
- `deploy/grafana/provisioning/alerting/rag-eval-rules.yaml` — `rag_eval_faithfulness_low` HIGH alert (faithfulness **< 0.80** on 2 consecutive nights) + `rag_eval_canary_dropped` HIGH alert for failed expected-chunk canaries
- `docs/runbooks/rag-quality.md` — triage runbook for both alerts

> **Ground-truth status (updated 2026-06-11).** GAP-EVAL-01/02 are closed in
> code: scored suite runs now require `reference_answer`, RAGAS receives that
> full reference answer rather than joined topic labels, and `expected_chunks`
> canaries hard-fail before fuzzy scoring. Old baselines remain incomparable;
> recapture `baseline-v5` after a live `manual-canary-debug` run confirms the
> canary markers match production retrieval output.

### Ad-hoc usage for variant experiments

```bash
docker exec klai-core-knowledge-ingest-1 \
  python -m knowledge_ingest.eval --suite chat --variant my-experiment
```

`evaluate_retrieval_quality_nightly` is registered on the `RAG_EVAL` LLM-lane queue with `queueing_lock=f"rag-eval-{suite}"`. The nightly cron-trigger **is** wired now: a per-suite `@procrastinate_app.periodic` wrapper (`ragas_runner.py:~230`) defers the eval on schedule. The operator can still trigger ad-hoc via the CLI above.

### Eval-harness limitations to know

- **Bypasses the litellm-hook.** The harness calls retrieval-api directly with the raw query, so it cannot measure features that live in the hook itself: `query_rewrite_v1` (`QUERY_REWRITE_MODEL` rewrite + history coreference) and `taxonomy_v1` (multi-KB classifier). Their effect shows up only on the live chat-completion path.
- **No taxonomy nodes on Voys.** `taxonomy_v1` is also unmeasurable on Voys-support specifically because the KB has 0 curated taxonomy nodes today (the multi-KB hook logs `skip_reason=all_kbs_low_coverage` for every query). Tenants that curate a taxonomy will see filter-narrowing impact; Voys won't until support content is tagged.

---

## What landed (Tier 1 + Tier 2)

All five SPECs in Tier 1 + Tier 2 shipped on 2026-05-05. The measured deltas above were captured immediately after deploy.

### Tier 1 (target: pre-launch)

| SPEC | Scope | PRs | Status |
|---|---|---|---|
| [SPEC-RAG-EVAL-001](../../.moai/specs/SPEC-RAG-EVAL-001/spec.md) | RAGAS evaluation harness; variant-tagged metrics on representative query set | #303, #306, #308, #312, #321, **#350**, **#358**, **#359** | **SHIPPED** |
| [SPEC-RAG-CONTEXTUAL-001](../../.moai/specs/SPEC-RAG-CONTEXTUAL-001/spec.md) | Anthropic-pattern contextual retrieval — per-document summary + context_prefix per chunk | **#329**, **#347** (document_text persist + lingua langdetect) | **SHIPPED** |
| [SPEC-RAG-QUERY-REWRITE-001](../../.moai/specs/SPEC-RAG-QUERY-REWRITE-001/spec.md) | LiteLLM-hook rewrite via klai-fast, combined into a single LLM call with the taxonomy classifier (REQ-5: zero added roundtrip) | **#334** | **SHIPPED** (hook-level — not measured by current eval harness) |

### Tier 2 (gated on Tier 1 metrics — landed in the same window because the architecture made parallel work safe)

| SPEC | Scope | PRs | Status |
|---|---|---|---|
| [SPEC-RAG-PARENT-CHILD-001](../../.moai/specs/SPEC-RAG-PARENT-CHILD-001/spec.md) | Parent-child chunking + retrieval-api parent expansion | **#338**, **#357** (rebuild_kb thread parent_chunk_id into Qdrant) | **SHIPPED** |
| [SPEC-RAG-TAXONOMY-001](../../.moai/specs/SPEC-RAG-TAXONOMY-001/spec.md) | Query-time taxonomy classifier + retrieval filter + binary coverage fallback, multi-KB | **#340**, **#349** (multi-KB + Redis cache + SQL bug fix) | **SHIPPED** (hook-level — only narrows when KB has nodes; Voys has 0 today) |

Plus the rebuild_kb operator backfill (SPEC-RAG-REBUILD-KB-001 #341 + #345 reconstruction-from-Qdrant + **#357** parent_chunk_id threading) so legacy artifacts on Voys could be brought onto the new pipeline without re-fetching from source.

### What this stack actually does on a chat turn

1. User asks "Hoe troubleshoot ik Bubble?" — message arrives at the litellm-hook.
2. Hook fetches per-org KB feature flag (Redis-cached, version-keyed).
3. Hook fetches taxonomy trees for in-scope KBs in parallel with coverage map (Redis-cached, multi-KB, single retrieval-api roundtrip).
4. Hook runs combined query-rewrite + taxonomy classifier in ONE `QUERY_REWRITE_MODEL` call (default `mistral-small-2603`, in `klai_kb_query_rewrite.py`): rewritten query + classified node IDs back. Anti-hallucination guard filters IDs to the union of valid IDs across all KBs.
5. Hook calls `/retrieve` with the rewritten query + (if coverage threshold met) `taxonomy_node_ids` filter.
6. Retrieval-api: BGE-M3 dense + sparse + question vectors → Qdrant RRF (+ graph leg, Graphiti) → Infinity reranker → top-K children selected (orchestration now in `api/retrieve.py` + `api/ranking.py` after the 2026-06-07 decomposition).
7. For each top-K child, retrieval-api expands to its parent chunk via `parent_chunk_id` lookup (`services/parent_lookup.py`; PR #357 made the linkage actually work for legacy artifacts).
8. Chunks return to the hook — provenance-labelled `[org]` / `[persoonlijk]` — and prepend to the system message.
9. LLM sees: rewritten query + taxonomy-narrowed parent chunks (each prefixed with their per-document summary + per-chunk context_prefix from Anthropic pattern).

Every step has fail-open semantics: any failure logs a structured warning and the chat falls through to a degraded but functional path.

---

## Why prioritise improvements before launch (recap from 2026-05-04)

Three reasons that still apply:

1. **Pre-launch is the cheapest moment.** Adding `chunk_context` to ingest now means re-embedding the (small) existing corpus once. Doing it after launch means coordinating with active customers. ✓ done — Voys-support corpus rebuilt cleanly with parent_chunk_id linkage post-PR-E.
2. **Metrics before features.** Without RAGAS in place, week 4's question *"did parent-child chunking help?"* has no answer. ✓ done — we now have `precision +61%` / `recall +154%` against a clean baseline.
3. **Industry-standard expectation.** Customers comparing Klai to alternatives (Glean, Perplexity Enterprise, Notion AI) have a baseline expectation that the answer relates to the question. ✓ baseline answer_relevance was already 0.71 — still 0.71 post-stack on the eval (the harness uses the raw user query so answer_relevance moves only on retrieved-context quality, not on rewrite quality).

---

## Tier 3 — only on data, not preemptively

Tier 3 SPECs remain conditional on what current metrics reveal. With Tier 1 + Tier 2 numbers in hand, three observations frame the next decision:

1. **Recall jumped much further than precision.** +154% recall vs +61% precision means parent-child + contextual retrieval is broadening what we find more than it's narrowing what we surface. Top-K is fuller of relevant chunks AND fuller of marginally-relevant chunks — and 3-leg RRF + Infinity reranker keep them survivable. The next ROI lever is precision, not recall.
2. **Faithfulness 0.812 is healthy.** The alert threshold is **0.80**; we are ~1.2pp **above** it — clear of a misleading-answer crisis, though with less margin than the earlier "4pp below 0.85" framing implied. No urgent investment here, but the small margin is worth watching.
3. **answer_relevance flat at 0.71.** The eval bypasses the hook's query rewriter, so this is "does the retrieved context address the literal question" — and it didn't move because the embedding model is the same and chunks are still BGE-M3-matched. The hook-level rewrite WILL move this on real chat turns; just not measurable here.

| Idea | When to consider | Source | Voys signal today |
|---|---|---|---|
| HyDE (Hypothetical Document Embeddings) | RAGAS shows low context-precision on short technical queries | [HyDE — Pondhouse](https://www.pondhouse-data.com/blog/advanced-rag-hypothetical-document-embeddings) | Plausible candidate — Voys precision is 0.37, room to grow |
| GraphRAG community-summaries | Users ask cross-document synthesis questions ("what changed between Q1 and Q3 reports") | [Microsoft GraphRAG](https://microsoft.github.io/graphrag/) | Wait for production traces — chat suite is too small to detect synthesis demand |
| Agentic RAG with query decomposition | Complex multi-hop queries surface in production traces | [Agentic RAG Patterns 2026](https://www.digitalapplied.com/blog/agentic-rag-patterns-multi-step-reasoning-guide) | Wait for production — most Voys queries are single-hop today |

**Recommendation:** focus on launch. Re-measure after 4 weeks of real customer queries. Tier 3 picks itself based on which precision-failure mode dominates.

---

## Sequencing — final state

```
DONE     ─ SPEC-RAG-EVAL-001              (RAGAS harness)            [SHIPPED 2026-05-05]
            └─ baseline-v4: precision 0.231, recall 0.253, faithfulness NaN
DONE     ─ SPEC-RAG-CONTEXTUAL-001        (Anthropic chunk-context)  [SHIPPED 2026-05-05]
DONE     ─ SPEC-RAG-QUERY-REWRITE-001     (single-call rewrite+classify hook)  [SHIPPED 2026-05-05]
DONE     ─ SPEC-RAG-PARENT-CHILD-001      (small/large chunking)     [SHIPPED 2026-05-05]
DONE     ─ SPEC-RAG-TAXONOMY-001          (multi-KB classifier+filter) [SHIPPED 2026-05-05]
DONE     ─ post_pr_abcdefg_v1: precision 0.372 (+61%), recall 0.642 (+154%), faithfulness 0.812
NEXT     ─ Re-measure on production traces (4 weeks post-launch).
DECIDE   ─ Tier 3 picked based on dominant failure mode in production data:
            • short-technical query precision low?  → HyDE
            • cross-doc synthesis demand visible?   → GraphRAG community-summaries
            • multi-hop reasoning visible?           → Agentic RAG
            • all three healthy?                     → done; close roadmap
```

---

## Constraints we will not break

- **Reranker stays.** The Infinity cross-encoder is the highest-ROI single component. None of the shipped improvements remove it; PR A explicitly fixed reranker parity (chunks now feed the reranker `context_prefix + text`, the same shape that gets stored in Qdrant).
- **Per-tenant scoping stays at retrieval time.** Adding query rewriting or HyDE must not bypass `_scope_filter()`. Verified in every SPEC's acceptance criteria.
- **No regression on cost.** The combined Tier 1+2 stack adds an estimated 10-15% per-query token cost (mostly the rewrite-and-classify single LLM call). Within the < 30% cap stated when the roadmap was accepted. Eval-harness embedding cost is amortised by the LiteLLM proxy's klai-bge-m3 batching.
- **Fail-open everywhere.** Every new layer has a documented degraded path. Outages in the rewrite LLM, the taxonomy lookup, the parent_chunks table, or the Redis cache all degrade gracefully to "old retrieval behaviour" — never to "no retrieval".

---

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

**Status (2026-05-05 — closing snapshot):**
- Roadmap accepted 2026-05-04, Tier 1 + Tier 2 SHIPPED 2026-05-05.
- 7 PRs landed in one day: #347 (PR A reranker/document_text/lingua), #349 (PR B taxonomy multi-KB), #350 (PR C ragas.metrics.collections), #352 (PR D 18 stale tests unblocked), #357 (PR E rebuild_kb parent_chunk_id), #358 (PR F embedding_factory + polish), #359 (PR G faithfulness max_tokens).
- Voys-support measured deltas: `context_precision +61%`, `context_recall +154%`, `faithfulness NaN → 0.812`, `answer_relevance flat`.
- All 5 SPECs in Tier 1 + Tier 2 closed. Tier 3 deferred until 4 weeks of post-launch production traces.
