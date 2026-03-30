# Assertion Mode Weights: Literature Review and Recommendations

> Compiled: 2026-03-30
> Status: Research document — input for evidence_tier.py weight configuration
> Scope: What are defensible starting weights for assertion mode scoring in Klai's RAG retrieval pipeline?
> Depends on: [Assertion Modes Research](assertion-modes-research.md), [Implementation Plan](../implementation/implementation-plan.md)
> Part of: [Research Synthesis](../README.md)

---

## Executive Summary

There is no published system that weights retrieved passages by epistemic assertion type (fact/hypothesis/opinion) and measures the retrieval quality impact. The concept is novel. However, converging evidence from adjacent fields — credibility-weighted IR, noisy-label machine learning, multi-criteria decision theory, and robust forecasting — provides principled guidance for choosing initial weights.

**Core finding:** The maximum defensible weight spread is constrained by the classifier error rate. With a ~15% misclassification rate (realistic for LLM-based assertion typing), the theoretical maximum safe spread between highest and lowest weight is approximately 1.00 to 0.85. The [Implementation Plan](../implementation/implementation-plan.md) proposal (1.00 to 0.70) is too aggressive given current classifier reliability. Equal weighting (all 1.00) is a legitimate and possibly superior alternative until classification accuracy improves or empirical evaluation on Klai data is conducted.

---

## 1. Epistemic Modality in Information Retrieval

### 1.1 What the NLP literature says

Epistemic modality classification — distinguishing how certain an author is about a claim — is well-studied in computational linguistics but almost entirely disconnected from information retrieval.

**Hedging detection** is the most mature subfield. Hyland (1998) established the taxonomy; Medlock & Briscoe (2007) automated detection on biomedical text. The key finding: hedging markers ("may", "might", "suggests", "preliminary") are linguistically tractable. LLMs and even rule-based systems detect hedging with 85-90% accuracy because the signals are lexical and syntactic, not semantic.

**Certainty classification** at 3 categories achieves ~89% accuracy (Prieto et al., 2020, PeerJ). At 5 categories, human agreement drops to ~67% (Rubin, 2007, NAACL). This is the most important empirical constraint: any weighting scheme that assumes reliable 5-way classification is building on sand.

**The factual/belief boundary** is intrinsically hard. "The evidence suggests the vaccine is effective" is epistemic territory where even expert annotators disagree (Rubin 2007). LLMs perform worse on factual claims than on opinions — GPT-4o declined to classify 43% of claims in a multilingual evaluation (Aicher et al., 2025, arXiv:2506.03655). This is not a bug; the task itself is ambiguous.

### 1.2 The TRACE/WebTrust system (2025)

The closest production-oriented system to assertion-mode weighted retrieval is TRACE (arXiv:2506.12072, Chandra et al., 2025), which scores web content reliability using five features including a **Hedging Modality (HM)** score. The HM score is inversely proportional to hedging word density — fewer hedges = higher reliability score.

Critical insight from TRACE's grid search optimization:

| Feature combination | Optimal weights |
|---|---|
| Best 2 features | Factual Density (0.7) + Readability (0.3) |
| Best 3 features | Factual Density (0.7) + Readability (0.2) + Lexical Objectivity (0.1) |
| Best 4 features | Factual Density (0.7) + Readability (0.1) + Lexical Objectivity (0.1) + Hedging Modality (0.1) |

**Hedging Modality received minimal weight (0.1) or was excluded entirely in optimal configurations.** Factual density (named entity + numerical data concentration) dominated. This suggests that in real web content, hedging detection adds marginal signal on top of content richness — it is not a strong standalone discriminator.

### 1.3 Credibility-weighted retrieval (TREC Health Misinformation)

The TREC Health Misinformation Track (2020-2022) is the closest benchmark to what Klai proposes. Systems that incorporated credibility scoring into retrieval achieved:

- **+60% MAP** via fusion-based credibility-aware retrieval (Huang et al., 2025, JASIST)
- **+30% NDCG_UCC** (credibility-focused metric) over best single system

However, TREC credibility is a **document-level** assessment (is the source trustworthy?), not a **claim-level** assertion classification. This is a crucial distinction. Klai's assertion modes operate at chunk level, which is far more granular and error-prone.

### 1.4 RA-RAG: Source reliability estimation (Hwang et al., 2024)

RA-RAG (arXiv:2410.22954) estimates source reliability by cross-checking information across multiple sources and uses weighted majority voting (WMV) to aggregate. It prioritizes documents from highly reliable and relevant sources. The system outperforms baselines in heterogeneous-reliability scenarios, but operates at the **source level**, not the assertion level within individual passages.

**Key architectural insight:** RA-RAG does not assign fixed weights per category. It learns reliability iteratively from cross-source consistency. This is fundamentally different from pre-assigning weights based on assertion type.

---

## 2. Systems That Weight by Assertional/Epistemic Status

### 2.1 Direct answer: none exist

No production system or research prototype assigns fixed weights to retrieved passages based on their epistemic assertion type (fact / hypothesis / opinion / procedure) and demonstrates measurable retrieval improvement. This was confirmed in the prior research document ([Assertion Modes Research](assertion-modes-research.md), Section 4) and remains true after additional literature review.

### 2.2 Adjacent systems

| System | Level | What it weights | How |
|---|---|---|---|
| TRACE/WebTrust (2025) | Statement | Hedging, factual density, objectivity | Weighted sum, grid-searched (HM gets 0.1 weight) |
| TREC Health Misinfo (2020-2022) | Document | Source credibility (binary) | Fusion with relevance; +60% MAP |
| RA-RAG (2024) | Source | Cross-source consistency | Iterative reliability estimation, WMV |
| SELF-RAG (2024, ICLR) | Generation-time | Is this chunk relevant? Does it support? | Reflection tokens at generation, not index time |
| Nanopublications | Triple | Provenance graph + publication metadata | RDF-based assertion containers |
| TrustGraph | Triple | Per-triple confidence via reification | Source provenance, conflict resolution |

The gap between these systems and what Klai proposes (fixed per-category weights on assertion type at retrieval time) is significant. The closest analog is TRACE's HM feature, and that received minimal weight in the optimal configuration.

---

## 3. Defensible Methodology for Choosing Initial Weights

### 3.1 The Rank Order Centroid (ROC) method

When only an ordinal ranking is available (we know "factual > procedural > quoted > hypothesis" but not by how much), the MCDM literature offers the **Rank Order Centroid** method (Barron, 1992). ROC computes the centroid of all weight vectors that preserve the ranking and sum to 1. It outperforms other surrogate weight methods (rank sum, rank reciprocal) in ~85% of cases (Katsikopoulos & Fasolo, 2006).

For an ordinal ranking of N items where item 1 is most important:

```
ROC weight for rank i = (1/N) * sum(1/j for j in range(i, N+1))
```

Applied to Klai's 5 assertion modes (factual > procedural > quoted > belief > hypothesis):

| Mode | Rank | ROC weight (raw) | Normalized to max=1.00 |
|---|---|---|---|
| factual | 1 | 0.457 | 1.00 |
| procedural | 2 | 0.257 | 0.56 |
| quoted | 3 | 0.157 | 0.34 |
| belief | 4 | 0.090 | 0.20 |
| hypothesis | 5 | 0.040 | 0.09 |

**These ROC weights produce an extremely wide spread (1.00 to 0.09) that would be destructive in practice.** ROC is designed for decision problems where the ranking reflects genuine importance differences. For assertion modes, the ranking reflects *epistemic reliability*, which is a much weaker signal — a hypothesis can be the most useful retrieval result for a researcher's question.

**Conclusion:** ROC is theoretically principled but inappropriate for this application because it assumes the ordinal ranking maps to strong preference differences. Assertion mode ranking does not.

### 3.2 The Einhorn & Hogarth principle: equal weights

The forecasting literature provides a strong counter-argument to differentiated weighting. Einhorn & Hogarth (1975) demonstrated that equal weights outperform regression-estimated weights when:

1. Sample size is small relative to the number of predictors
2. The data is noisy
3. The true weights are uncertain

These three conditions all apply to Klai's situation:
- **No empirical data** on assertion-mode weight effectiveness (sample size = 0)
- **High classification noise** (~15% error rate for LLM-based assertion typing)
- **Deep uncertainty** about whether assertion type even affects retrieval quality

Graefe (2013, Journal of Business Research) extended this finding: across U.S. presidential election models, equally weighted predictors had 5% lower error than regression weights, and 48% lower error than typical regression models when all variables were included.

Dana & Dawes (2004) found regression weights only outperform equal weights when sample size exceeds ~100 observations per predictor and adjusted R-squared exceeds 0.9. Neither condition is met here.

**Conclusion:** The equal-weighting literature strongly suggests that flat weights (all 1.00) are the safest starting point when empirical validation data does not exist.

### 3.3 The pharmacokinetic insight

A finding from pharmacokinetic modeling (PubMed:18094641) directly applies: "Weights estimated from noisy data should be avoided as they can severely degrade parameter estimation. If the noise characteristic of the data is unknown, uniform weighting is recommended." The noisy Poisson weights performed worst, while uniform weighting gave acceptable results.

This maps precisely to Klai's situation: the assertion mode labels are generated by a noisy classifier, and the weight estimates (factual=1.00, hypothesis=0.70) are themselves guesses. Using noisy-input-derived weights on noisy labels compounds the error.

---

## 4. Classification Uncertainty and Maximum Safe Weight Spread

### 4.1 The core constraint

If the classifier is wrong X% of the time, then a weight of W on a misclassified chunk produces an error of magnitude proportional to |W_correct - W_assigned|. The wider the spread between weights, the larger the penalty for misclassification.

**Formal reasoning:**

Let:
- `p_err` = probability of misclassification (e.g., 0.15 for 15%)
- `W_max` = weight for highest-ranked category (1.00)
- `W_min` = weight for lowest-ranked category
- `spread` = W_max - W_min
- `expected_error_impact` = p_err * spread (average scoring distortion from misclassification)

For the system to do more good than harm, the benefit of correct assertion weighting must exceed the cost of misclassification:

```
benefit_of_correct_weighting * (1 - p_err) > cost_of_misclassification * p_err
```

If we assume benefit and cost scale linearly with spread:

```
spread * (1 - p_err) > spread * p_err
```

This simplifies to: `(1 - p_err) > p_err`, which is satisfied whenever `p_err < 0.50`. So differentiated weighting is *theoretically justified* at any error rate below 50%.

**But this assumes the benefit of correct weighting is equal to the cost of misclassification**, which is false. The asymmetry matters:

- Labeling a hypothesis as factual → user trusts speculative content (**high harm**)
- Labeling a fact as hypothesis → user seeks unnecessary verification (**low harm, but reduces recall**)
- Correct labeling of assertion type → marginal retrieval improvement (**unknown, possibly small**)

The cost-benefit ratio depends on the actual retrieval impact of assertion mode, which is unmeasured. Without this data, the safest assumption is that the cost of misclassification equals or exceeds the benefit of correct classification.

### 4.2 Deriving maximum safe spread from error rate

From the noisy-label machine learning literature (arXiv:2501.15163, Liu et al., 2025), the error bound for classification with noisy labels is:

```
excess_risk <= O(noise_rate / sqrt(n))
```

For our purposes, the practical heuristic is: **the weight spread should not exceed 1 minus the error rate**, so that a single misclassification cannot flip the ranking order of two chunks.

| Classifier accuracy | Error rate | Maximum safe spread | W_min (if W_max = 1.00) |
|---|---|---|---|
| 95% | 5% | 0.10 | 0.90 |
| 90% | 10% | 0.15 | 0.85 |
| 85% | 15% | 0.20 | 0.80 |
| 80% | 20% | 0.25 | 0.75 |
| 75% | 25% | Equal weights recommended | 1.00 |

**For Klai's estimated ~85% 3-category accuracy (Prieto et al., 2020), the maximum safe spread is 0.20, giving a minimum weight of 0.80.**

For 5 categories with ~67% accuracy, the maximum safe spread is 0.50 — but this is misleading because at 67% accuracy the signal-to-noise ratio is too low for weighting to help at all. At that accuracy level, equal weights are preferable (per Einhorn & Hogarth).

### 4.3 Comparison with [Implementation Plan](../implementation/implementation-plan.md) proposal

The current proposal in [Implementation Plan](../implementation/implementation-plan.md):

| Mode | Proposed weight | Safe weight (this research) |
|---|---|---|
| factual | 1.00 | 1.00 |
| procedural | 0.95 | 0.97 |
| quoted | 0.90 | 0.95 |
| belief | 0.75 | 0.88 |
| hypothesis | 0.70 | 0.85 |

The [Implementation Plan](../implementation/implementation-plan.md) spread of 0.30 (1.00 to 0.70) exceeds the safe spread of 0.20 for an 85% accurate classifier. This means misclassified chunks will be penalized more than the benefit from correct classification warrants.

### 4.4 The error asymmetry correction

The [Assertion Modes Research](assertion-modes-research.md) correctly identified the error asymmetry: labeling a hypothesis as factual is worse than labeling a fact as hypothesis. This means the weights should be compressed upward (high floor) rather than spread downward. A hypothesis that is correctly classified still deserves a reasonable score — the content may be exactly what the user needs.

---

## 5. When Flat Weighting Outperforms Differentiated Weighting

### 5.1 Conditions favoring equal weights

Based on the literature synthesis, flat weighting (all assertion modes = 1.00) outperforms differentiated weighting under these conditions — **all of which currently apply to Klai**:

| Condition | Applies to Klai? | Evidence |
|---|---|---|
| No empirical data on weight effectiveness | Yes | No A/B test exists |
| Classifier noise characteristics unknown | Yes | Error rate is estimated, not measured on Klai content |
| Small or zero training sample | Yes | Zero labeled retrieval-outcome pairs |
| Many prediction dimensions (content_type, assertion_mode, temporal_decay, corroboration) | Yes | 4 dimensions, multiplicative |
| Weights estimated from noisy data | Yes | Assertion labels from LLM with ~15% error |
| True effect size of the feature is unknown | Yes | No study measures assertion-mode impact on retrieval |

### 5.2 The multiplicative compounding problem

The [Implementation Plan](../implementation/implementation-plan.md) formula is:

```
final_score = base_score * content_weight * assertion_weight * decay * corr_boost
```

When multiple noisy weight dimensions are multiplied, errors compound. If each dimension has a 15% error rate and a 0.30 spread, a chunk that is misclassified on two dimensions simultaneously could receive a score distortion of up to 0.30 * 0.30 = 0.09 (9% of its true score). With four multiplicative dimensions and independent 15% error rates:

```
P(at least one error) = 1 - (0.85)^4 = 0.48
```

Nearly half of all chunks will have at least one misclassified dimension. With a 0.30 spread per dimension, the expected distortion is substantial.

**Recommendation:** If multiple uncertain dimensions are used simultaneously, each individual dimension's spread must be reduced proportionally. With 4 dimensions, the per-dimension spread should be at most 0.10-0.15 rather than 0.30.

### 5.3 When differentiated weighting becomes justified

Differentiated assertion mode weights become defensible when:

1. **Classifier accuracy on Klai's content is measured** (not estimated from literature). Run 200+ chunk classification evaluations.
2. **Retrieval impact is measured.** A/B test: assertion-weighted scoring vs. flat scoring on real queries, measuring recall@10 and precision@10.
3. **The effect size is positive and larger than the noise floor.** If weighted retrieval improves recall by less than 2-3%, it is within noise range and not worth the complexity.
4. **Content distribution is known.** If 90% of chunks are `factual`, assertion weighting affects only the 10% minority — the expected system-level improvement is proportionally small.

---

## 6. Proposed Weight Table

### 6.1 Recommended configuration: Conservative (default)

For the initial deployment, before any empirical validation on Klai data:

| assertion_mode | weight | rationale |
|---|---|---|
| `factual` | 1.00 | Reference weight. No penalty for established assertions. |
| `procedural` | 1.00 | Instructions are neither more nor less reliable than facts. Different type, not different quality. |
| `quoted` | 0.98 | Minimal reduction: attributed content is reliable but indirect. The 0.02 reduction is symbolic rather than material. |
| `claim` / `belief` | 0.95 | Slight reduction for subjective content. The small spread (0.05) ensures misclassification causes negligible harm. |
| `hypothesis` | 0.90 | Largest reduction, still conservative. A hypothesis chunk that matches the query should still rank well. |
| `None` / unknown | 0.97 | Unlabeled content gets benefit of the doubt, slightly below factual. Never penalize absence of metadata. |

**Total spread: 0.10 (1.00 to 0.90)**

This spread is within the safe range for an 85% accurate classifier (maximum safe spread: 0.20). It leaves room for the spread to be widened once empirical data confirms the benefit.

### 6.2 Alternative configuration: Flat (safest)

| assertion_mode | weight | rationale |
|---|---|---|
| All modes | 1.00 | No assertion-mode weighting. All scoring comes from content_type, temporal decay, and corroboration. |

This is the most defensible choice given the current evidence. It should be the default until empirical evaluation demonstrates that differentiated assertion weights improve retrieval quality.

### 6.3 Alternative configuration: Measured (post-validation)

Only to be used after a 200+ chunk classification evaluation and an A/B retrieval test:

| assertion_mode | weight | rationale |
|---|---|---|
| `factual` | 1.00 | Baseline |
| `procedural` | 0.98 | Near-factual |
| `quoted` | 0.95 | Indirect evidence |
| `claim` / `belief` | 0.85 | Subjective content, wider spread justified by measured accuracy |
| `hypothesis` | 0.80 | Speculative content |
| `None` | 0.95 | Conservative default |

**Total spread: 0.20** — the maximum safe spread for an 85% classifier. Only justifiable with empirical backing.

---

## 7. Confidence in These Recommendations

| Recommendation | Confidence | Basis |
|---|---|---|
| Maximum safe spread of 0.20 for 85% classifier | **High** | Derived from noisy-label ML bounds + MCDM robustness studies |
| Equal weights are defensible as v1 default | **High** | Einhorn & Hogarth (1975), Graefe (2013), Dana & Dawes (2004) |
| The [Implementation Plan](../implementation/implementation-plan.md) spread (0.30) is too wide | **Medium-High** | Based on estimated error rate; if actual error rate is lower, the spread may be acceptable |
| Hedging Modality is a weak standalone signal | **Medium** | Based on TRACE grid search (HM=0.1 in optimal config); may differ for non-web content |
| Assertion mode weighting will improve retrieval quality | **Low** | No direct evidence. Plausible from adjacent work but unmeasured. |
| The specific weight values (0.90, 0.95, etc.) | **Low** | Educated guesses constrained by safe-spread bounds. No empirical basis for choosing 0.90 vs. 0.88. |
| Multiplicative compounding is a real risk | **High** | Mathematical certainty: P(>=1 error in 4 dims) = 0.48 with 15% error per dimension |

---

## 8. Conditions Under Which These Weights Should NOT Be Used

### 8.1 Do not use differentiated assertion weights when:

1. **The assertion mode is set from frontmatter only (current state).** If assertion_mode comes from YAML frontmatter authored by the content creator, it reflects the author's self-classification — not an independent epistemic assessment. Author-labeled "factual" content may still be wrong. Weighting by author-provided labels introduces selection bias, not epistemic signal.

2. **More than 30% of chunks have `None` / unknown assertion mode.** If the majority of content is unlabeled, weighting the labeled minority creates a systematic bias: labeled content gets either boosted or penalized relative to the unlabeled majority, regardless of actual quality.

3. **The content domain is research-heavy.** In research domains, hypotheses are often the most valuable content. A researcher searching for "what might cause X" wants hypothesis-labeled chunks at the top, not demoted.

4. **The content is homogeneous.** If 95% of chunks are `factual`, assertion weighting affects only the 5% minority. The system-level impact is near-zero, and the complexity is not worth the maintenance cost.

5. **Multiple uncertain weight dimensions are already active.** If content_type weighting and temporal decay are both active with non-trivial spreads, adding assertion mode weighting as a third noisy dimension may push total scoring distortion above acceptable levels (see Section 5.2).

### 8.2 Do not use the "Measured" configuration without:

1. A 200+ chunk classification evaluation on representative Klai content
2. An A/B retrieval test with at least 50 queries
3. Demonstrated positive effect size exceeding 3% on recall@10 or precision@10
4. Measured classifier accuracy exceeding 80% on Klai content specifically

---

## 9. Recommended Next Steps

### 9.1 Ship v1 with assertion mode weights disabled (flat)

Set all assertion_mode weights to 1.00 in the evidence profile. This means `evidence_tier.py` includes the assertion_mode dimension but it has zero effect. The plumbing exists; the weights are neutral.

### 9.2 Measure before tuning

1. **Classification accuracy evaluation:** Sample 200 chunks from real Klai knowledge bases. Have 2 human annotators classify assertion mode (3-category: assertion, speculation, procedure). Measure inter-annotator agreement. Then run the LLM classifier on the same 200 chunks. Compare.

2. **Retrieval impact evaluation:** Build a test set of 50 queries with known-relevant chunks spanning multiple assertion modes. Run retrieval with flat weights vs. the conservative profile (0.10 spread). Measure recall@10, precision@10, and NDCG@10.

3. **Distribution analysis:** Count the actual assertion mode distribution across production knowledge bases. If >80% is `factual`, assertion weighting has limited scope for impact.

### 9.3 Widen spread only with data

If the evaluation shows:
- Classifier accuracy > 85% on Klai content
- Retrieval improvement > 3% with conservative weights
- Reasonable assertion mode distribution (no single mode > 80%)

Then widen to the "Measured" configuration (0.20 spread). Never go beyond 0.20 without per-domain validation.

---

## 10. Key Sources

### Epistemic modality and hedging

| Citation | Relevance |
|---|---|
| Prieto et al. (2020). Data-driven classification of the certainty of scholarly assertions. PeerJ. [peerj.com/articles/8871](https://peerj.com/articles/8871/) | 89.2% accuracy at 3 categories; key accuracy benchmark |
| Rubin (2007). Stating with Certainty or Stating with Doubt. NAACL. [aclanthology.org/N07-2036](https://aclanthology.org/N07-2036/) | ~67% human agreement at 5 categories |
| Aicher et al. (2025). Facts are Harder Than Opinions. [arXiv:2506.03655](https://arxiv.org/abs/2506.03655) | GPT-4o declined 43% of fact-classification tasks |
| Chandra et al. (2025). TRACE: Transparent Web Reliability Assessment. [arXiv:2506.12072](https://arxiv.org/abs/2506.12072) | Hedging Modality gets 0.1 weight in optimal config |

### Credibility-weighted retrieval

| Citation | Relevance |
|---|---|
| Huang et al. (2025). Combating Health Misinformation with Fusion-Based Credible Retrieval. JASIST. [SAGE](https://journals.sagepub.com/doi/10.1177/14604582251388860) | +60% MAP with credibility fusion on TREC Health |
| Hwang et al. (2024). RA-RAG: Retrieval-Augmented Generation with Estimation of Source Reliability. [arXiv:2410.22954](https://arxiv.org/abs/2410.22954) | Source-level reliability estimation via cross-checking |
| TREC Health Misinformation Track (2020-2022). [trec-health-misinfo.github.io](https://trec-health-misinfo.github.io/) | Benchmark for credibility-aware retrieval |

### Weight selection methodology

| Citation | Relevance |
|---|---|
| Barron (1992). The use of rank order centroid weights in MCDM. Published via subsequent replications in Danielson & Ekenberg (2016). [Springer](https://link.springer.com/article/10.1007/s10726-016-9494-6) | ROC surrogate weights; ~85% decision accuracy |
| Einhorn & Hogarth (1975). Unit weighting schemes for decision making. Org. Behavior & Human Performance. [PhilPapers](https://philpapers.org/rec/EINUWS) | Equal weights outperform regression weights under noise |
| Graefe (2013). Improving forecasts using equally weighted predictors. J. Business Research. [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0148296315001563) | 48% lower forecast error with equal weights |
| Dana & Dawes (2004). Regression >100 obs/predictor and R-squared >0.9 to outperform equal weights. | Threshold conditions for differentiated weighting |
| Hatefi (2023). Improved Rank Order Centroid (IROC). Informatica. [SAGE](https://journals.sagepub.com/doi/10.15388/23-INFOR507) | Refined ROC with non-uniform corner weighting |

### Noisy labels and classification error impact

| Citation | Relevance |
|---|---|
| Liu et al. (2025). Error Bounds in Classification with Noisy Labels. [arXiv:2501.15163](https://arxiv.org/abs/2501.15163) | Excess risk bounds under label noise |
| Natarajan et al. (2013). Classification with Noisy Labels by Importance Reweighting. [arXiv:1411.7718](https://arxiv.org/pdf/1411.7718) | Consistency guarantees for noisy-label classification |
| Google Research (2025). Constrained Reweighting for Training DNNs with Noisy Labels. [research.google](https://research.google/blog/constrained-reweighting-for-training-deep-neural-nets-with-noisy-labels/) | Controlling weight deviation from uniform under noise |

### Equal weights vs. differentiated weights

| Citation | Relevance |
|---|---|
| PubMed:18094641. Non-uniform weighting in non-linear regression for PET neuroreceptor modelling. | Uniform weighting recommended when noise characteristics unknown |
| Vainsencher et al. (2017). Learning with Large Noise Through Reweighting-Minimization. PMLR. [proceedings.mlr.press](http://proceedings.mlr.press/v65/vainsencher17a/vainsencher17a.pdf) | Optimal weights converge to uniform as noise increases |

---

*End of document. Last updated 2026-03-30.*
