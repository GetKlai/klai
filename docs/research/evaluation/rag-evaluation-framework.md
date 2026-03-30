# RAG Evaluation Framework for Klai: Research

> Created: 2026-03-30
> Status: Complete
> Context: Pre-implementation research for evidence-tier scoring evaluation
> Scope: Practical, low-overhead evaluation framework for measuring RAG retrieval quality improvements
> Part of: [Research Synthesis](../README.md)

---

## Executive Summary

Klai is about to implement evidence-tier scoring (weighting chunks by content_type, assertion_mode, temporal age, and corroboration). Before deploying these changes, we need a way to measure whether they actually improve retrieval quality. This document evaluates the current (2025-2026) landscape of RAG evaluation tools, metrics, statistical methods, and production testing approaches, and recommends a concrete evaluation protocol for Klai.

**Recommended approach:** RAGAS-based offline evaluation with LLM-as-judge, using a hybrid test set (50 curated + 100 synthetic queries), paired Wilcoxon signed-rank tests for statistical significance, and optional shadow scoring in production. Estimated effort: medium (2-3 developer-days for initial setup, then automated).

---

## 1. Evaluation Frameworks Assessed

### 1.1 RAGAS (Retrieval Augmented Generation Assessment)

**What it is:** Open-source framework for reference-free evaluation of RAG pipelines. Originally published at NeurIPS 2023 (Shahul Es et al.), with v2 updated April 2025. The most widely adopted RAG evaluation framework.

**Core metrics:**
- **Faithfulness** -- is the generated answer grounded in retrieved context?
- **Answer Relevancy** -- is the answer relevant to the question?
- **Context Precision** -- are retrieved chunks relevant (and irrelevant ones excluded)?
- **Context Recall** -- did the retriever capture all necessary information?

**Key strengths for Klai:**
- Reference-free: does not require ground-truth answers for core metrics (reduces annotation burden)
- Built-in synthetic test data generation via `TestsetGenerator` (knowledge graph-based, with SingleHop/MultiHop query distribution)
- Lightweight: `uvx` one-liner to run, pip-installable, works with any LLM provider via LiteLLM
- Active maintenance, large community, extensive documentation
- Custom metrics via decorators (`DiscreteMetric` support)

**Limitations:**
- NaN scores from invalid JSON generation (less stable than DeepEval for custom LLMs)
- No built-in observability or production monitoring (evaluation-only)
- Context Recall requires ground-truth reference answers

**Fit for Klai:** HIGH -- the synthetic data generation from existing KB documents is directly applicable. Works with LiteLLM (which Klai already uses).

**Sources:**
- [RAGAS Documentation](https://docs.ragas.io/en/latest/)
- [RAGAS Paper (arXiv:2309.15217)](https://arxiv.org/abs/2309.15217)
- [RAGAS Testset Generation](https://docs.ragas.io/en/stable/getstarted/rag_testset_generation/)

---

### 1.2 DeepEval

**What it is:** Open-source pytest-compatible LLM evaluation framework by Confident AI. Brings test-driven development (TDD) to RAG evaluation. 50+ built-in metrics.

**Core RAG metrics:**
- Faithfulness, Contextual Recall, Contextual Precision, Contextual Relevancy
- RAGAS composite score (average of the above)
- Multi-turn RAG metrics (sliding window approach)

**Key strengths for Klai:**
- Pytest integration: write `test_retrieval.py` with `deepeval` assertions, run in CI/CD
- Debuggable: LLM judge reasoning is inspectable (why did it score this way?)
- JSON-confineable: fewer NaN scores than RAGAS with custom LLMs
- Parallel test execution

**Limitations:**
- Heavier dependency tree than RAGAS
- Confident AI cloud platform is the natural upsell (open-source core is sufficient)
- Less mature synthetic data generation than RAGAS

**Fit for Klai:** MEDIUM-HIGH -- excellent for CI/CD integration once the evaluation set exists. Could complement RAGAS (use RAGAS for test set generation, DeepEval for CI assertions).

**Sources:**
- [DeepEval RAG Evaluation Guide](https://deepeval.com/guides/guides-rag-evaluation)
- [DeepEval GitHub](https://github.com/confident-ai/deepeval)
- [DeepEval RAG Quickstart](https://deepeval.com/docs/getting-started-rag)

---

### 1.3 ARES (Automated RAG Evaluation System)

**What it is:** Stanford research framework (Saad-Falcon, Khattab, Potts, Zaharia). Three-stage pipeline: synthetic data generation, fine-tuned judge models, prediction-powered inference (PPI) with statistical confidence intervals.

**Key strengths:**
- Minimal human annotations needed (~150 preference labels + 5 few-shot examples)
- Fine-tuned lightweight judges (not relying on expensive LLM calls per evaluation)
- Statistical confidence intervals via PPI -- uniquely rigorous
- Outperforms RAGAS by 59.3 percentage points on context relevance (per paper)

**Limitations:**
- Requires fine-tuning judge models (setup overhead)
- Needs ~150 human-annotated preference examples for PPI calibration
- Less active community than RAGAS/DeepEval
- More academic than production-ready

**Fit for Klai:** LOW-MEDIUM -- the PPI approach is rigorous but the setup cost (fine-tuning judges, collecting 150 annotations) is high for Klai's current stage. Worth revisiting when evaluation maturity increases.

**Sources:**
- [ARES Paper (arXiv:2311.09476)](https://arxiv.org/abs/2311.09476)
- [ARES GitHub](https://github.com/stanford-futuredata/ARES)
- [ARES Documentation](https://ares-ai.vercel.app/)

---

### 1.4 TruLens

**What it is:** Open-source evaluation and tracing library by TruEra (Snowflake). Innovated the "RAG Triad" (Context Relevance, Groundedness, Answer Relevance). Now at v2.6 with OpenTelemetry support.

**Key strengths:**
- RAG Triad is conceptually clean and well-benchmarked (LLM-AggreFact, TREC-DL, HotPotQA)
- OpenTelemetry tracing -- connects evaluation to production observability
- Ground truth persistence in SQL datastores
- Agent's GPA framework for agentic evaluation (future-proofing)
- Reasoning model support (DeepSeek, GPT-5, o-series as judges)

**Limitations:**
- Heavier infrastructure (SQL persistence, OTel setup)
- Snowflake corporate backing may influence roadmap direction
- Migration from trulens-eval to TruLens v1+ caused community confusion

**Fit for Klai:** MEDIUM -- good for production monitoring later, overkill for initial A/B evaluation of scoring changes.

**Sources:**
- [TruLens RAG Triad](https://www.trulens.org/getting_started/core_concepts/rag_triad/)
- [TruLens GitHub](https://github.com/truera/trulens)
- [TruLens Blog 2025](https://www.trulens.org/blog/archive/2025/)

---

### 1.5 Arize Phoenix

**What it is:** Open-source AI observability platform. OpenTelemetry-based tracing + evaluation + datasets + experiments. Licensed under Elastic License 2.0.

**Key strengths:**
- Strong observability: traces, spans, evaluation side-by-side
- Integrates with RAGAS, DeepEval, Cleanlab evaluators
- Dataset versioning and experiment tracking
- Works with LiteLLM (Klai's provider)
- Visual UI for debugging retrieval quality

**Limitations:**
- Evaluation is secondary to observability (less opinionated evaluation workflow)
- Requires running a Phoenix server (Docker container)
- Less mature production-to-eval loop

**Fit for Klai:** MEDIUM -- useful as an observability layer if Klai needs visual debugging. Not the primary evaluation tool.

**Sources:**
- [Arize Phoenix Documentation](https://arize.com/docs/phoenix)
- [Phoenix GitHub](https://github.com/Arize-ai/phoenix)
- [Phoenix RAG Evaluation with Qdrant](https://superlinked.com/vectorhub/articles/retrieval-augmented-generation-eval-qdrant-arize)

---

### 1.6 Evidently AI

**What it is:** Open-source ML/LLM observability framework. 100+ built-in metrics for data drift, feature quality (ML), and output quality/hallucination (LLM). Declarative testing API.

**Key strengths:**
- Unified ML + LLM evaluation in one framework
- Declarative test suites runnable in CI/CD
- Production monitoring dashboards
- LLM-as-judge with custom prompts
- 25M+ downloads, mature library

**Limitations:**
- Generalist (not RAG-specific)
- RAG metrics less sophisticated than RAGAS/DeepEval
- Cloud platform for advanced features (synthetic data generation)

**Fit for Klai:** LOW-MEDIUM -- better suited for production monitoring of already-validated changes. Not ideal for the initial evaluation comparison.

**Sources:**
- [Evidently RAG Evaluation Guide](https://www.evidentlyai.com/llm-guide/rag-evaluation)
- [Evidently GitHub](https://github.com/evidentlyai/evidently)
- [Evidently RAG Testing](https://www.evidentlyai.com/rag-testing)

---

### 1.7 eRAG

**What it is:** Research method (Salemi & Zamani, SIGIR 2024) for evaluating retrieval quality in RAG. Each retrieved document is individually fed to the LLM, and the output is evaluated against ground truth. The per-document score becomes the relevance label.

**Key insight:** eRAG achieves higher correlation with downstream RAG performance than traditional IR metrics (Kendall's tau improvement 0.168-0.494) while using 50x less GPU memory than end-to-end evaluation.

**Fit for Klai:** CONCEPTUALLY USEFUL -- the idea of evaluating per-chunk utility (not just relevance) aligns with evidence-tier scoring. The per-chunk evaluation approach could validate whether higher-tier chunks actually produce better answers.

**Sources:**
- [eRAG Paper (arXiv:2404.13781)](https://arxiv.org/abs/2404.13781)
- [eRAG GitHub](https://github.com/alirezasalemi7/eRAG)
- [eRAG at ACM SIGIR 2024](https://dl.acm.org/doi/10.1145/3626772.3657957)

---

## 2. Metrics That Matter for Klai

### 2.1 Retrieval Metrics

Klai's evidence-tier scoring changes the retrieval ranking. The primary question is: does reranking by evidence tier improve the quality of retrieved chunks?

| Metric | What it measures | When to use | Requires ground truth? |
|---|---|---|---|
| **NDCG@k** | Graded relevance with position weighting | Best for Klai: supports varying chunk quality levels (not just binary relevant/irrelevant) | Yes (graded relevance labels) |
| **Recall@k** | Fraction of relevant chunks retrieved | Whether evidence-tier filtering excludes important chunks | Yes |
| **Precision@k** | Fraction of top-k chunks that are relevant | Whether top chunks are cleaner after scoring | Yes |
| **MRR** | How quickly the first relevant chunk appears | Less useful for Klai (we use multiple chunks, not just the first) | Yes |
| **Context Precision** (RAGAS) | Are relevant chunks ranked higher? | LLM-judged, no manual labels needed | No (LLM judge) |
| **Context Recall** (RAGAS) | Is all necessary info retrieved? | Needs reference answer | Yes (reference answer) |

**Recommended primary metrics for Klai:**
1. **NDCG@10** -- supports graded relevance, directly measures whether evidence-tier scoring improves ranking quality. The "graded" aspect is critical: a factual KB article chunk should score higher than a transcript fragment that mentions the same topic in passing.
2. **Context Precision** (RAGAS, LLM-judged) -- no manual labeling needed, directly measures retrieval quality from the LLM's perspective.
3. **Recall@10** -- ensures evidence-tier scoring does not exclude relevant content.

**Important caveat:** Traditional IR metrics (NDCG, MRR) assume sequential human examination with diminishing attention. In RAG, the LLM processes all chunks at once. NDCG still correlates with downstream quality (because putting better chunks first means less noise for the LLM), but the correlation is imperfect. A 2025 survey found retrieval accuracy alone explains only 60% of variance in end-to-end RAG quality.

### 2.2 Generation Metrics

These measure whether improved retrieval actually produces better answers.

| Metric | What it measures | Requires ground truth? |
|---|---|---|
| **Faithfulness** (RAGAS) | Is the answer grounded in retrieved context? | No |
| **Answer Relevancy** (RAGAS) | Does the answer address the question? | No |
| **Answer Correctness** | Is the answer factually correct? | Yes (reference answer) |

**Recommended:** Faithfulness + Answer Relevancy (both reference-free). Answer Correctness only for the curated test set where we have reference answers.

### 2.3 Metric Priority for Evidence-Tier Scoring

For the specific question "does evidence-tier scoring improve retrieval?", the metrics in priority order:

1. **Context Precision** (RAGAS) -- the purest signal: are higher-tier chunks ranked higher?
2. **NDCG@10** -- does the overall ranking improve? (requires ground-truth relevance labels for the curated set)
3. **Faithfulness** -- does better retrieval lead to more grounded answers?
4. **Answer Relevancy** -- does better retrieval lead to more on-topic answers?
5. **Recall@10** -- safety check: are we losing relevant chunks?

---

## 3. Minimum Viable Evaluation Set

### 3.1 Sample Size for Statistical Significance

For comparing flat scoring (baseline) vs. evidence-tier scoring (treatment) on the same test queries, we use a **paired design** (both systems answer every query). This is statistically efficient.

Power analysis for paired t-test (alpha=0.05, power=0.80):

| Expected Effect Size (Cohen's d) | Required Number of Test Queries |
|---|---|
| 0.2 (small) | ~199 |
| 0.3 (small-medium) | ~90 |
| 0.5 (medium) | ~34 |
| 0.8 (large) | ~15 |

**What effect size should we expect?**

Based on the evidence-weighted-knowledge research already completed:
- BayesRAG corroboration: +20% Recall@20 (large effect)
- TREC Health credibility weighting: +60% MAP, +30% NDCG (very large effect)
- Multi-Meta-RAG metadata filtering: +7.89% to +25.6% accuracy (medium-large)
- Anthropic Contextual Retrieval: -67% failure rate (large)

These are all best-case results from research papers. In production with Klai's mixed content, a medium effect size (d=0.5) is a conservative but realistic target for content_type weighting. For individual dimensions (temporal decay alone, corroboration alone), expect small-medium effects (d=0.3).

**Recommended test set sizes:**

| Test set | Size | Purpose |
|---|---|---|
| **Curated test set** | 50 queries | Human-verified ground truth, covers all content types, high confidence in labels |
| **Synthetic test set** | 100 queries | RAGAS-generated from KB documents, covers SingleHop/MultiHop patterns |
| **Total** | 150 queries | Sufficient for d=0.3 detection (need ~90 for paired test), with margin for dropped/ambiguous queries |

With 150 paired observations, we can detect:
- d=0.3 effect at 95% power (well above 80% threshold)
- d=0.2 effect at ~70% power (borderline but informative)
- Individual dimension effects can be tested on the full 150 set

### 3.2 Test Set Composition

The test set must represent Klai's actual content mix and query patterns.

**By content type (proportional to Klai's KB content):**

| Content type | Curated queries | Synthetic queries | Total |
|---|---|---|---|
| KB articles (manually written) | 15 | 30 | 45 |
| Web-crawled pages | 10 | 25 | 35 |
| Meeting transcripts | 10 | 20 | 30 |
| PDF documents | 10 | 15 | 25 |
| Mixed (cross-type) | 5 | 10 | 15 |
| **Total** | **50** | **100** | **150** |

**By query complexity:**

| Type | Fraction | Description |
|---|---|---|
| Single-hop factual | 50% | "What is the onboarding procedure for new customers?" |
| Multi-hop reasoning | 25% | "How did the meeting decision from Jan 15 change the pricing policy?" |
| Comparative/abstract | 15% | "What are the differences between our Standard and Pro tier?" |
| Temporal (recency matters) | 10% | "What is the current status of the migration project?" |

### 3.3 Constructing the Curated Test Set

**Step 1: Mine production queries.** Extract 200+ real queries from Klai's query logs (or from demo/test usage if production logs are insufficient).

**Step 2: Select representative subset.** Pick 50 queries that cover the content type and complexity distributions above. Prioritize queries where the answer requires information from specific content types (to test evidence-tier impact).

**Step 3: Annotate ground truth.** For each query, a domain expert identifies:
- The ideal chunks (which chunks should be retrieved?)
- Graded relevance per chunk (3=perfect, 2=relevant, 1=marginally relevant, 0=irrelevant)
- A reference answer (for Answer Correctness)

**Estimated effort:** 2-3 hours for a domain expert to annotate 50 queries (assuming familiarity with the KB content).

### 3.4 Constructing the Synthetic Test Set

Use RAGAS `TestsetGenerator` with Klai's KB documents:

```python
from ragas.testset import TestsetGenerator
from ragas.testset.synthesizers import (
    SingleHopSpecificQuerySynthesizer,
    MultiHopAbstractQuerySynthesizer,
    MultiHopSpecificQuerySynthesizer,
)

# Use Klai's LiteLLM proxy
generator = TestsetGenerator(
    llm=litellm_model,          # klai-large for generation
    embedding_model=embedding,   # same embedder as production
    knowledge_graph=kg,          # built from KB documents
)

testset = generator.generate(
    testset_size=100,
    query_distribution=[
        (SingleHopSpecificQuerySynthesizer(), 0.5),
        (MultiHopAbstractQuerySynthesizer(), 0.25),
        (MultiHopSpecificQuerySynthesizer(), 0.25),
    ],
)
```

**Key consideration:** Use different LLM families for generation vs. evaluation to avoid self-enhancement bias (see Section 5).

---

## 4. A/B Testing in Production

### 4.1 Shadow Scoring (Recommended)

The lowest-risk production approach. Both scoring methods run on every query; only the current (flat) scoring serves the response.

```
Query → Retriever → [Flat scoring → serve response]
                  → [Evidence-tier scoring → log only]
```

**What to log per query:**
- Query text
- Top-10 chunks from flat scoring (with scores)
- Top-10 chunks from evidence-tier scoring (with scores)
- Overlap: how many chunks appear in both top-10?
- Rank displacement: for shared chunks, how much did position change?

**Analysis:** Run RAGAS Context Precision on both retrieval sets offline. Compare with paired tests.

**Effort:** Small -- instrument the retrieval path to run both scorers, log results. No user impact.

### 4.2 Interleaving (Advanced)

Present a mixed ranking from both scorers to the LLM. Track which scorer's chunks get cited in the answer. The scorer whose chunks get cited more often is producing more useful context.

**How it works:**
1. Retrieve top-10 from flat scoring and top-10 from evidence-tier scoring
2. Interleave into a single ranked list (team-draft or balanced interleaving)
3. Feed interleaved context to LLM
4. Track which chunks the LLM actually uses (via citation or attribution)
5. Attribute credit to the scorer that contributed the used chunks

**Advantage over A/B testing:** In A/B testing, you compare two different LLM outputs (confounded by generation variability). In interleaving, the LLM sees the same context and votes by usage.

**Effort:** Medium -- requires citation tracking or attribution analysis.

**Source:** [Max Irwin - Interleaving for RAG](https://maxirwin.com/articles/interleaving-rag/)

### 4.3 User Feedback Collection (Complementary)

Add thumbs-up/thumbs-down on RAG answers. Low signal-to-noise ratio individually, but aggregated over hundreds of queries, it provides a directional signal.

**Practical considerations:**
- Feedback rate is typically 5-15% of queries
- Negative feedback is more informative than positive (users rarely upvote correct answers)
- Cannot distinguish retrieval problems from generation problems
- Useful as a monitoring signal, not as the primary evaluation method

### 4.4 Recommended Production Approach

**Phase 1 (launch):** Shadow scoring only. Log both rankings, compare offline.
**Phase 2 (if Phase 1 shows improvement):** Switch to evidence-tier scoring as default.
**Phase 3 (ongoing):** Add thumbs-up/down feedback, monitor for regression.

Interleaving is elegant but complex. Reserve it for cases where shadow scoring results are ambiguous.

---

## 5. The LLM-as-Judge Question

### 5.1 Current State of Research (2025-2026)

LLM-as-judge is now the dominant approach for RAG evaluation where human annotation is impractical. The research is clear on both its power and its limitations.

**Reliability:**
- LLM judges achieve ~80% agreement with human preferences, matching human-to-human consistency (EMNLP 2025)
- Cost savings: 500x-5000x cheaper than human review
- Chain-of-thought reasoning in judge prompts significantly improves alignment with humans

**Known biases (quantified):**

| Bias | Impact | Mitigation |
|---|---|---|
| **Position bias** | 40% inconsistency in GPT-4 (SIGIR 2025) | Evaluate both (A,B) and (B,A) orderings |
| **Verbosity bias** | ~15% score inflation for longer answers | Explicit scoring rubrics that reward conciseness |
| **Self-enhancement bias** | 5-7% boost when judging own model family | Use different model family as judge vs. generator |
| **Scoring rubric sensitivity** | Varies by rubric presentation | Standardize rubrics, use chain-of-thought |
| **Multilingual inconsistency** | Significant cross-language variance (EMNLP 2025) | Evaluate in the language of the content |

**Sources:**
- [Survey on LLM-as-a-Judge (arXiv:2411.15594)](https://arxiv.org/abs/2411.15594)
- [Position Bias Study (arXiv:2406.07791)](https://arxiv.org/abs/2406.07791)
- [Justice or Prejudice? Quantifying Biases](https://arxiv.org/html/2410.02736v1)
- [LLM as a Judge: 2026 Guide](https://labelyourdata.com/articles/llm-as-a-judge)

### 5.2 Can Klai Use LLM-as-Judge?

**Yes, with mitigations.** For comparing two retrieval configurations on the same queries, LLM-as-judge is reliable enough -- the biases cancel out in paired comparisons (both systems are evaluated by the same biased judge, so the relative difference is preserved).

**Practical setup for Klai:**

1. **Judge model:** Use `klai-large` (Mistral Large, EU-hosted) as the judge. This avoids data leaving the EU and avoids self-enhancement bias (Klai's retrieval does not use Mistral for generation).

2. **Evaluation protocol:** For Context Precision, use RAGAS's built-in prompt which asks the judge to evaluate each retrieved chunk's relevance to the query. RAGAS handles the scoring mechanics.

3. **Bias mitigation for Klai:**
   - Position bias: RAGAS Context Precision evaluates chunks independently (not comparatively), reducing position bias
   - Self-enhancement: judge (Mistral Large) differs from generation model
   - Verbosity: not applicable (evaluating retrieved chunks, not generated text)

4. **Calibration:** On the 50 curated queries with human-annotated relevance labels, compute agreement between the LLM judge and human labels. If agreement > 75%, the judge is sufficiently reliable for the synthetic set.

### 5.3 When Human Evaluation Is Still Needed

- **Initial calibration:** The 50 curated queries should have human relevance judgments to validate the LLM judge
- **Edge cases:** Queries where the two systems strongly disagree (large rank displacement) should be human-reviewed
- **Content-type specific validation:** Verify the LLM judge handles transcripts and web crawls fairly (these are noisier than KB articles, and the judge might penalize noise that is actually informative)

---

## 6. Recommended Approach for Klai

### 6.1 Framework Selection

**Primary:** RAGAS (for synthetic data generation + evaluation metrics)
**Secondary:** DeepEval (for CI/CD test assertions once the evaluation set is stable)

**Rationale:**
- RAGAS has the best synthetic test data generation (knowledge graph-based, diverse query types)
- RAGAS works with any LLM via LiteLLM (Klai's existing infrastructure)
- DeepEval adds pytest-style assertions for regression testing
- Both are open-source, no vendor lock-in
- No need for ARES's fine-tuned judges or TruLens's OTel infrastructure at this stage

### 6.2 Evaluation Protocol (Step by Step)

**Phase 0: Setup (1 day)**

1. Install RAGAS: `pip install ragas`
2. Configure LiteLLM connection (Klai already has this)
3. Create evaluation script skeleton that:
   - Takes a query set as input
   - Runs retrieval with flat scoring
   - Runs retrieval with evidence-tier scoring
   - Computes RAGAS metrics for both
   - Outputs paired comparison results

**Phase 1: Build Test Set (1 day)**

1. Generate synthetic test set (100 queries) using RAGAS `TestsetGenerator` from Klai's KB documents
2. Curate 50 queries from production logs or representative use cases
3. For curated queries: annotate ground-truth relevant chunks and reference answers
4. Validate: spot-check 10 synthetic queries for quality, regenerate if needed

**Phase 2: Baseline Evaluation (0.5 day)**

1. Run the full test set (150 queries) through current flat scoring retrieval
2. Compute baseline metrics:
   - RAGAS Context Precision (all 150)
   - RAGAS Faithfulness and Answer Relevancy (all 150)
   - NDCG@10 (50 curated, using human relevance labels)
   - Recall@10 (50 curated)
3. Store results as the baseline checkpoint

**Phase 3: Evidence-Tier Evaluation (0.5 day)**

1. Implement evidence-tier scoring (the actual feature)
2. Run the same 150 queries through evidence-tier retrieval
3. Compute the same metrics
4. Run paired statistical tests:
   - Wilcoxon signed-rank test on per-query Context Precision scores (non-parametric, no normality assumption)
   - Paired t-test on per-query NDCG@10 scores (if distribution is approximately normal)
   - Report p-values and effect sizes (Cohen's d)

**Phase 4: Dimension Isolation (0.5 day)**

To measure the impact of individual scoring dimensions, run retrieval with each dimension enabled independently:

| Configuration | content_type weight | assertion_mode weight | temporal_decay | corroboration_boost |
|---|---|---|---|---|
| Baseline (flat) | off | off | off | off |
| content_type only | on | off | off | off |
| assertion_mode only | off | on | off | off |
| temporal_decay only | off | off | on | off |
| corroboration only | off | off | off | on |
| All combined | on | on | on | on |

This produces 6 configurations x 150 queries = 900 evaluation runs. With Bonferroni correction for 5 pairwise comparisons (each dimension vs. baseline), alpha = 0.01 per comparison.

**Phase 5: Production Validation (ongoing)**

1. Deploy shadow scoring (Section 4.1)
2. Log both retrieval rankings for every production query
3. Run weekly RAGAS evaluation on a sample of production queries
4. Monitor for regression after deploying evidence-tier scoring as default

### 6.3 Statistical Testing Protocol

**Primary test:** Wilcoxon signed-rank test (non-parametric paired test)
- Does not assume normal distribution of metric differences
- Robust to outliers (common in LLM-judged scores)
- Widely used in IR evaluation research

**Reporting:**
- p-value (reject H0 at alpha=0.05)
- Effect size: matched-pairs rank-biserial correlation (r) or Cohen's d on the differences
- Confidence interval on the median difference
- Number of concordant vs. discordant pairs (queries where evidence-tier was better vs. worse)

**Why Wilcoxon over paired t-test:** RAGAS scores are bounded [0,1] and often non-normally distributed (clustering at 0 and 1). The Wilcoxon test handles this correctly. The paired t-test can be reported as a secondary check.

**Reference:** Sakai (SIGIR 2016) established that for IR evaluation, the paired t-test, bootstrap test, and randomization test yield similar results, but the Wilcoxon test is safer when distributions are skewed.

---

## 7. Tools and Libraries

| Tool | Purpose | Install |
|---|---|---|
| `ragas` | Synthetic data generation + evaluation metrics | `pip install ragas` |
| `deepeval` | CI/CD test assertions (Phase 5+) | `pip install deepeval` |
| `scipy.stats.wilcoxon` | Paired statistical tests | `pip install scipy` (likely already installed) |
| `litellm` | LLM judge provider | Already in Klai stack |
| `pandas` | Results analysis and comparison | Already in Klai stack |

No additional infrastructure is required. Everything runs locally or through Klai's existing LiteLLM proxy.

---

## 8. Estimated Effort

| Phase | Effort | Output |
|---|---|---|
| Phase 0: Setup | 1 day | Evaluation script, RAGAS configured |
| Phase 1: Build Test Set | 1 day | 150 queries (50 curated + 100 synthetic) |
| Phase 2: Baseline | 0.5 day | Baseline metrics stored |
| Phase 3: Evidence-Tier Evaluation | 0.5 day | Comparative results with statistical tests |
| Phase 4: Dimension Isolation | 0.5 day | Per-dimension impact analysis |
| Phase 5: Shadow Scoring (production) | 0.5 day setup, then automated | Ongoing production validation |
| **Total initial** | **3-4 days** | Complete evaluation framework |
| **Ongoing** | ~2 hours/week | Weekly metric review from shadow scoring |

---

## 9. What This Does NOT Cover

- **End-user satisfaction measurement** -- requires deployed system with real users and feedback collection
- **Latency impact of evidence-tier scoring** -- needs benchmarking, not evaluation framework
- **Cost of LLM judge calls** -- estimate: 150 queries x ~5 metrics x ~$0.002/call = ~$1.50 per evaluation run (negligible with Klai's self-hosted Mistral)
- **Fine-tuning embedding models** -- separate from scoring changes, would need its own evaluation
- **Multi-language evaluation** -- current Klai content is primarily Dutch/English; LLM-as-judge may need validation per language

---

## 10. Key Decisions Summary

| Decision | Choice | Rationale |
|---|---|---|
| Primary framework | RAGAS | Best synthetic data generation, lightweight, LiteLLM compatible |
| Primary metrics | Context Precision + NDCG@10 + Faithfulness | Retrieval quality + end-to-end quality |
| Test set size | 150 (50 curated + 100 synthetic) | Sufficient for d=0.3 detection at >80% power |
| Statistical test | Wilcoxon signed-rank (paired) | Non-parametric, robust to non-normal score distributions |
| LLM judge | klai-large (Mistral Large) | EU-hosted, different family from generation model |
| Production approach | Shadow scoring first, then cutover | Zero risk to users during evaluation |
| Human annotation budget | 50 queries (~3 hours) | Calibration for LLM judge + ground-truth NDCG |

---

## Sources

### Frameworks
- [RAGAS Documentation](https://docs.ragas.io/en/latest/)
- [RAGAS Paper (arXiv:2309.15217)](https://arxiv.org/abs/2309.15217)
- [DeepEval RAG Evaluation](https://deepeval.com/guides/guides-rag-evaluation)
- [DeepEval GitHub](https://github.com/confident-ai/deepeval)
- [ARES Paper (arXiv:2311.09476)](https://arxiv.org/abs/2311.09476)
- [TruLens Documentation](https://www.trulens.org/)
- [Arize Phoenix](https://arize.com/docs/phoenix)
- [Evidently AI RAG Guide](https://www.evidentlyai.com/llm-guide/rag-evaluation)
- [eRAG Paper (arXiv:2404.13781)](https://arxiv.org/abs/2404.13781)

### Metrics and Methods
- [Weaviate - Retrieval Evaluation Metrics](https://weaviate.io/blog/retrieval-evaluation-metrics)
- [Pinecone - Evaluation Measures in IR](https://www.pinecone.io/learn/offline-evaluation/)
- [Evidently - NDCG Explained](https://www.evidentlyai.com/ranking-metrics/ndcg-metric)
- [RAG Evaluation Comprehensive Survey (arXiv:2504.14891)](https://arxiv.org/abs/2504.14891)

### LLM-as-Judge
- [Survey on LLM-as-a-Judge (arXiv:2411.15594)](https://arxiv.org/abs/2411.15594)
- [Position Bias Study (arXiv:2406.07791)](https://arxiv.org/abs/2406.07791)
- [Justice or Prejudice? Quantifying Biases (ICLR 2025)](https://arxiv.org/html/2410.02736v1)
- [LLM as a Judge 2026 Guide](https://labelyourdata.com/articles/llm-as-a-judge)

### Statistical Methods
- [Sakai - Statistical Significance, Power, and Sample Sizes (SIGIR 2016)](https://dl.acm.org/doi/10.1145/2911451.2911492)
- [Kelly - Power Analysis for IR (ECIR 2015)](https://link.springer.com/chapter/10.1007/978-3-319-16354-3_94)
- [G*Power Sample Size Calculations](https://pmc.ncbi.nlm.nih.gov/articles/PMC8441096/)

### Production Testing
- [Max Irwin - Interleaving for RAG](https://maxirwin.com/articles/interleaving-rag/)
- [Evidently - RAG Evaluation Best Practices](https://www.evidentlyai.com/llm-guide/rag-evaluation)
- [A/B Testing and Experimentation for RAG](https://apxml.com/courses/large-scale-distributed-rag/chapter-5-orchestration-operationalization-large-scale-rag/ab-testing-experimentation-rag)

### Synthetic Data Generation
- [RAGAS Testset Generation](https://docs.ragas.io/en/stable/getstarted/rag_testset_generation/)
- [Gretel - Building a Robust RAG Evaluation Pipeline](https://www.gretel.ai/blog/building-a-robust-rag-evaluation-pipeline)
- [NVIDIA - Evaluating RAG with Synthetic Data](https://developer.nvidia.com/blog/evaluating-and-enhancing-rag-pipeline-performance-using-synthetic-data/)

### Klai Internal Research
- [Evidence-Weighted Knowledge Research](../foundations/evidence-weighted-knowledge.md)
- [Assertion Modes Research](../assertion-modes/assertion-modes-research.md)
- [Assertion Mode Weights](../assertion-modes/assertion-mode-weights.md)
- [Corroboration Scoring](../corroboration/corroboration-scoring.md)
- [Implementation Plan](../implementation/implementation-plan.md)
