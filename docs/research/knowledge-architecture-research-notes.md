# Knowledge architecture: research notes (March to May 2026)

These passages stood in `docs/architecture/klai-knowledge-architecture.md` until the documentation was consolidated on 12 November 2026. That document now describes only what runs, and the design that is not built lives in `docs/architecture/product-gaps-backlog.md`. What follows is the research behind several of those decisions, moved here as it stood (section numbers included, two table cells reworded), because it stood nowhere else. It is a record of what was known and recommended at the time, not a description of the system: model names, versions and "current state" lines are those of spring 2026. The full original document is in the git history (`git show acd6c377c:docs/architecture/klai-knowledge-architecture.md`).

Related research in this directory: `knowledge-system-fundamentals.md` and `knowledge-pipeline-architecture.md` (the two source studies), `link-graph/` (graph retrieval), `assertion-modes/` (epistemic labels and weights), `models/llm-selection-knowledge-graph.md` (model choice for graph extraction).

## Graph retrieval: the verdicts and the gate that was proposed

**Original decision rationale (V1 gate):** do not activate the graph layer until query analysis justifies it for B2B knowledge base query patterns.

Research finding (GraphRAG-Bench, ICLR 2026; RAG vs. GraphRAG systematic evaluation, 2025; SAP enterprise study, 2025):

Graph RAG provides measurable benefit **only for multi-hop relational queries** — queries that require traversing entity relationships across multiple documents (e.g., "which features are affected by the vendor who supplies component X?"). For single-hop factual queries ("what is the return policy?") and procedural queries ("how do I reset my password?") — which constitute the majority of B2B knowledge base traffic — graph RAG either matches or underperforms hybrid vector retrieval. MS-GraphRAG in global mode regressed from 60.92% to 36.92% on fact retrieval in one benchmark: a catastrophic result.

**The LightRAG "90% fewer tokens" claim is inverted.** GraphRAG-Bench measured LightRAG at ~100,000 tokens per query prompt vs. ~954 for vanilla RAG — a 100× overhead, not a 90% reduction. The claim was a misreading of a retrieval-phase comparison on a specific large corpus. At GPT-4o pricing, LightRAG at 100K tokens/query costs $0.30–$1.00 per query. This is untenable at volume.

**LightRAG is also pre-production for self-hosted B2B:** 2,000+ open GitHub issues, upgrade breakage in production causing "extremely long downtime" (issue #2255), entity extraction failures with models below 32B parameters, and query latency degrading from 2s to 15s+ at 10,000 documents without tuning.

**Gate condition for adding a graph layer:**
Before building any graph infrastructure, sample 200 real user queries from the platform and classify them as single-hop factual / procedural / multi-hop relational. If fewer than 20% are multi-hop relational, do not add a graph layer. If multi-hop queries are significant, evaluate **HippoRAG2 + SpaCy-based construction**:
- SpaCy for entity/relationship extraction at ingest: zero LLM cost, achieves 94% of LLM-based construction quality (SAP study)
- HippoRAG2 Personalized PageRank traversal at query time: ~1,000 tokens/query (vs. LightRAG's 100,000), documented multi-hop recall improvement of +5–13pp on multi-hop benchmarks, no regression on simple queries

**FastGraphRAG** (circlemind-ai, MIT) is an alternative: ~4,200 tokens/query, incrementally updatable, but no published accuracy benchmarks.

Do not use full Microsoft GraphRAG (community reports too expensive, no benefit for topically narrow corpora) or LightRAG as primary graph system.

## Taxonomy

### 6.2 The "self-managing taxonomy" claim: verdict

The working assumption from the prior research document was: "The taxonomy manages itself. There is no maintenance process, no governance meeting, no manual migration required."

**This claim is significantly overstated. No production B2B knowledge management system operates this way.**

Research findings (Taxonomy Boot Camp 2024; Enterprise Knowledge 2024; arXiv 2502.18469, 2510.15125):
- Every production KM system studied — Intercom, Zendesk, Tettra, enterprise KM vendors — maintains a human approval step for taxonomy changes.
- Enterprise Knowledge's 2024 report is explicit: "Decision making for taxonomy management still requires human judgement regarding organizational alignment and business objectives. No AI system can autonomously manage long-term taxonomy updates."
- The conflation is between *document classification* (largely automatable once a taxonomy is defined) and *taxonomy curation* (not automatable without unacceptable error rates).

### 6.3 What BERTopic + HDBSCAN actually produces

BERTopic with HDBSCAN can discover an initial taxonomy from unlabeled documents. What it genuinely delivers vs. where it fails:

**Genuine strengths:**
- Outperforms LDA and Top2Vec on short-to-medium text in multiple comparisons (specific benchmark numbers vary by source and dataset — the "34% improvement" figure cited in prior research came from a blog post, not a peer-reviewed benchmark; treat as directionally correct, not precise)
- No predefined number of topics required — suitable for exploratory discovery
- LLM-generated cluster names rate 2.7–2.8/5 by human evaluators, vs. 1.2/5 for keyword-only labels
- Modular pipeline — each stage can be swapped independently

**Documented failure modes for production use:**
- **20–40% outlier rate**: HDBSCAN assigns documents that don't fit any cluster to class -1 (noise). In practice, 20–40% of a real-world corpus ends up unclassified with default parameters. These are not garbage documents — they are legitimate content the model failed to cluster.
- **Minimum viable corpus ~1,000 documents**: For corpora below this size, HDBSCAN produces unstable, unreliable clusters. A B2B tenant onboarding with 200 articles will get poor results.
- **Hyperparameter sensitivity**: `min_cluster_size` and `min_samples` have a "dramatic effect" on output. Too low → hundreds of micro-topics, many noise artifacts. Too high → real distinct topics get merged into coarse buckets. There is no universal default; manual tuning per corpus is required.
- **Stochastic non-reproducibility**: UMAP (used for dimensionality reduction before clustering) is stochastic by default. Two runs on the same corpus can produce different taxonomy structures. This is incompatible with production systems that need stable taxonomy IDs for downstream tagging. Fix `random_state` explicitly.
- **LLM labels are plausible, not reliable**: Labels often default to generic ("Customer Service Issues", "Technical Problems"). Human inter-rater agreement on label quality was only moderate (Cohen's Kappa = 0.66). Labels require validation before use in a customer-facing system.

**Online learning (incremental updates) has a fundamental flaw:**
BERTopic's `.partial_fit()` uses a decay mechanism that progressively reduces the weight of older documents. This causes semantic drift by design — the taxonomy you deployed at month 1 is not the same taxonomy at month 6, even if concepts have not changed. For a system that needs stable category IDs for retrieval and gap tracking, this is a production-blocking issue. Online learning should not be used as the primary taxonomy evolution mechanism without a human gate that validates changes before they are applied.

### 6.6 Technology selection

**BERTopic** remains the best-supported open-source option. Configure explicitly:
- Fix `random_state` in UMAP for reproducibility
- Tune `min_cluster_size` per corpus (start at 1–2% of document count)
- Use LLM representation model for labels, not raw c-TF-IDF keywords
- Do not rely on `.partial_fit()` online learning for production taxonomy evolution

**FASTopic** (2024): benchmarks show better coherence and stability scores than BERTopic on multiple datasets with significantly faster inference. Worth evaluating as the primary discovery model for production — less battle-tested but the performance case is credible.

**Argilla** (open source): the most practical tool for the human review queue. Supports active learning (surfaces uncertain classifications first, not random samples), integrates with Python pipelines, and has a UI accessible to non-technical reviewers. This is the interface through which taxonomy proposals and classification reviews flow.

### 6.7 Quality metrics

| Metric | What it measures | Reliability |
|---|---|---|
| Coverage (1 − outlier rate) | % of corpus assigned to non-noise clusters | High — directly measures taxonomy fitness |
| NPMI / C_V coherence | Semantic consistency of top words within topics | Moderate — correlates with human judgment but can be gamed |
| Topic diversity | Proportion of unique words across topics | Useful for detecting micro-topic overfitting |
| Contextualized Topic Coherence (CTC, 2024) | LLM-simulated human coherence judgment | Better correlation with human eval than NPMI; computationally expensive |

**The evaluation gap that remains:** No single automatic metric reliably predicts whether a taxonomy will produce *better retrieval* in production. Amazon's QuaIIT system (2024) found only 35% of auto-generated topics were rated consistent by human evaluators after quality filtering. Human sign-off on production taxonomy is still required — these metrics support the decision, they do not replace it.

---


## Open questions as they were researched

### 13.2 Bi-temporal query infrastructure [RESEARCHED — achievable in V1 with simplification]

**The basic use cases are achievable with the existing Qdrant + PostgreSQL stack using a simplified model.** No additional infrastructure required for V1.

**V1 simplified model (drop world_time entirely):**

```yaml
belief_time_start: "2024-01-15"     # ISO date
belief_time_end: "9999-12-31"       # sentinel for "currently active"; never null
system_created_at: "2024-01-16T09:32:00Z"
supersedes: null
```

Using a sentinel (`"9999-12-31"`) instead of NULL for active items makes every Qdrant range query a simple `lte`/`gte` without the `IsNullCondition` complexity and eliminates a known Qdrant `DatetimeRange` bug (issue #5641, December 2024). Store timestamps as Unix epoch integers in Qdrant payloads — fully reliable across all versions.

**Queries that work in V1:**

| Query | Execution |
|---|---|
| "What did we know about X on date D?" | Qdrant semantic search + `belief_start_ts <= D AND belief_end_ts >= D` |
| "Which items became outdated in last 6 months?" | PostgreSQL: `WHERE belief_time_end < NOW() AND belief_time_end != '9999-12-31' AND belief_time_end >= NOW() - 6 months` |
| "Evolution of position on topic Y over 2 years" | PostgreSQL: `WHERE topic_id = ? ORDER BY belief_time_start` |
| "All currently active items" | Qdrant: `belief_end_ts = 9999-12-31` as exact match, or PostgreSQL `WHERE belief_time_end = '9999-12-31'` |

**What requires V2 custom work:**
- Automatic temporal contradiction detection (Graphiti-style: LLM invalidates contradicted edges at ingest)
- `FOR PORTION OF` interval splits (splitting a valid-time interval mid-range — PostgreSQL doesn't support this natively yet)
- Temporal joins across knowledge items ("items simultaneously active with item X")
- Full transaction-time audit trail (system-time history table via `temporal_tables` extension or XTDB)

**Reference implementations:**
- [Graphiti/Zep](https://github.com/getzep/graphiti) — open source, four timestamps per graph edge, LLM-assisted contradiction detection. Best reference for the full bi-temporal knowledge graph vision.
- [XTDB](https://xtdb.com/) — native bi-temporal SQL (MPL-2.0, self-hostable). Correct long-term answer if regulatory audit trail is required. JVM service, adds operational complexity.
- [temporal_tables extension](https://github.com/arkhipov/temporal_tables) — adds system-period versioning to PostgreSQL. Defer to V2.

### 13.4 Epistemic labeling automation [RESEARCHED — simplified model + weights guidance]

> Full research: [Assertion Modes Research](../research/assertion-modes/assertion-modes-research.md) | [Assertion Mode Weights](../research/assertion-modes/assertion-mode-weights.md)

**The full 4-way classification (quote/paraphrase/interpretation/synthesis) is not solvable at acceptable accuracy with current open-source tools.** The paraphrase/interpretation boundary is genuinely ambiguous — even human annotators disagree in 25–30% of cases without access to the original source text. Do not attempt to automate this distinction in V1.

**Recommended V1 model: 3-way classification**

A simpler 3-way scheme covers most of the epistemic value without the hard boundary problem:
- **Verbatim** — text is identical or near-identical to a source (marked quotes, blockquotes)
- **Attributed** — text reflects human processing of a named source (paraphrase, interpretation of a specific source)
- **Own conclusion** — the author's own synthesis or judgment, not traceable to a single source

**Implementation: tiered approach**

**Tier 1 — Structural parser (zero ML, zero cost):** Parse markdown blockquotes, quotation marks, and citation syntax. Auto-classify as "verbatim" or "attributed." Expected coverage: 20–40% of items at ~90%+ accuracy.

**Tier 2 — Self-hosted LLM classification (remaining items):** Use Mistral Small 3.1 with 5–10 few-shot examples per category and chain-of-thought rationale. Route low-confidence results (below ~0.75) to human review. Expected accuracy: 65–75% (open-weight models perform somewhat below GPT-4o class on this task; gap is smaller with good few-shot examples).

**Tier 3 — Human review via Label Studio:** Label Studio (open source, Apache 2.0) with uncertainty-based active learning — surfaces only low-confidence items, not the full corpus. Research shows uncertainty sampling saves ~66% of annotation effort vs. random sampling. Show LLM chain-of-thought reasoning to reduce reviewer cognitive load.

**Evolution path:** Once 500–1,000 labeled examples are collected, fine-tune `deberta-v3-base` (Hugging Face, open source). This reduces inference cost by ~100× compared to Mistral and improves consistency.

**Retrieval weight guidance (from weights research):**

The [assertion mode weights research](../research/assertion-modes/assertion-mode-weights.md) established:

- **Start with flat weights (all 1.00).** The equal-weighting literature (Einhorn & Hogarth 1975, Graefe 2013) strongly shows flat weights are safest when empirical validation data doesn't exist.
- **Maximum safe spread: 0.20** for an ~85% accurate classifier (minimum weight 0.80 if max is 1.00). Wider spreads cause more harm from misclassification than benefit from correct classification.
- **Multiplicative compounding risk:** With 4 scoring dimensions at ~85% accuracy each, 48% of chunks will have at least one misclassified dimension. This is why flat weights are the safe default.
- **Evaluation gate:** Only widen the spread after 200+ chunk classification evaluation on Klai's own content + A/B retrieval test with Wilcoxon signed-rank paired testing. See [RAG Evaluation Framework](../research/evaluation/rag-evaluation-framework.md).
- **Never show assertion labels to users:** CHI 2024: confidence indicators don't improve task accuracy. ACL 2024: overconfident epistemic markers cause lasting trust damage. ACL 2025: LLMs can't produce calibrated epistemic markers. Use as internal ranking signal only.

**What to defer permanently:**
- Paraphrase/interpretation boundary without source text — unsolvable at useful accuracy
- Cross-document synthesis detection — unsolved research problem at scale
- Fine-grained paraphrase type detection — research-grade, not production-ready

**Open-source constraint note:** Research surfaced Prodigy (Explosion AI) as a leading active learning annotation tool. Prodigy is commercial ($390). Label Studio is the open-source replacement and covers the same active learning workflow.

### 13.6 Enrichment and extraction LLM [DECIDED + RESEARCHED]

**Pipeline LLM constraint:** No Anthropic/Claude API anywhere in the pipeline. Mistral API is allowed for non-sensitive enrichment tasks (contextual retrieval prefix, HyPE question generation). Transcript extraction (GDPR-sensitive source data) remains self-hosted only — no cloud API regardless of provider.

**Two tasks that need an LLM:**

| Task | Data sensitivity | Cloud API allowed? | Recommended model |
|---|---|---|---|
| Contextual Retrieval prefix generation | Chunks of organisation KB content | Yes (Mistral API) | Mistral Small 3.1 via API |
| HyPE question generation | Same as above | Yes (Mistral API) | Mistral Small 3.1 via API |
| Helpdesk transcript extraction | Raw support call data (GDPR) | No — self-hosted only | Qwen2.5-14B or Mistral Small 3.1 self-hosted |
| Epistemic label classification | Internal KB content | Yes (Mistral API) | Mistral Small 3.1 via API |

**Model selection (researched, IFEval benchmark + RTX 4090 throughput):**

**Primary recommendation: Qwen2.5-14B-Instruct (self-hosted, Q8)**
- IFEval: 81.0 (strong instruction following — the critical requirement for prefix/question generation)
- Throughput: ~64 tok/s on RTX 4090 at Q4; Q8 fits in ~15 GB leaving headroom for batching
- License: Apache 2.0
- Multilingual: strong pretraining including European languages; Dutch is not in the published eval set (a benchmark gap, not a known quality gap — validate on a sample before committing)
- Cost: effectively zero marginal cost once the server is running; breaks even vs. Mistral Nemo API at ~200K chunks/month

**Fallback / ramp-up: Mistral Small 3.1 24B via API**
- IFEval: ~83-85 (slightly higher than Qwen2.5-14B)
- Explicit Dutch language support in model documentation
- API pricing: $0.030 input / $0.110 output per MTok (~$760 per 1M chunks at 500 input + 100 output tokens each)
- Use during early phase when volume does not justify managing inference infrastructure; migrate to self-hosted Qwen2.5-14B once chunk volume exceeds ~200K/month
- Self-hosted Q4 fits in ~14 GB on RTX 4090; throughput ~45-55 tok/s (slower than Qwen2.5-14B)

**What was evaluated and discarded:**

| Model | IFEval | Reason discarded |
|---|---|---|
| Mistral Small 3.2 | No public score | $0.075/$0.200 — 2.5× more expensive than 3.1 with no evidence of proportional quality gain for these tasks |
| Gemma 3 27B | 0.904 | High IFEval but 28-38 tok/s measured throughput and poor overall quality-per-compute; takes 22 GB Q4 leaving minimal KV cache |
| Phi-4 14B base | 0.630 | IFEval 63 is a disqualifier for instruction-following tasks |
| Llama 3.1 8B | 0.804 | Weak Dutch; not suitable when Dutch is a first-class requirement |
| Mistral Nemo 12B | No public IFEval | Viable cost-minimizing fallback ($0.020/$0.040 via API) if quality testing on Dutch content confirms adequacy |
| Mistral 7B v0.3 | Low | More expensive via API than Nemo; weaker quality |

**Validation gate before production commit:** Run 100 representative Dutch chunks through both Qwen2.5-14B and Mistral Small 3.1. Compare prefix coherence and question diversity. This must happen before scale ingestion begins — the Dutch quality gap is a known unknown.

### 13.8 Web search backend [DEPLOYED — March 2026]

**Current production state (core-01):**
- SearXNG reconfigured: Google and Bing removed; Startpage + DuckDuckGo active
- Mojeek engine configured but disabled (API key needed to activate; see settings.yml)
- LibreChat webSearch enabled: SearXNG + Firecrawl scraper + Infinity reranker (bge-reranker-v2-m3, CPU)
- Infinity reranker is shared infrastructure — will also serve the Knowledge retrieval pipeline (§7.1) when deployed

The platform currently uses SearXNG (self-hosted, `http://searxng:8888`) for web search in LibreChat. Web search is a user-initiated, opt-in action — not part of the Knowledge ingestion pipeline.

**Platform constraint:** all components must be open-source and privacy-friendly. Cloud APIs that send queries to US companies (Tavily, Brave Search API) are excluded regardless of GDPR compliance claims.

#### The SearXNG problem (quality, not privacy)

SearXNG's privacy posture is fine — self-hosted, queries routed via server IP, not the user's. The problem is reliability. SearXNG has no index of its own; it aggregates upstream engines. Two of its three main sources are now effectively dead on datacenter IPs:

- **Google**: actively blocks datacenter IP ranges via TLS/HTTP2 fingerprinting. Not fixable by rotating IPs. Only 25 of 91 public instances had a working Google engine in 2025/2026.
- **Bing**: shut down its public search API in August 2025. Gone entirely.
- **DuckDuckGo**: still works but applies aggressive rate limits.

**Fix: strip Google and Bing entirely, use engines with independent indexes.** Viable non-blocking engines for SearXNG: Startpage (Google partnership, works from datacenter IPs), DuckDuckGo (rate-limited but functional), and Mojeek (independent crawler, API-based).

#### Landscape of EU privacy-friendly alternatives (researched March 2026)

| Option | Index | Self-hostable | EU/privacy fit | Quality | Verdict |
|---|---|---|---|---|---|
| **SearXNG (reconfigured)** | Metasearch (Startpage + DDG) | Yes (AGPL) | Fine — self-hosted | Medium | Short-term fix |
| **Mojeek API** | Own crawler (9B+ pages) | No (API) | UK company, EU adequacy until 2031, no query retention | Below Google, acceptable for B2B | Best strategic fit |
| **MetaGer (self-hosted)** | Metasearch | Technically yes (AGPL) | German non-profit | Same blocking problem as SearXNG | Not worth the added complexity |
| **Stract** | Own crawler | Yes (Rust) | Danish founder | Not production-ready — no deployment guide, poor quality | Promising future, not today |
| **YaCy** | Federated P2P | Yes (GPL) | Decentralized | Poor for B2B general queries | Wrong tool |
| **Whoogle** | Google proxy | Yes (MIT) | N/A | **Dead since Jan 2025** — Google required JS | Do not use |
| **Common Crawl** | Own crawl data | Theoretical | Neutral | No turnkey product exists | Engineering project, not a solution |

#### Recommended path

**Short term:** Reconfigure SearXNG — disable Google and Bing, enable Startpage + DuckDuckGo + Mojeek (via API engine). This is the lowest-effort fix and keeps everything self-hosted.

**Medium term:** Replace SearXNG with the **Mojeek API** directly. Mojeek is the only option satisfying all platform criteria: independent index (own crawler since 2004, 9B+ pages), UK company with EU adequacy (renewed December 2025, valid until 2031), no query data retained. Quality is below Google for technical queries — validate on a representative sample of Focus queries before committing. Pricing: £1–3 per 1,000 requests.

**Not pursued:** Stract (not production-ready), Whoogle (dead), YaCy (wrong use case), MetaGer self-hosted (same blocking root cause, higher complexity).

