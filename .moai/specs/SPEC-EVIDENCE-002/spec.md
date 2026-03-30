# SPEC-EVIDENCE-002: Assertion Mode Weight Activation

> Status: Draft
> Priority: LOW (blocked on SPEC-EVIDENCE-001 + SPEC-TAXONOMY-001)
> Created: 2026-03-30
> Depends on: `SPEC-EVIDENCE-001` (evidence tier scoring + evaluation framework), `SPEC-TAXONOMY-001` (assertion mode vocabulary alignment)
> Research: `docs/research/assertion-modes/assertion-mode-weights.md`, `docs/research/assertion-modes/assertion-modes-research.md`
> Scope: `klai-retrieval-api/`, `deploy/knowledge-ingest/`

---

## Context

SPEC-EVIDENCE-001 builds the evidence tier scoring pipeline with four dimensions: `content_type` weighting, temporal decay, U-shape ordering, and a RAGAS evaluation framework. It also lays down assertion mode plumbing with **flat weights** (all modes = 1.00). The plumbing exists; the effect is zero.

This SPEC activates assertion mode as a differentiated scoring signal -- widening the weights from flat to non-uniform. The core challenge: no published system weights passages by epistemic assertion type and measures retrieval impact. The concept is novel. Starting weights must be derived from adjacent research (credibility-weighted IR, noisy-label ML, multi-criteria decision theory), and validated empirically on Klai data before production use.

The research (assertion-mode-weights.md) concludes that equal weights outperform estimated weights when no empirical data exists, classifier noise is high, and true effect is unknown (Einhorn & Hogarth 1975, Graefe 2013). All three conditions currently apply. Therefore, this SPEC gates activation on empirical validation and uses a conservative weight table (spread: 0.10) as the first non-flat configuration.

---

## Goal

Activate assertion mode as a non-flat scoring dimension in the evidence tier pipeline, gated on empirical validation that it improves retrieval quality. Provide per-org configurability so tenants can opt in or stay on flat weights.

---

## Gate Criteria

**All gates must pass before assertion mode weights are activated in production.** These are hard prerequisites, not aspirational targets.

### G1 — SPEC-EVIDENCE-001 delivered and validated

- [ ] `evidence_tier.py` deployed with content_type weighting active
- [ ] RAGAS evaluation framework operational (R8 from EVIDENCE-001)
- [ ] Baseline metrics (flat scoring) recorded
- [ ] Content type weighting shows statistically significant improvement over flat (Wilcoxon p < 0.05)

### G2 — SPEC-TAXONOMY-001 delivered

- [ ] Assertion mode vocabulary aligned between DB schema, MCP tools, and ingest pipeline
- [ ] Single canonical set of assertion mode values used end-to-end
- [ ] No unmapped values between any interface boundary

### G3 — Classifier accuracy measured on Klai content

- [ ] 200+ chunk classification evaluation completed on representative Klai knowledge base content
- [ ] At least 2 human annotators with measured inter-annotator agreement (Cohen's kappa >= 0.60)
- [ ] LLM classifier accuracy >= 80% against human ground truth on the same 200+ chunks
- [ ] Confusion matrix documented: which modes are most confused with each other?

### G4 — Assertion mode distribution analyzed

- [ ] Distribution of assertion modes across production knowledge bases documented
- [ ] No single mode constitutes > 80% of chunks (if it does, weighting has negligible system-level impact and should not be activated)
- [ ] Percentage of `None`/unknown assertion mode chunks < 30% (above this threshold, weighting introduces systematic bias)

### G5 — A/B retrieval test passed

- [ ] 50+ queries with known-relevant chunks spanning multiple assertion modes
- [ ] Comparison: flat weights (1.00 for all) vs. conservative weight table (Section R2)
- [ ] Minimum positive effect size: 3% improvement on recall@10 or precision@10
- [ ] Wilcoxon signed-rank test on paired results: p < 0.05

---

## Requirements (EARS)

### R1 — Assertion mode weight activation

**When** all gate criteria (G1-G5) are met **and** an org's evidence profile has `assertion_mode_enabled: true`, **the system shall** apply differentiated assertion mode weights from the org's evidence profile to the evidence tier calculation.

**When** `assertion_mode_enabled` is `false` (default), **the system shall** use flat weights (1.00 for all modes), identical to SPEC-EVIDENCE-001 behavior.

### R2 — Conservative weight table (v1)

**The system shall** ship the following as the default non-flat assertion mode weight table:

| assertion_mode | weight | rationale |
|---|---|---|
| `factual` | 1.00 | Reference weight. No penalty for established assertions. |
| `procedural` | 1.00 | Instructions are neither more nor less reliable than facts. |
| `quoted` | 0.98 | Minimal reduction: attributed content is reliable but indirect. |
| `claim` / `belief` | 0.95 | Slight reduction for subjective content. |
| `hypothesis` | 0.90 | Largest reduction, still conservative. |
| `None` / unknown | 0.97 | Unlabeled content gets benefit of the doubt, slightly below factual. |

**Total spread: 0.10** (1.00 to 0.90). This is within the safe range for an 85% accurate classifier (maximum safe spread: 0.20).

Rationale for spread constraint: with 4 scoring dimensions at ~15% error each, 48% of chunks have at least one misclassification. Per-dimension spread of 0.10 limits the scoring distortion from any single misclassification to at most 10% of the dimension's range.

### R3 — Measured weight table (v2, post-extended-validation)

**The system shall** support a "measured" weight configuration, activated only after extended validation (Section R7):

| assertion_mode | weight | rationale |
|---|---|---|
| `factual` | 1.00 | Baseline |
| `procedural` | 0.98 | Near-factual |
| `quoted` | 0.95 | Indirect evidence |
| `claim` / `belief` | 0.85 | Subjective content, wider spread justified by measured accuracy |
| `hypothesis` | 0.80 | Speculative content |
| `None` / unknown | 0.95 | Conservative default |

**Total spread: 0.20** -- the maximum safe spread for an 85% classifier. Only activatable after R7 criteria are met.

### R4 — Per-org evidence profile configurability

**The system shall** allow per-org configuration of assertion mode behavior in the evidence profile:

```python
{
    "assertion_mode_enabled": False,       # default: flat weights
    "assertion_mode_weights": "conservative",  # "flat" | "conservative" | "measured" | custom dict
    "assertion_mode_weight_overrides": {},  # per-mode overrides, e.g. {"hypothesis": 0.92}
}
```

**When** `assertion_mode_weights` is a string, **the system shall** resolve it to one of the predefined weight tables (flat, conservative, measured).

**When** `assertion_mode_weights` is a dict, **the system shall** use the provided weights directly, validating that all values are between 0.50 and 1.00 and that the spread does not exceed 0.25.

### R5 — Automatic revert conditions

**The system shall** automatically revert an org to flat weights (and log a warning) when any of the following conditions are detected:

1. More than 30% of the org's chunks have `None`/unknown assertion mode
2. More than 80% of the org's chunks share a single assertion mode
3. The org's content is flagged as research-heavy (future: domain classifier or manual tag)

These conditions are evaluated at evidence profile load time, not per-query.

### R6 — Shadow scoring for assertion mode weights

**When** assertion mode weights are first activated for an org, **the system shall** run in shadow mode for a configurable period (default: 7 days):

- Both flat and weighted assertion mode scores are computed
- Only flat scores are used for retrieval ranking
- Weighted scores are logged for offline comparison
- After the shadow period, if no degradation is detected, weighted scores become active

### R7 — Extended validation gate for measured weights

**The system shall** only allow the "measured" weight table (R3) when:

- [ ] Conservative weights have been active in production for >= 30 days
- [ ] A/B comparison of conservative vs. flat shows sustained improvement >= 3% on recall@10
- [ ] Classifier accuracy on the org's content specifically measured >= 85%
- [ ] No degradation on any query category (per-category analysis, not just aggregate)

---

## Acceptance Criteria

- [ ] `assertion_mode_enabled` flag exists in evidence profile, default `false`
- [ ] Conservative weight table (R2) is the default non-flat configuration
- [ ] Measured weight table (R3) exists but is not activatable without R7 criteria
- [ ] Per-org evidence profile supports assertion mode configuration (R4)
- [ ] Automatic revert conditions implemented and tested (R5)
- [ ] Shadow scoring mode works for initial activation period (R6)
- [ ] Weight spread validation rejects spreads > 0.25
- [ ] All weight values validated between 0.50 and 1.00
- [ ] Gate criteria evaluation script/checklist exists for G1-G5
- [ ] Integration tests cover: flat -> conservative transition, revert conditions, shadow mode
- [ ] RAGAS evaluation confirms >= 3% improvement with conservative weights over flat

---

## Architecture Fit

This SPEC extends the evidence tier pipeline built in SPEC-EVIDENCE-001. No new services or endpoints are introduced.

### Modified files

| File | Change |
|---|---|
| `retrieval_api/services/evidence_tier.py` | `_assertion_weight()` reads from profile instead of returning 1.00; add weight table resolution, spread validation |
| `retrieval_api/models.py` | Extend `EvidenceProfile` with `assertion_mode_enabled`, `assertion_mode_weights`, `assertion_mode_weight_overrides` |
| `retrieval_api/services/evidence_profile.py` (new or extended) | Per-org profile loading; revert condition evaluation (R5) |
| `retrieval_api/services/shadow_scoring.py` (new) | Shadow mode logging for assertion mode weight comparison |

### New evaluation artifacts

| File | Contents |
|---|---|
| `evaluation/assertion_mode_eval.py` | 200+ chunk classification accuracy evaluation script |
| `evaluation/assertion_mode_ab_test.py` | A/B comparison: flat vs. conservative weights on 50+ queries |
| `evaluation/gate_checklist.md` | Manual + automated gate criteria verification checklist |

### Score formula (unchanged from EVIDENCE-001)

```
final_score = reranker_score * content_type_weight * assertion_weight * temporal_decay
```

The only change is that `assertion_weight` returns values from the configured weight table instead of always returning 1.00.

---

## Evaluation Protocol

### Phase 1: Classifier accuracy (G3)

1. Sample 200+ chunks from representative Klai knowledge bases (at least 3 orgs, mixed content types)
2. Two human annotators classify each chunk into the canonical assertion modes
3. Measure inter-annotator agreement (Cohen's kappa; threshold: >= 0.60)
4. Run LLM classifier on the same chunks
5. Compare LLM labels vs. human majority-vote labels
6. Produce confusion matrix; document which mode pairs are most confused
7. **Pass criterion:** LLM accuracy >= 80%

### Phase 2: Distribution analysis (G4)

1. Count assertion mode distribution across all production knowledge bases
2. Per-org breakdown: which orgs have viable distribution (no single mode > 80%, unknown < 30%)
3. **Pass criterion:** At least 3 orgs have viable distribution for activation

### Phase 3: A/B retrieval test (G5)

1. Build test set: 50+ queries with known-relevant chunks, spanning at least 3 assertion modes per query set
2. Run retrieval with flat weights (baseline)
3. Run retrieval with conservative weight table (treatment)
4. Measure recall@10, precision@10, NDCG@10
5. Wilcoxon signed-rank test on paired results
6. **Pass criterion:** >= 3% improvement on recall@10 or precision@10, p < 0.05

### Phase 4: Shadow production (R6)

1. Activate shadow scoring for 2-3 pilot orgs with viable assertion mode distribution
2. Log both flat and weighted scores for 7 days
3. Analyze offline: does the weighted ordering produce measurably different (better) results?
4. **Pass criterion:** No degradation on any query category; positive trend on aggregate metrics

### Phase 5: Extended validation for measured weights (R7)

1. Conservative weights active for >= 30 days
2. Continuous monitoring shows sustained improvement
3. Org-specific classifier accuracy measured >= 85%
4. Per-category analysis shows no degradation
5. **Pass criterion:** All R7 conditions met for >= 30 consecutive days

---

## Revert Protocol

### When to revert to flat weights

| Trigger | Action | Scope |
|---|---|---|
| Shadow scoring shows degradation | Revert to flat, log incident | Per-org |
| A/B test fails (< 3% improvement or p >= 0.05) | Do not activate, remain on flat | Global |
| Org's content distribution changes (> 80% single mode or > 30% unknown) | Automatic revert (R5) | Per-org |
| Research-heavy domain detected | Automatic revert (R5) | Per-org |
| User/admin reports quality degradation | Manual revert via evidence profile | Per-org |
| Classifier accuracy drops below 80% (measured on new content) | Revert to flat, re-evaluate | Per-org |

### Revert mechanism

Reverting to flat weights is a configuration change (`assertion_mode_enabled: false`), not a code deployment. The evidence profile change takes effect on the next query. No downtime required.

---

## What Is Explicitly NOT in Scope

- Implementing the assertion mode classifier (HyPE prompt or fine-tuned model) -- that is a prerequisite, not part of this SPEC
- Corroboration boost (SPEC-EVIDENCE-003)
- User-facing confidence labels (research says: do not do this, CHI 2024 / ACL 2024)
- Taxonomy alignment (SPEC-TAXONOMY-001, a prerequisite)
- Content type weight changes (SPEC-EVIDENCE-001)
- Temporal decay changes (SPEC-EVIDENCE-001)

---

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Assertion mode weighting degrades retrieval for some query types | HIGH | Shadow scoring (R6), per-category evaluation, automatic revert (R5) |
| Classifier accuracy is lower than 80% on Klai content | MEDIUM | Gate G3 prevents activation; flat weights remain default |
| Content distribution is too homogeneous for weighting to help | MEDIUM | Gate G4 checks distribution before activation |
| Multiplicative compounding with other noisy dimensions | HIGH | Conservative spread (0.10), spread validation (max 0.25), per-dimension isolation in eval |
| Weight spread too aggressive for actual classifier accuracy | HIGH | Two-tier table: conservative (0.10) first, measured (0.20) only after extended validation |
| Org-specific content changes over time invalidate initial evaluation | MEDIUM | Periodic revert condition evaluation (R5), re-evaluation cadence TBD |

---

## Sources

- Einhorn & Hogarth (1975): equal weights outperform under uncertainty -- [PhilPapers](https://philpapers.org/rec/EINUWS)
- Graefe (2013): 48% lower forecast error with equal weights -- [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0148296315001563)
- Dana & Dawes (2004): regression weights need >100 obs/predictor and R-squared >0.9
- Prieto et al. (2020): 89.2% accuracy at 3 certainty categories -- [PeerJ](https://peerj.com/articles/8871/)
- Rubin (2007): ~67% human agreement at 5 categories -- [ACL Anthology](https://aclanthology.org/N07-2036/)
- TRACE/WebTrust (Chandra et al., 2025): hedging modality gets 0.1 weight -- [arXiv:2506.12072](https://arxiv.org/abs/2506.12072)
- Liu et al. (2025): noisy-label error bounds -- [arXiv:2501.15163](https://arxiv.org/abs/2501.15163)
- SPEC-EVIDENCE-001: `/.moai/specs/SPEC-EVIDENCE-001/spec.md`
- Full research: `docs/research/assertion-modes/assertion-mode-weights.md`, `docs/research/assertion-modes/assertion-modes-research.md`
