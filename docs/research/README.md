# Evidence-Weighted Knowledge Retrieval: Research Synthesis

> Last updated: 2026-03-30
> Status: Active research programme — informs Klai knowledge architecture decisions
> Authors: Mark Duijndam + AI research assistants

---

## What this research programme covers

Klai's RAG pipeline currently treats all retrieved chunks equally. A manually curated KB article and an automatically crawled web page receive identical retrieval scores. This research programme investigates whether — and how — weighting chunks by evidence quality improves retrieval.

Four scoring dimensions are investigated:

| Dimension | Question | Key finding |
|---|---|---|
| **Content type** | Should a KB article rank higher than a web crawl? | Yes — RA-RAG: +51% accuracy, TREC Health: +60% MAP |
| **Assertion mode** | Should facts rank higher than hypotheses? | Promising but unproven for RAG specifically |
| **Temporal decay** | Should recent content rank higher? | Yes conceptually, but calibration is unsolved |
| **Cross-source corroboration** | Should facts confirmed by multiple sources rank higher? | Yes — but only with near-duplicate detection and source grouping |

---

## Core conclusions

### 1. Evidence-weighted retrieval works — the principle is proven

Converging evidence from information retrieval, knowledge graphs, and fact verification confirms that weighting by source quality improves results:

- **TREC Health Misinformation** (Huang et al., 2025): +60% MAP, +30% NDCG via credibility-weighted fusion
- **RA-RAG** (Hwang et al., 2024): +51% accuracy in adversarial settings via source reliability estimation
- **BayesRAG** (Li et al., 2026): +20% Recall@20 via Bayesian corroboration modelling
- **Knowledge Vault** (Google, KDD 2014): 271M high-confidence triples via multi-extractor corroboration

### 2. The specific weights are uncertain — start flat, measure, then tune

No study tests epistemic assertion type labels (fact/hypothesis/opinion) on chunks and measures retrieval impact. The concept is novel. The equal-weighting literature (Einhorn & Hogarth 1975, Graefe 2013) strongly suggests flat weights are safest when empirical data doesn't exist.

**Maximum safe weight spread** for an ~85% accurate classifier: **0.20** (i.e., minimum weight 0.80 if maximum is 1.00). Wider spreads cause more harm from misclassification than benefit from correct classification.

### 3. Corroboration scoring needs prerequisites before deployment

Raw `EntityEdge.episodes` counts in Graphiti conflate independent sources, near-duplicates, and chunking artifacts. Three prerequisites must be met before corroboration can be used as a retrieval signal:

1. **Near-duplicate detection** (SemHash or similar at ingest time)
2. **Source-level grouping** (`source_document_id` above the episode level)
3. **Entity resolution validation** (>90% precision, >85% recall on Klai content)

### 4. Don't show confidence labels to users

CHI 2024: confidence indicators don't improve task accuracy. ACL 2024: overconfident epistemic markers cause lasting trust damage. ACL 2025: LLMs can't produce calibrated epistemic markers. Use assertion mode as an *internal* ranking signal, not a user-facing label.

### 5. Measure before deploying — RAGAS + shadow scoring

The recommended evaluation protocol: 150 test queries (50 curated + 100 synthetic via RAGAS), Wilcoxon signed-rank paired tests, shadow scoring in production before cutover. Estimated setup: 3-4 developer-days.

---

## Research documents

### Foundations

Broad literature review establishing the scientific basis.

| Document | Scope | Language |
|---|---|---|
| [Evidence-Weighted Knowledge Research](foundations/evidence-weighted-knowledge.md) | Core research across 4 topics: cross-source corroboration, source credibility, probabilistic KGs, confidence in RAG. 50+ papers synthesised. | NL |
| [ThetaOS & Klai Comparison](foundations/thetaos-klai-comparison.md) | Maps research findings to ThetaOS (Mark's personal knowledge system) and Klai. Identifies validated strengths, gaps, and what each system can learn from the other. | NL |

**Key validated principles from the foundations research:**

| Principle | Status | Strongest evidence |
|---|---|---|
| Cross-source corroboration improves verification | Proven | FEVER, BayesRAG (+20% Recall@20) |
| Source authority improves retrieval | Proven | TREC Health (+60% MAP), PageRank |
| KG confidence scores correlate with accuracy | Proven | Knowledge Vault, NELL (91.3%) |
| More sources = always better | Refuted | Lost in the Middle: >30% degradation |
| Confidence scores are directly usable in RAG | Doubtful | ACL 2025: no method satisfies all 5 axioms |
| Showing confidence to users improves decisions | Refuted | CHI 2024, MDPI 2024 |

---

### Assertion Modes

Deep dive into whether epistemic type labels (fact/hypothesis/opinion) should influence retrieval.

| Document | Scope | Language |
|---|---|---|
| [Assertion Modes Research](assertion-modes/assertion-modes-research.md) | Industry landscape, classification feasibility (3 vs 5 categories), error asymmetry, user-facing confidence display, Klai implementation status. | EN |
| [Assertion Mode Weights](assertion-modes/assertion-mode-weights.md) | Literature review on defensible starting weights. ROC method, equal-weighting evidence, maximum safe spread derivation, multiplicative compounding risk. | EN |

**Key decisions from assertion modes research:**

| Decision | Recommendation | Confidence |
|---|---|---|
| Number of categories | 3 (not 5) — human agreement at 5 is ~67%, at 3 is ~89% | High |
| Starting weights | Flat (all 1.00) or conservative (0.10 spread max) | High |
| The implementatie.md spread (0.30) | Too aggressive for current classifier reliability | Medium-High |
| When to widen spread | Only after 200+ chunk classification evaluation + A/B retrieval test | High |

---

### Corroboration

Deep dive into using Graphiti's episode counts as a cross-source corroboration signal.

| Document | Scope | Language |
|---|---|---|
| [Corroboration Scoring](corroboration/corroboration-scoring.md) | Graphiti entity resolution assessment, episodes data model, near-duplicate detection approaches, production precedents (Knowledge Vault, Wikidata), risk assessment, prerequisites for safe deployment. | EN |

**Verdict:** Defer implementation. Build prerequisites first (near-duplicate detection, source grouping, entity resolution validation). Phase 0 (now): collect data, don't act on it.

---

### Evaluation

How to measure whether evidence-weighted scoring actually improves retrieval quality.

| Document | Scope | Language |
|---|---|---|
| [RAG Evaluation Framework](evaluation/rag-evaluation-framework.md) | Assessment of 7 evaluation frameworks (RAGAS, DeepEval, ARES, TruLens, Phoenix, Evidently, eRAG), metrics selection, minimum viable test set design, A/B testing approaches, LLM-as-judge considerations, statistical testing protocol. | EN |

**Recommended approach:**

| Component | Choice |
|---|---|
| Primary framework | RAGAS (synthetic data generation + evaluation) |
| Primary metrics | Context Precision + NDCG@10 + Faithfulness |
| Test set | 150 queries (50 curated + 100 synthetic) |
| Statistical test | Wilcoxon signed-rank (paired, non-parametric) |
| LLM judge | klai-large (Mistral Large, EU-hosted) |
| Production validation | Shadow scoring first, then cutover |

---

### Implementation

Concrete code changes to implement evidence-weighted scoring in Klai.

| Document | Scope | Language |
|---|---|---|
| [Implementation Plan](implementation/implementation-plan.md) | Six identified gaps with exact file/line changes, proposed evidence tier mapping, assertion mode weight tables, corroboration boost formula, U-shape chunk ordering, evidence profile architecture for multi-tenant configurability. | NL |

**The six gaps:**

| # | Gap | Status |
|---|---|---|
| 1 | `content_type` stored but not used for scoring | Ready to implement |
| 2 | `assertion_mode` dropped at Qdrant upsert | Ready to implement |
| 3 | Corroboration count not computed | Deferred (prerequisites needed) |
| 4 | Chunk ordering not optimised (Lost in the Middle) | Ready to implement |
| 5 | No temporal decay in scoring | Ready to implement |
| 6 | `ingested_at` and `assertion_mode` not returned in retrieval | Ready to implement |

**Deliberately not implemented:**
- Conflict detection (ACL 2025: no reliable method exists)
- Confidence calibration (ICLR 2020: systematically miscalibrated without calibration step)
- Near-duplicate detection for Qdrant chunks (separate project)

---

## Reading order

For someone new to this research:

1. Start with **this document** (you're here) for the overview
2. Read [Evidence-Weighted Knowledge Research](foundations/evidence-weighted-knowledge.md) for the scientific foundation
3. Read [ThetaOS & Klai Comparison](foundations/thetaos-klai-comparison.md) to understand how it applies to Klai
4. Then go into the specific dimensions you're interested in:
   - Assertion modes: [research](assertion-modes/assertion-modes-research.md) then [weights](assertion-modes/assertion-mode-weights.md)
   - Corroboration: [scoring research](corroboration/corroboration-scoring.md)
   - Evaluation: [framework](evaluation/rag-evaluation-framework.md)
5. Finally, [Implementation Plan](implementation/implementation-plan.md) for the concrete code changes

---

## Open questions

These questions span multiple research documents and remain unresolved:

1. **Does assertion mode as a retrieval signal improve quality for Klai's content mix?** No empirical data exists. Requires A/B testing with the evaluation framework.

2. **What is the actual classifier accuracy on Klai's content?** The 85% figure comes from Prieto et al. (2020) on biomedical text. Klai's content is different.

3. **Is corroboration useful at Klai's current KB size (10-100 docs)?** Most edges will have episode count = 1. The signal may be too sparse.

4. **Can the HyPE prompt reliably classify assertion mode as a marginal addition?** MDKeyChunker (2026) shows multi-field extraction is feasible. Untested on Klai's pipeline.

5. **Should the MCP taxonomy be aligned with the DB taxonomy?** Yes — current mapping gap (`fact/claim/note` vs `factual/procedural/quoted/belief/hypothesis`) must be resolved regardless.

---

## Complete source bibliography

### Metadata enrichment in RAG
- Mishra et al. (2025). Meta-RAG. [emergentmind.com](https://www.emergentmind.com/topics/meta-rag-framework)
- Poliakov & Shvai (2024). Multi-Meta-RAG. ICTERI. [arXiv:2406.13213](https://arxiv.org/abs/2406.13213)
- Anthropic (2024). Contextual Retrieval. [anthropic.com](https://www.anthropic.com/news/contextual-retrieval)
- Metadata-Driven RAG for Financial QA (2025). [arXiv:2510.24402](https://arxiv.org/html/2510.24402v1)
- Maniyar et al. (2026). Legal RAG with Metadata-Enriched Pipelines. [arXiv:2603.19251](https://arxiv.org/abs/2603.19251)
- Xu et al. (2025). MEGA-RAG. [Frontiers](https://www.frontiersin.org/journals/public-health/articles/10.3389/fpubh.2025.1635381/full)
- Asai et al. (2024). SELF-RAG. ICLR Oral. [arXiv:2310.11511](https://arxiv.org/abs/2310.11511)

### Epistemic classification and certainty
- Prieto et al. (2020). Certainty of scholarly assertions. PeerJ. [peerj.com/articles/8871](https://peerj.com/articles/8871/)
- Rubin (2007). Stating with Certainty or Stating with Doubt. NAACL. [ACL Anthology](https://aclanthology.org/N07-2036/)
- Aicher et al. (2025). Facts are Harder Than Opinions. [arXiv:2506.03655](https://arxiv.org/abs/2506.03655)
- Chandra et al. (2025). TRACE. [arXiv:2506.12072](https://arxiv.org/abs/2506.12072)

### Credibility-weighted retrieval
- Huang et al. (2025). Combating Health Misinformation. JASIST. [SAGE](https://journals.sagepub.com/doi/10.1177/14604582251388860)
- Hwang et al. (2024). RA-RAG. [arXiv:2410.22954](https://arxiv.org/abs/2410.22954)
- TREC Health Misinformation Track (2020-2022). [trec-health-misinfo.github.io](https://trec-health-misinfo.github.io/)

### Knowledge graphs and confidence
- Dong et al. (2014). Knowledge Vault. KDD. [Google Research](https://research.google/pubs/knowledge-vault-a-web-scale-approach-to-probabilistic-knowledge-fusion/)
- Mitchell et al. (2018). NELL. CACM. [paper](https://burrsettles.pub/mitchell.cacm18.pdf)
- Safavi & Koutra (2020). Probability Calibration for KGE. ICLR. [OpenReview](https://openreview.net/forum?id=S1g8K1BFwS)
- Kuhn et al. (2018). Nanopublications. [arXiv:1809.06532](https://arxiv.org/abs/1809.06532)
- TrustGraph. [trustgraph.ai](https://trustgraph.ai/guides/key-concepts/context-graphs/)

### Fact verification and corroboration
- Thorne et al. (2018). FEVER. [arXiv:1803.05355](https://arxiv.org/abs/1803.05355)
- Li et al. (2026). BayesRAG. [arXiv:2601.07329](https://arxiv.org/abs/2601.07329)
- Liu et al. (2023). Lost in the Middle. [arXiv:2307.03172](https://arxiv.org/abs/2307.03172)

### Confidence and uncertainty in RAG
- Soudani et al. (2025). Why UE Methods Fall Short in RAG. ACL. [arXiv:2505.07459](https://arxiv.org/abs/2505.07459)
- Yan et al. (2024). CRAG. [arXiv:2401.15884](https://arxiv.org/abs/2401.15884)
- Bayesian RAG (2025). [Frontiers in AI](https://www.frontiersin.org/journals/artificial-intelligence/articles/10.3389/frai.2025.1668172/full)

### User-facing confidence and trust
- CHI 2024. Impact of Model Interpretability. [ACM DL](https://dl.acm.org/doi/10.1145/3613904.3642780)
- Zhou et al. (2024). Relying on the Unreliable. ACL. [ACL Anthology](https://aclanthology.org/2024.acl-long.198.pdf)
- Liu et al. (2025). Revisiting Epistemic Markers. ACL. [ACL Anthology](https://aclanthology.org/2025.acl-short.18/)

### Weight selection methodology
- Einhorn & Hogarth (1975). Unit weighting schemes. [PhilPapers](https://philpapers.org/rec/EINUWS)
- Graefe (2013). Equally weighted predictors. J. Business Research. [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0148296315001563)
- Barron (1992). Rank Order Centroid. Via Danielson & Ekenberg (2016). [Springer](https://link.springer.com/article/10.1007/s10726-016-9494-6)
- Liu et al. (2025). Noisy Labels Error Bounds. [arXiv:2501.15163](https://arxiv.org/abs/2501.15163)

### RAG evaluation
- RAGAS (2023). [arXiv:2309.15217](https://arxiv.org/abs/2309.15217)
- DeepEval. [GitHub](https://github.com/confident-ai/deepeval)
- ARES (2023). [arXiv:2311.09476](https://arxiv.org/abs/2311.09476)
- Sakai (2016). Statistical Significance in IR. SIGIR. [ACM DL](https://dl.acm.org/doi/10.1145/2911451.2911492)

### Entity resolution and deduplication
- Graphiti / Zep. [GitHub](https://github.com/getzep/graphiti), [arXiv:2501.13956](https://arxiv.org/abs/2501.13956)
- SemHash. [GitHub](https://github.com/MinishLab/semhash)
- MDKeyChunker (2026). [arXiv:2603.23533](https://arxiv.org/abs/2603.23533)
- Wikidata Quality. [wikidata.org](https://www.wikidata.org/wiki/Wikidata:Item_quality)
