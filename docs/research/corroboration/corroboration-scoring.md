# Corroboration Scoring for Knowledge Graph Retrieval: Research Reference

> Compiled: 2026-03-30
> Status: Decision-support document for Klai knowledge architecture
> Scope: Should Graphiti's episode counts be used as a corroboration boost in the retrieval pipeline?
> Part of: [Research Synthesis](../README.md)

---

## 1. Context

Klai's knowledge architecture uses Graphiti (by Zep) as its knowledge graph layer on top of FalkorDB. Graphiti extracts entities and relationships from ingested documents and stores them in a temporal graph. Each entity edge (relationship) maintains an `episodes` field -- a list of episode UUIDs that contributed to that fact.

An implementation plan proposes using the length of `EntityEdge.episodes` as a corroboration signal: facts mentioned by more independent source documents would receive a retrieval boost. The intuition is sound -- if three independent documents agree on a fact, that fact is more reliable than one mentioned in a single document.

This document assesses whether this is safe and beneficial to implement today, given Graphiti's current entity resolution quality, the absence of near-duplicate detection, and the small size of Klai's knowledge bases.

---

## 2. Graphiti's Entity Resolution: What It Actually Does

### 2.1 Architecture

Graphiti uses a three-tier entity resolution strategy:

| Tier | Method | When used |
|---|---|---|
| **Exact match** | Normalized string comparison | First pass -- fast path |
| **Fuzzy heuristic** | Entropy-gated MinHash + LSH on 3-gram shingles | High-entropy names (>4 chars, non-repetitive) |
| **LLM-based** | Structured LLM prompt comparing candidate names and summaries | Low-entropy names, or when heuristic is inconclusive |

The entity extraction prompt instructs the LLM to be "explicit and unambiguous in naming entities (e.g., use full names when available)." The deduplication prompt instructs the LLM that "duplicate nodes may have different names" and to examine both names and summaries. When duplicates are identified, the system generates an updated (merged) name and summary.

Source: [Graphiti GitHub](https://github.com/getzep/graphiti), [Zep technical paper (arXiv:2501.13956)](https://arxiv.org/abs/2501.13956), [Zep blog: LLM Data Extraction at Scale](https://blog.getzep.com/llm-rag-knowledge-graphs-faster-and-more-dynamic/)

### 2.2 Edge Resolution

Edge deduplication mirrors entity resolution but is constrained to edges between the same entity pairs. When a new episode mentions an existing relationship:

1. **Verbatim check**: If the fact is semantically identical after normalization, the new episode UUID is appended to the existing edge's `episodes` list (no new edge created).
2. **LLM deduplication**: The LLM determines if the new fact is a duplicate of, contradicts, or is distinct from existing edges between the same entity pair.
3. **Temporal invalidation**: If the new fact contradicts an existing one, the old edge gets `invalid_at` set rather than being deleted.

Source: [DeepWiki: Edge Operations](https://deepwiki.com/getzep/graphiti/5.3-edge-operations)

### 2.3 Known Quality Issues

| Issue | Severity | Source |
|---|---|---|
| **Duplicate entities with non-default DB names** | Critical | Custom Neo4j/FalkorDB database names cause dedup queries to miss existing entities entirely ([#875](https://github.com/getzep/graphiti/issues/875)) |
| **Bulk upload entity resolution failures** | High | `NodeResolutions` validation errors during batch ingestion -- entity resolution can completely break ([#879](https://github.com/getzep/graphiti/issues/879)) |
| **LLM-dependent variance** | Medium | Resolution quality varies significantly with LLM provider/model. Non-OpenAI models frequently produce schema validation errors ([#796](https://github.com/getzep/graphiti/issues/796), [#912](https://github.com/getzep/graphiti/issues/912)) |
| **Custom entity type properties lost** | Medium | Type-specific attributes that could aid deduplication are not persisted to graph nodes ([#567](https://github.com/getzep/graphiti/issues/567)) |
| **Low-entropy name instability** | Low-Medium | Short names (e.g., "Jan", "HR") skip heuristic matching and rely entirely on LLM, which can be inconsistent |

### 2.4 Assessment

Graphiti's entity resolution is LLM-based at its core, which makes it reasonably good at handling name variants ("Jan" vs. "Jan van der Berg") *when the LLM performs well*. However, it has no guaranteed accuracy level and no benchmarks published for entity resolution specifically. The Zep paper reports 94.8% on the Deep Memory Retrieval (DMR) benchmark, but DMR measures retrieval quality, not entity resolution precision.

**Key gap for corroboration**: There is no mechanism to assess entity resolution confidence. When "Jan" and "Jan van der Berg" are *not* merged, both accumulate separate episode counts -- inflating the apparent corroboration of connected edges. There is no way to detect this has happened without auditing the graph.

---

## 3. The Episodes Data Model

### 3.1 How Episodes Work

An episode is a single ingestion event -- a message, document chunk, JSON payload, or raw text. Each episode becomes an `EpisodicNode` in the graph.

| Field | Type | Description |
|---|---|---|
| `uuid` | UUID | Unique identifier |
| `source` | EpisodeType | `message`, `json`, or `text` |
| `source_description` | string | Free-text label (e.g., "meeting notes", "policy document") |
| `content` | string | Raw episode content |
| `entity_edges` | list[UUID] | EntityEdge UUIDs extracted from this episode |
| `created_at` | datetime | When ingested |
| `valid_at` | datetime | When the event occurred (bi-temporal) |

Source: [DeepWiki: Episode Processing](https://deepwiki.com/getzep/graphiti/5.1-episode-processing)

### 3.2 Episode-to-Edge Linking

The provenance chain works bidirectionally:

- **Forward**: `EpisodicNode.entity_edges` lists which EntityEdges were extracted from this episode.
- **Backward**: `EntityEdge.episodes` lists which EpisodicNode UUIDs support this fact.
- **Structural**: `EpisodicEdge` (relationship type `:MENTIONS`) connects EpisodicNodes to EntityNodes.

When a new episode mentions an existing fact, the episode's UUID is appended to `EntityEdge.episodes`. This means `len(edge.episodes)` represents the number of ingestion events that mentioned this specific relationship.

### 3.3 Can You Count Independent Sources?

**Technically yes, practically no.** You can count `len(EntityEdge.episodes)`, but this number represents episode count, not independent source count. The distinction matters because:

1. **One document may produce multiple episodes**: If a 10-page document is chunked into 5 episodes, and 3 of those chunks mention the same fact, the edge gets `episodes` count = 3 from a single document.
2. **Near-duplicates count separately**: Meeting notes and a meeting summary from the same meeting are different episodes. Graphiti does not detect their content overlap.
3. **No source-level deduplication**: Graphiti has no concept of "source document" above the episode level. The `source_description` field is a free-text label, not a foreign key to a deduplicated source registry.

---

## 4. Near-Duplicate Detection: State of the Art

### 4.1 Available Approaches

| Approach | Type | Accuracy | Speed | Complexity |
|---|---|---|---|---|
| **MinHash + LSH** | Lexical (n-gram Jaccard) | High for surface-level duplicates | Very fast, sub-linear | Medium (tuning b, r parameters) |
| **SimHash** | Lexical (fingerprint) | Good for exact/near-exact copies | Very fast | Low |
| **SemHash** (Model2Vec + ANN) | Semantic (embedding-based) | Captures paraphrases | Fast (~ms per text) | Low (pip install semhash) |
| **Cross-encoder similarity** | Semantic (transformer) | Highest quality | Slow (O(n^2) pairs) | High |

Sources: [Milvus blog on MinHash LSH](https://milvus.io/blog/minhash-lsh-in-milvus-the-secret-weapon-for-fighting-duplicates-in-llm-training-data.md), [SemHash GitHub](https://github.com/MinishLab/semhash), [HuggingFace dedup blog](https://huggingface.co/blog/dedup)

### 4.2 What "Independent" Means

For corroboration to be meaningful, two episodes must be *independently authored*. In Klai's context:

| Scenario | Independent? | Why |
|---|---|---|
| Policy document + separate FAQ document mentioning same policy | Yes | Different authors, different purposes |
| Meeting notes + meeting summary from same meeting | No | Derived from the same event, likely same author |
| Two different blog posts referencing the same internal report | Partially | Independent observations, but shared underlying source |
| Same document chunked into multiple episodes | No | Same source, just split for processing |

**Typical threshold**: In the literature, documents with >0.85 Jaccard similarity (MinHash) or >0.90 cosine similarity (embeddings) are considered near-duplicates. For corroboration purposes, the threshold should be more aggressive (>0.70 semantic similarity = "not independent").

### 4.3 Practical Recommendation for Klai

SemHash is the most practical option for Klai's scale (10-100 documents per org):

- **Lightweight**: Pure Python, ~8MB model, no infrastructure requirements
- **Semantic**: Catches paraphrases and reformulations, not just lexical overlap
- **Fast enough**: At Klai's scale, even O(n^2) comparisons would be feasible
- **Explainable**: Returns similarity scores and matched pairs for debugging

Integration point: Run at ingest time, before Graphiti episode creation. Tag episodes with a `source_cluster_id` derived from near-duplicate clustering. When counting corroboration, count distinct `source_cluster_id` values, not raw episode counts.

---

## 5. Corroboration in Production Systems

### 5.1 Google Knowledge Vault

Google's Knowledge Vault (Dong et al., KDD 2014) is the canonical example of corroboration-based confidence scoring at scale.

**How it works:**
- Multiple extractors (text analysis, DOM tree parsing, HTML tables, human annotations) independently extract facts from web pages.
- A probabilistic fusion model combines extractor outputs with prior knowledge (from Freebase) to compute calibrated confidence scores.
- Facts corroborated by multiple independent extractors receive higher confidence scores.
- 1.6B total facts extracted; 271M "confident facts" (>0.9 probability).

**Key insight**: Knowledge Vault explicitly models *extractor accuracy* and *source independence*. The fusion model knows that two facts from the same web page are not independent, and that some extractors are more reliable than others. Raw count of sources is not used -- the probabilistic model weights by source quality and independence.

**Scale requirement**: Knowledge Vault operates on billions of web pages. The statistical foundation works *because* there are many independent sources for popular facts. For rare facts (long-tail entities), the system falls back to priors from the existing knowledge base.

Source: [Google Research: Knowledge Vault](https://research.google/pubs/knowledge-vault-a-web-scale-approach-to-probabilistic-knowledge-fusion/), [Dong et al. 2014](https://dl.acm.org/doi/10.1145/2623330.2623623)

### 5.2 Wikidata

Wikidata's quality framework uses source counting as a quality signal, but with important nuance:

- A "well-referenced item" should have sources from "more than one, non-correlated reference works."
- The **RQSS** (Referencing Quality Scoring System) scores referencing quality across multiple dimensions: completeness, consistency, relevance, trustworthiness. The overall referencing quality in Wikidata subsets is 0.58 out of 1.0.
- **ProVe** (automated Provenance Verification) uses LLMs to verify whether references actually support claims, producing a score from -1 to +1.
- Wikidata explicitly allows contradictory information as long as each claim is referenced -- the number of references alone does not determine truth.

**Key insight**: Even in a mature, human-curated knowledge base, raw reference counting is insufficient. Wikidata invests heavily in *quality* of references (do they actually support the claim?) rather than *quantity* alone.

Source: [Wikidata:Item quality](https://www.wikidata.org/wiki/Wikidata:Item_quality), [RQSS paper](https://www.semantic-web-journal.net/content/rqss-referencing-quality-scoring-system-wikidata), [Wikidata:ProVe](https://www.wikidata.org/wiki/Wikidata:ProVe)

### 5.3 RAG Systems Using Graph Corroboration

No production RAG system was found that uses graph-based corroboration counts as a retrieval boost factor. The closest approaches in the 2025 RAG landscape:

| System | What it does | Relevance |
|---|---|---|
| **CRAG** (Corrective RAG) | Scores retrieved documents with a confidence evaluator; classifies as correct/incorrect/ambiguous | Per-document confidence, not cross-document corroboration |
| **GraphRAG** (Microsoft) | Community summarization over entity clusters | Aggregation, but not source-count-based scoring |
| **RankRAG** | Unified reranking + generation | Relevance scoring, not corroboration |
| **KG-RAG** (dual-pathway) | Knowledge graph paths + text retrieval in parallel | Structural relevance, not source counting |

**Key insight**: The industry has invested heavily in reranking and relevance scoring, but not in source-count-based corroboration for RAG. This may indicate that the problem is harder than it appears, or that simpler signals (semantic similarity, structural context) provide sufficient quality.

---

## 6. Risk Assessment

### 6.1 Risks of Implementing Corroboration Scoring Now

| Risk | Likelihood | Impact | Description |
|---|---|---|---|
| **False inflation from near-duplicates** | High | Medium | Meeting notes + meeting summary both count as 2 episodes for the same fact. Without near-duplicate detection, common organizational document patterns (draft -> final, notes -> summary, report -> presentation) inflate counts. |
| **Entity resolution errors creating false corroboration** | Medium | High | If "Jan" and "Jan van der Berg" are not merged, edges connected to each get separate episode counts. Queries traversing through one miss corroboration on the other, or worse, a merged query double-counts. |
| **Small KB nullification** | High | Medium | With 10-100 documents per org, most entities appear in 1-2 documents. The corroboration signal is sparse: nearly all edges will have `episodes` count of 1, making the boost meaningless for most queries. |
| **Popular-but-wrong amplification** | Low | High | Multiple documents repeating the same error (e.g., outdated project deadline propagated across meeting notes, plans, and status reports) get boosted. The error appears *more* reliable because it has high corroboration. |
| **Chunking artifacts** | High | Low-Medium | A single document chunked into multiple episodes can inflate edge counts for facts mentioned across chunks. This is a systematic bias, not random noise. |
| **LLM model sensitivity** | Medium | Medium | Switching the LLM used for entity resolution (e.g., for cost optimization) could change entity resolution quality, silently altering corroboration counts across the entire graph. |

### 6.2 Compounding Effects

Entity resolution errors and near-duplicate inflation compound multiplicatively:

- Base scenario: 3 independent documents mention "Project Alpha deadline is March 2026"
- With one near-duplicate pair (meeting notes + summary): count inflates to 4
- With entity resolution miss ("Project Alpha" vs "Alpha project" not merged): the corroboration is split -- one entity has 2, the other has 2, instead of one entity with 4
- Combined: the "correct" entity might show count=2 while a "wrong" entity (with the near-duplicate pair) shows count=2. No clear signal.

### 6.3 The GraphRAG Error Compounding Problem

Research on GraphRAG entity resolution failures shows that at 85% entity resolution accuracy, multi-hop query accuracy follows (0.85)^n. At 5 hops, fewer than half of answers are trustworthy. Using episode counts as a boost factor adds another error source to this chain: now the boost itself can be wrong, further degrading multi-hop quality.

Source: [Sowmith Mandadi: GraphRAG Entity Resolution](https://www.sowmith.dev/blog/graphrag-entity-disambiguation)

### 6.4 Benefits If Implemented Correctly

| Benefit | Conditions Required |
|---|---|
| Higher ranking for well-established facts | Near-duplicate detection + entity resolution validation |
| Reduced hallucination for common queries | Large enough KB that most important facts appear in 3+ independent sources |
| Implicit quality signal for knowledge curation | Dashboard showing corroboration counts helps editors identify poorly-sourced claims |
| Competitive differentiation | No production RAG system currently uses this -- first-mover advantage if done well |

---

## 7. Prerequisites for Safe Corroboration Scoring

Before corroboration scoring can be reliably deployed, the following must be in place:

### P1: Near-Duplicate Detection (Required)

Without this, corroboration counts are systematically inflated by organizational document patterns. Implementation: Run SemHash or similar at ingest time; cluster episodes by semantic similarity; count distinct clusters, not raw episodes.

**Estimated effort**: 2-3 days. SemHash is a pip install; the integration point is the knowledge ingest pipeline before Graphiti episode creation.

### P2: Entity Resolution Validation (Required)

There is currently no way to know how accurate Graphiti's entity resolution is for Klai's data. Before trusting episode counts, validate:

1. Ingest a representative corpus (50-100 documents from a real org).
2. Manually review the entity graph: count missed merges, false merges, and total entities.
3. Compute precision and recall of entity resolution.
4. Establish a minimum quality threshold (e.g., >90% precision, >85% recall) before enabling corroboration.

**Estimated effort**: 3-5 days (mostly manual review).

### P3: Source-Level Grouping (Required)

Graphiti's episode granularity is too fine for corroboration. A single document split into 5 chunks produces 5 episodes. The system needs a `source_document_id` concept above the episode level:

- Tag each episode with the source document ID at ingest time.
- When counting corroboration, count distinct `source_document_id` values, not `len(EntityEdge.episodes)`.
- Combine with P1: episodes from near-duplicate documents share a `source_cluster_id`.

**Estimated effort**: 1-2 days (metadata addition to the ingest pipeline).

### P4: Minimum KB Size Threshold (Recommended)

For knowledge bases with <20 documents, disable corroboration boosting entirely. The signal is too sparse to be useful, and the risk of false inflation is high. Activate corroboration only when:

- The KB has >20 unique source documents (after near-duplicate clustering).
- At least 30% of entities have 2+ independent source documents.

### P5: Corroboration Cap (Recommended)

Implement a logarithmic or capped boost to prevent popular-but-wrong amplification:

```
boost = min(log2(independent_source_count), MAX_BOOST)
```

Where `MAX_BOOST` is 2-3x. This ensures that a fact mentioned in 20 documents does not get 20x the boost of one mentioned in 1 document. The marginal value of additional corroboration should diminish.

### P6: Monitoring Dashboard (Recommended)

Surface corroboration statistics in the knowledge management UI:

- Distribution of corroboration counts per entity/edge.
- Top entities by corroboration count (sanity check: are these the entities you'd expect?).
- Entities with suspiciously high corroboration (potential near-duplicate inflation).
- Entity resolution merge/split log for audit.

---

## 8. Graphiti Capability Assessment

| Capability | Status | Notes |
|---|---|---|
| **Episode-to-edge provenance** | Available | `EntityEdge.episodes` tracks UUIDs; `EpisodicEdge` provides structural `:MENTIONS` links |
| **Episode counting per edge** | Available (raw) | `len(EntityEdge.episodes)` works, but counts episodes not independent sources |
| **Source document grouping** | Not available | No concept of source document above episode level; `source_description` is free text |
| **Near-duplicate detection** | Not available | Not part of Graphiti; must be implemented externally |
| **Entity resolution confidence** | Not available | No score or metric; resolution is binary (merged or not) |
| **Entity resolution quality** | Unvalidated | LLM-based, reasonable in theory, but no benchmarks for ER specifically; known bugs with custom DB names |
| **Independence assessment** | Not available | No mechanism to determine if two episodes are independently authored |
| **Corroboration scoring** | Not available | Not a Graphiti feature; would need to be built as a custom retrieval signal |

**Summary**: Graphiti provides the raw data (`EntityEdge.episodes`) needed for corroboration counting, but none of the safeguards (near-duplicate detection, source grouping, independence assessment, ER validation) needed to make that count meaningful.

---

## 9. Recommendation

### Verdict: Defer implementation. Build prerequisites first.

Corroboration scoring is a promising signal for retrieval quality, and Klai has a strategic opportunity to be the first production RAG system to implement it well. However, implementing it *now* -- without near-duplicate detection, source grouping, or entity resolution validation -- would introduce a signal that is more noise than information.

### Phased approach

**Phase 0 (now): Collect data, don't act on it.**
- Add `source_document_id` to the episode metadata at ingest time (P3). Low effort, zero risk.
- Log `EntityEdge.episodes` counts to a monitoring dashboard (P6). Observe the distribution without using it for retrieval.
- This costs almost nothing and creates the data foundation for later phases.

**Phase 1 (when a customer has 50+ documents): Validate entity resolution.**
- Run the ER validation protocol (P2) against a real customer's knowledge base.
- If ER precision is >90% and recall is >85%, proceed to Phase 2.
- If not, invest in improving ER quality (custom entity types, domain-specific resolution prompts) before enabling corroboration.

**Phase 2 (after Phase 1 passes): Implement near-duplicate detection.**
- Integrate SemHash (P1) into the ingest pipeline.
- Cluster episodes by semantic similarity; assign `source_cluster_id`.
- Recompute corroboration counts as `count(distinct source_cluster_id per edge)`.

**Phase 3 (after Phase 2): Enable corroboration boost with safeguards.**
- Implement the capped boost formula (P5).
- Only enable for KBs meeting the minimum size threshold (P4).
- A/B test: compare retrieval quality with and without the corroboration boost on real queries.
- Monitor for popular-but-wrong amplification via the dashboard (P6).

### Why not build it now?

1. **The signal is unreliable today.** Raw `EntityEdge.episodes` count conflates independent sources, near-duplicates, and chunking artifacts. Using it as a boost factor would sometimes improve results and sometimes degrade them, with no way to predict which.

2. **The knowledge bases are too small.** At 10-100 documents per org, corroboration is sparse. Most edges will have episode count = 1. The boost would affect very few queries, and those it affects might be the wrong ones.

3. **There are no production precedents.** No existing RAG system uses source-count corroboration as a retrieval signal. Google Knowledge Vault uses it, but with sophisticated probabilistic fusion, extractor accuracy modeling, and source independence assessment -- infrastructure far beyond raw episode counting.

4. **The existing pipeline already has strong signals.** Klai's retrieval pipeline uses content_type weighting, semantic reranking, and knowledge graph structure. These are well-understood and validated. Adding an unreliable corroboration signal could interfere with them.

5. **Phase 0 is nearly free and preserves optionality.** By adding `source_document_id` and monitoring counts now, Klai can build the data foundation without risk. When the prerequisites are met, the feature can be enabled quickly.

---

## 10. Sources

- [Graphiti GitHub Repository](https://github.com/getzep/graphiti)
- [Zep: A Temporal Knowledge Graph Architecture for Agent Memory (arXiv:2501.13956)](https://arxiv.org/abs/2501.13956)
- [DeepWiki: Graphiti Architecture](https://deepwiki.com/getzep/graphiti)
- [DeepWiki: Episode Processing](https://deepwiki.com/getzep/graphiti/5.1-episode-processing)
- [DeepWiki: Edge Operations](https://deepwiki.com/getzep/graphiti/5.3-edge-operations)
- [Zep Blog: LLM Data Extraction at Scale](https://blog.getzep.com/llm-rag-knowledge-graphs-faster-and-more-dynamic/)
- [Graphiti Issue #875: Duplicate Entities with Custom DB Names](https://github.com/getzep/graphiti/issues/875)
- [Graphiti Issue #879: Bulk Upload Entity Resolution Failures](https://github.com/getzep/graphiti/issues/879)
- [Google Research: Knowledge Vault (KDD 2014)](https://research.google/pubs/knowledge-vault-a-web-scale-approach-to-probabilistic-knowledge-fusion/)
- [Wikidata: Item Quality Guidelines](https://www.wikidata.org/wiki/Wikidata:Item_quality)
- [RQSS: Referencing Quality Scoring System for Wikidata](https://www.semantic-web-journal.net/content/rqss-referencing-quality-scoring-system-wikidata)
- [Wikidata: ProVe Automated Provenance Verification](https://www.wikidata.org/wiki/Wikidata:ProVe)
- [GraphRAG Entity Resolution Failures (Sowmith Mandadi)](https://www.sowmith.dev/blog/graphrag-entity-disambiguation)
- [SemHash: Semantic Deduplication Library](https://github.com/MinishLab/semhash)
- [Milvus Blog: MinHash LSH for Duplicate Detection](https://milvus.io/blog/minhash-lsh-in-milvus-the-secret-weapon-for-fighting-duplicates-in-llm-training-data.md)
- [HuggingFace: Large-scale Near-deduplication Behind BigCode](https://huggingface.co/blog/dedup)
- [FalkorDB: Graphiti Integration](https://docs.falkordb.com/agentic-memory/graphiti.html)
- [Neo4j Blog: Graphiti Knowledge Graph Memory](https://neo4j.com/blog/developer/graphiti-knowledge-graph-memory/)
- [Medium: Knowledge Graphs and Misinformation](https://medium.com/@dmccreary/how-knowledge-graphs-promote-fake-news-362947220ea8)

---

## Related Documents

- [Evidence-Weighted Knowledge Research](../foundations/evidence-weighted-knowledge.md) — broader research context (Topic 1: corroboration, Topic 3: probabilistic KGs)
- [Implementation Plan](../implementation/implementation-plan.md) — corroboration boost implementation (Gap 3, deferred)
- [Assertion Modes Research](../assertion-modes/assertion-modes-research.md) — the assertion mode dimension (complements corroboration)
- [RAG Evaluation Framework](../evaluation/rag-evaluation-framework.md) — how to validate corroboration impact
