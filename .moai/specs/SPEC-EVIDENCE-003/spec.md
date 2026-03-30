# SPEC-EVIDENCE-003: Corroboration Scoring for Knowledge Graph Retrieval

> Status: Draft
> Priority: MEDIUM
> Created: 2026-03-30
> Research: `docs/research/corroboration/corroboration-scoring.md`
> Architecture: `docs/architecture/klai-knowledge-architecture.md`
> Depends on: SPEC-EVIDENCE-001 (evaluation framework for A/B testing in Phase 3)
> Scope: `klai-retrieval-api/`, `deploy/knowledge-ingest/`

---

## Context

Klai's knowledge architecture uses Graphiti (by Zep) on FalkorDB as its knowledge graph layer. Each `EntityEdge` maintains an `episodes` field -- a list of episode UUIDs that contributed to that fact. The intuition behind corroboration scoring is sound: if three independent documents agree on a fact, that fact is more reliable than one mentioned in a single document.

However, research (see `docs/research/corroboration/corroboration-scoring.md`) reveals that raw `EntityEdge.episodes` count is unreliable as a corroboration signal today:

1. **Chunking inflation**: One document chunked into 5 episodes produces 5 episode counts for facts mentioned across chunks.
2. **Near-duplicate inflation**: Meeting notes + meeting summary = 2 episodes for the same event, not independent sources.
3. **No source-level grouping**: Graphiti has no concept of `source_document_id` above the episode level.
4. **Unvalidated entity resolution**: Graphiti's ER is LLM-based with no published benchmarks. Known bugs exist (#875, #879). Missed merges split corroboration counts across duplicate entities.
5. **No production precedent**: No RAG system uses source-count corroboration as a retrieval boost. Google Knowledge Vault uses probabilistic fusion far beyond raw counting.

**Business context**: Voys (customer) is loading a KB with 190 pages immediately. This makes corroboration scoring more relevant than initially assumed -- the "small KB" argument weakens. The Voys dataset is an ideal validation corpus for Phase 1.

This SPEC defines a four-phase approach: collect data safely (Phase 0), validate prerequisites with real data (Phase 1), build safeguards (Phase 2), and enable the boost with measurement (Phase 3). Each phase has explicit gate criteria.

---

## Goal

Build the data foundation and safeguards required for reliable corroboration scoring. Phase 0 is actionable now (zero risk). Phase 1 validates entity resolution quality using Voys' 190-page KB. Phases 2-3 implement near-duplicate detection and the actual retrieval boost, gated on Phase 1 results.

---

## Phase 0 -- Data Collection (actionable now)

### R0.1 -- Source document ID on episodes

**When** a document is ingested into the knowledge graph, **the system shall** tag each resulting episode with a `source_document_id` metadata field containing the ID of the originating knowledge base page.

This enables counting distinct source documents per edge, rather than raw episode counts. The field must be set at ingest time, before Graphiti episode creation.

### R0.2 -- Episode count observability

**When** entity edges exist in the graph, **the system shall** log the distribution of `len(EntityEdge.episodes)` per knowledge base to structured logs (structlog).

Log fields:
- `kb_id`: knowledge base identifier
- `edge_count`: total entity edges in the KB
- `episode_count_p50`: median episode count per edge
- `episode_count_p90`: 90th percentile
- `episode_count_max`: maximum
- `edges_with_1_episode_pct`: percentage of edges with only 1 episode

This runs as a periodic background task or on-demand admin endpoint, not on every query.

### R0.3 -- Metadata passthrough for source_document_id

**When** Graphiti returns entity edges during graph search, **the system shall** include `source_document_id` values from linked episodes in the retrieval result, so downstream scoring can access them.

### Acceptance criteria (Phase 0)

- [ ] `source_document_id` is set on every episode created during knowledge ingest
- [ ] Existing ingest pipeline passes `source_document_id` through to Graphiti episode metadata
- [ ] Episode count distribution is logged per KB (structured log with fields above)
- [ ] Graph search results include `source_document_id` data from linked episodes
- [ ] No retrieval behavior changes -- this phase is observability only

### Risk assessment (Phase 0)

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Metadata field not persisted by Graphiti | Low | Low | Verify with integration test on dev; Graphiti supports custom episode metadata |
| Logging overhead on large KBs | Low | Low | Run as background task, not per-query; use sampling for very large KBs |

### Estimated effort: 1-2 days

---

## Phase 1 -- Entity Resolution Validation (actionable when Voys data is loaded)

### R1.1 -- ER validation protocol

**When** a knowledge base has >50 pages, **the system shall** support an admin-triggered entity resolution audit that:

1. Exports the full entity graph (nodes + edges) for a given KB
2. Produces a review format (CSV or JSON) listing all entity pairs with similarity > 0.7 (by name embedding)
3. Flags candidate missed merges (similar names, different node UUIDs) and candidate false merges (same node, unrelated content)

### R1.2 -- ER quality metrics

**When** manual review of the ER audit is complete, **the system shall** compute:

- **Precision**: `correctly_merged / total_merged` (were merge decisions correct?)
- **Recall**: `correctly_merged / (correctly_merged + missed_merges)` (were all duplicates found?)

### Gate criteria (Phase 0 -> Phase 1)

- Voys KB is loaded with 190+ pages
- `source_document_id` is present on all episodes (Phase 0 complete)

### Gate criteria (Phase 1 -> Phase 2)

- ER precision > 90% on Voys dataset
- ER recall > 85% on Voys dataset
- If either threshold is not met: invest in ER improvement (custom entity types, domain-specific resolution prompts, Graphiti version upgrades) before proceeding. Do not proceed to Phase 2 with unreliable entity resolution.

### Acceptance criteria (Phase 1)

- [ ] ER audit tool exists (admin endpoint or script)
- [ ] Audit run on Voys 190-page KB
- [ ] Manual review completed (sample of 100+ entity pairs)
- [ ] Precision and recall computed and documented
- [ ] Go/no-go decision for Phase 2 recorded in this SPEC or a linked decision document
- [ ] No retrieval behavior changes -- this phase is validation only

### Risk assessment (Phase 1)

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| ER quality below threshold | Medium | High (blocks Phase 2+3) | Budget time for ER improvement; investigate Graphiti config tweaks, LLM model changes, or custom dedup prompts |
| Manual review is subjective | Medium | Low | Create clear review guidelines; use two independent reviewers on a sample subset |
| Voys data not representative of future KBs | Low | Medium | Document the domain characteristics; re-validate when a structurally different KB is loaded |
| Graphiti bugs (#875, #879) surface during bulk load | Medium | Medium | Test with Voys data on staging first; check Graphiti release notes for fixes |

### Estimated effort: 3-5 days (includes manual review)

---

## Phase 2 -- Near-Duplicate Detection (after Phase 1 passes)

### R2.1 -- SemHash integration at ingest time

**When** a document is ingested, **the system shall** compute a semantic fingerprint of the episode content using SemHash (Model2Vec + ANN) and compare it against existing episodes in the same knowledge base.

Episodes with semantic similarity > 0.70 to an existing episode are assigned to the same `source_cluster_id`. New episodes below the threshold get a new cluster ID.

### R2.2 -- Source cluster ID on episodes

**When** near-duplicate detection assigns a cluster, **the system shall** store `source_cluster_id` as metadata on the episode, alongside `source_document_id`.

The two fields serve complementary roles:
- `source_document_id`: same document chunked into multiple episodes
- `source_cluster_id`: semantically overlapping content from different documents (e.g., meeting notes + meeting summary)

### R2.3 -- Distinct source count computation

**When** the system computes corroboration for an entity edge, **the system shall** count distinct `source_cluster_id` values (not raw `len(EntityEdge.episodes)`).

```
corroboration_count = count(distinct source_cluster_id for episodes in edge.episodes)
```

This count is stored or computed at query time but is NOT yet used for retrieval scoring (that is Phase 3).

### R2.4 -- Backfill for existing episodes

**When** Phase 2 is deployed, **the system shall** provide a one-time backfill script that:

1. Loads all episodes for a KB
2. Computes SemHash fingerprints
3. Clusters episodes by semantic similarity
4. Assigns `source_cluster_id` to all existing episodes

### Gate criteria (Phase 1 -> Phase 2)

- Phase 1 gate passed (ER precision > 90%, recall > 85%)

### Acceptance criteria (Phase 2)

- [ ] SemHash integrated into the ingest pipeline
- [ ] `source_cluster_id` assigned to every new episode at ingest time
- [ ] Backfill script exists and has been tested on dev
- [ ] Distinct source count computation works correctly (verified with test cases: single-doc chunks, near-duplicate docs, independent docs)
- [ ] Similarity threshold (0.70) is configurable
- [ ] Corroboration count is observable (logged or accessible via admin endpoint) but NOT used for retrieval scoring
- [ ] No retrieval behavior changes -- this phase is infrastructure only

### Risk assessment (Phase 2)

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| SemHash threshold too aggressive (clusters independent docs) | Medium | Medium | Start at 0.70; validate on Voys data; adjust based on manual review of cluster assignments |
| SemHash threshold too lenient (misses near-duplicates) | Medium | Low | Same: validate on Voys data; err on the side of too aggressive (false cluster > missed duplicate for corroboration purposes) |
| Backfill performance on large KBs | Low | Low | Run as async background task; SemHash is fast (~ms per text) |
| Model2Vec model size in production | Low | Low | ~8MB, negligible compared to embedding models already deployed |

### Estimated effort: 2-3 days

---

## Phase 3 -- Corroboration Boost with Safeguards (after Phase 2)

### R3.1 -- Capped corroboration boost formula

**When** entity edges are scored during retrieval, **the system shall** apply a corroboration boost based on distinct source count:

| Distinct sources | Boost factor |
|---|---|
| 1 | 1.00 (baseline) |
| 2 | 1.10 |
| 3 | 1.18 |
| 4+ | 1.25 (cap) |

Formula: `boost = min(1.0 + 0.10 * ln(distinct_sources), 1.25)`

The boost multiplies the existing evidence-tier score from SPEC-EVIDENCE-001:

```
final_score = reranker_score * content_type_weight * assertion_weight * temporal_decay * corroboration_boost
```

### R3.2 -- Minimum KB size threshold

**When** a knowledge base has fewer than 20 unique source documents (after near-duplicate clustering), **the system shall** disable corroboration boosting for that KB (all edges get boost = 1.00).

Additionally, corroboration boosting is only activated when at least 30% of entities in the KB have 2+ distinct source documents. This prevents the boost from being dominated by a few highly-corroborated facts in an otherwise sparse graph.

### R3.3 -- Feature flag

**The system shall** gate corroboration boosting behind `EVIDENCE_CORROBORATION_ENABLED=true/false` (default: false).

### R3.4 -- Shadow scoring

**When** corroboration boosting is first deployed, **the system shall** compute both the boosted and unboosted scores, serve the unboosted score to users, and log the boosted score for offline comparison.

This enables A/B analysis without affecting production retrieval quality.

### R3.5 -- A/B evaluation (depends on SPEC-EVIDENCE-001)

**When** shadow scoring data is collected for at least 100 queries, **the system shall** evaluate the corroboration boost using the RAGAS evaluation framework from SPEC-EVIDENCE-001:

1. Compare retrieval quality (Context Precision, NDCG@10, Recall@10) between flat and corroboration-boosted scoring
2. Run Wilcoxon signed-rank test on paired results
3. Measure per-query impact: identify queries where corroboration improved vs. degraded results
4. Check for popular-but-wrong amplification: do any queries with high-corroboration results return outdated or incorrect facts?

The boost is only activated for production if the treatment (corroboration) shows statistically significant improvement (p < 0.05) without regression on any metric.

### R3.6 -- Monitoring dashboard

**The system shall** provide an admin-visible view of corroboration statistics per KB:

- Distribution of distinct source counts per entity edge
- Top 10 entities by corroboration count (sanity check)
- Entities with suspiciously high corroboration (> 2 standard deviations above mean)
- Before/after retrieval quality comparison (from A/B evaluation)

### Gate criteria (Phase 2 -> Phase 3)

- Phase 2 complete (`source_cluster_id` assigned, distinct source count computation verified)
- SPEC-EVIDENCE-001 evaluation framework operational (required for R3.5)

### Acceptance criteria (Phase 3)

- [ ] Corroboration boost formula implemented in `evidence_tier.py`
- [ ] Minimum KB size threshold enforced (20 unique source docs + 30% entity coverage)
- [ ] Feature flag `EVIDENCE_CORROBORATION_ENABLED` works correctly
- [ ] Shadow scoring implemented: boosted scores logged but not served
- [ ] A/B evaluation completed on Voys dataset using SPEC-EVIDENCE-001 framework
- [ ] Statistical significance achieved (p < 0.05) for improvement, OR decision to not enable documented
- [ ] Monitoring dashboard shows corroboration statistics
- [ ] No popular-but-wrong amplification detected in evaluation
- [ ] If evaluation passes: feature flag flipped to `true` for qualifying KBs

### Risk assessment (Phase 3)

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Corroboration boost degrades retrieval for some query types | Medium | Medium | Shadow scoring + A/B evaluation before activation; per-query impact analysis |
| Popular-but-wrong amplification | Low | High | Cap at 1.25; monitor dashboard for high-corroboration outliers; manual review of top-boosted results |
| Evaluation shows no significant improvement | Medium | Low | Feature stays off; Phase 0-2 infrastructure is still valuable for observability; revisit when KBs grow larger |
| Interaction effects with other evidence-tier dimensions | Low | Medium | SPEC-EVIDENCE-001 evaluation framework supports per-dimension isolation testing |

### Estimated effort: 3-4 days (excluding A/B evaluation runtime)

---

## Architecture fit

```
knowledge_ingest pipeline (Phase 0 + 2):
  step 1: receive document
  step 2: chunk into episodes
  step 3: -- PHASE 0 -- tag with source_document_id
  step 4: -- PHASE 2 -- compute SemHash fingerprint, assign source_cluster_id
  step 5: send to Graphiti for entity/edge extraction

retrieval pipeline (Phase 3):
  step 1: vector search (Qdrant)
  step 2: graph search (FalkorDB/Graphiti)
  step 3: merge + deduplicate
  step 4: reranker (BGE-reranker-v2-m3)
  step 5: evidence_tier.apply() (SPEC-EVIDENCE-001)
  step 6: -- PHASE 3 -- corroboration_boost.apply() (this SPEC)
  step 7: U-shape ordering
  step 8: return ChunkResult[]
```

### New files

| File | Phase | Contents |
|---|---|---|
| `knowledge_ingest/dedup/semhash_dedup.py` | Phase 2 | SemHash fingerprinting, clustering, `source_cluster_id` assignment |
| `knowledge_ingest/scripts/backfill_source_clusters.py` | Phase 2 | One-time backfill for existing episodes |
| `knowledge_ingest/scripts/er_audit.py` | Phase 1 | Entity resolution audit: export entities, flag candidate missed/false merges |
| `retrieval_api/services/corroboration.py` | Phase 3 | `compute_boost(edge, episodes) -> float`, KB threshold check |

### Modified files

| File | Phase | Change |
|---|---|---|
| `knowledge_ingest/routes/ingest.py` | Phase 0 | Pass `source_document_id` to episode metadata |
| `knowledge_ingest/graphiti_client.py` (or equivalent) | Phase 0 | Include `source_document_id` in episode creation |
| `retrieval_api/services/evidence_tier.py` | Phase 3 | Integrate `corroboration_boost` into score formula |
| `retrieval_api/services/search.py` | Phase 0 | Include `source_document_id` in graph search result passthrough |

---

## Dependency: SPEC-EVIDENCE-001

Phase 3 (R3.5: A/B evaluation) depends on the RAGAS evaluation framework from SPEC-EVIDENCE-001. Specifically:

- The testset of 150 queries (50 curated + 100 synthetic)
- The RAGAS metrics pipeline (Context Precision, Faithfulness, Answer Relevancy)
- NDCG@10 and Recall@10 computation
- Wilcoxon signed-rank test for paired comparison
- Per-dimension isolation support (to test corroboration boost in isolation)

If SPEC-EVIDENCE-001 is not complete when Phase 3 is ready, shadow scoring (R3.4) can proceed independently. The A/B evaluation (R3.5) is blocked until SPEC-EVIDENCE-001 delivers.

---

## What is explicitly NOT in scope

- Probabilistic fusion modeling (Google Knowledge Vault style) -- far beyond current scale
- User-facing confidence labels based on corroboration -- research says don't (CHI 2024)
- Org-specific corroboration thresholds (future, if needed)
- Entity resolution improvements (these are Graphiti upstream; we validate, not fix)
- Assertion mode interaction with corroboration (future SPEC-EVIDENCE-002 concern)

---

## Summary timeline

| Phase | Gate in | Deliverable | Risk | Effort |
|---|---|---|---|---|
| **Phase 0** | Now | `source_document_id` on episodes, episode count logging | Zero | 1-2 days |
| **Phase 1** | Voys KB loaded (190 pages) | ER validation audit, precision/recall metrics | Low (validation only) | 3-5 days |
| **Phase 2** | ER precision > 90%, recall > 85% | SemHash integration, `source_cluster_id`, backfill | Low (infrastructure only) | 2-3 days |
| **Phase 3** | Phase 2 + SPEC-EVIDENCE-001 | Corroboration boost, shadow scoring, A/B evaluation | Medium (retrieval change) | 3-4 days |

Total if all phases pass: 9-14 days, spread across natural milestones (not consecutive).

---

## Sources

- Corroboration scoring research: `docs/research/corroboration/corroboration-scoring.md`
- [Graphiti GitHub Repository](https://github.com/getzep/graphiti)
- [Zep: A Temporal Knowledge Graph Architecture (arXiv:2501.13956)](https://arxiv.org/abs/2501.13956)
- [Google Knowledge Vault (Dong et al., KDD 2014)](https://dl.acm.org/doi/10.1145/2623330.2623623)
- [SemHash: Semantic Deduplication Library](https://github.com/MinishLab/semhash)
- [GraphRAG Entity Resolution Failures (Sowmith Mandadi)](https://www.sowmith.dev/blog/graphrag-entity-disambiguation)
