---
id: SPEC-EVIDENCE-001-FOLLOWUP-001
version: "0.1.0"
status: draft
created: 2026-05-06
updated: 2026-05-06
author: Mark Vletter
priority: medium
related:
  - SPEC-EVIDENCE-001 (delivers shadow-mode pipeline; R9 leaves activation open)
  - SPEC-RAG-EVAL-001 (delivers RAGAS A/B harness with `variant` column)
audit_ref: .moai/audits/retrieval-coupling-2026-05-06/findings/F4-evidence-shadow-mode-default.md
---

# SPEC-EVIDENCE-001-FOLLOWUP-001: Activate or decommission Evidence-Tier scoring

## HISTORY

| Version | Date | Author | Change |
|---------|------|--------|--------|
| 0.1.0 | 2026-05-06 | Mark Vletter | Initial draft. Triggered by [retrieval coupling audit F4](../../audits/retrieval-coupling-2026-05-06/findings/F4-evidence-shadow-mode-default.md). |

---

## Context

`SPEC-EVIDENCE-001` shipped `2026-03-30` with status `Completed`. It delivers four scoring dimensions (`content_type` weights, temporal decay, U-shape ordering, PageRank boost) plus an `EVIDENCE_SHADOW_MODE=true` flag that means: **compute weighted score, log it, but serve the flat reranker order**. Quote from SPEC-EVIDENCE-001 R9:

> When evidence-tier scoring wordt gedeployed, the system shall beide scoring-methoden draaien en alleen flat scoring serveren. Evidence-tier resultaten worden gelogd voor offline vergelijking.

Five weeks later this is still the production default. Zero `EVIDENCE_*` env vars on `core-01` (verified 2026-05-06: `docker exec klai-core-retrieval-api-1 env | grep -i evidence` returns nothing). The CPU cost of computing both is paid every request. The benefit (or cost) on retrieval quality is unknown.

Two upstream changes since SPEC-EVIDENCE-001 invalidate the original "we don't have data yet" rationale:

1. **`SPEC-RAG-EVAL-001` landed nightly RAGAS (PR #369, 2026-05-05).** The `rag_eval_results` table now exists with a `variant` column explicitly designed for A/B comparison. The spec body of RAG-EVAL REQ-6 says: "WHEN a SPEC implementation team adds a `variant` column value (e.g. `contextual_v1`), the harness SHALL accept the variant via env var `RAG_EVAL_VARIANT` and tag every row with it." This is the missing measurement instrument.
2. **The retrieval coupling audit (2026-05-06) sampled 5 of the 284 `shadow_eval` events from the last 8 days.** Top-1 chunk changes between flat and evidence-tier scoring in **2 of 5 sample requests**; max absolute `score_delta` ranges 0.029–0.574. The change is **not marginal**, which means the activation decision actually matters for end-user output quality.

The risk of staying in shadow indefinitely: every iteration on Mistral / reranker / scoring drift moves the goal posts. After enough drift the offline-vs-online comparison becomes meaningless and the only path is decommission. Pinning a deadline avoids that erosion.

---

## Goal

Use the now-available RAGAS harness to **decide** between three terminal states for evidence-tier scoring within 30 days:

1. **Activate** — flip default behaviour, with staged rollout and rollback gates.
2. **Decommission** — remove the entire evidence-tier code path (saves CPU, reduces maintenance).
3. **Retain as plumbing-with-flags-off** — keep the code, default to inactive, available for future A/B experiments.

The scope of this SPEC is the **decision protocol** plus the rollout / removal mechanics for the chosen state. It does NOT introduce new scoring dimensions; it does NOT change SPEC-EVIDENCE-002 (assertion-mode) or SPEC-EVIDENCE-003 (corroboration).

---

## Out of scope

- Activating SPEC-EVIDENCE-002 (assertion-mode weights) — that has its own gating SPEC.
- Activating SPEC-EVIDENCE-003 (corroboration boost) — that has its own gating SPEC.
- Re-deriving `DEFAULT_EVIDENCE_PROFILE` weights — the existing weights stand for this experiment. If RAGAS shows that flipped activation regresses on a specific content_type, the corrective action is decommission or retain-flags-off, NOT a weight tweak. Weight tuning belongs to a separate research SPEC.

---

## Requirements

### REQ-1 — Run the RAGAS A/B comparison

**When** this SPEC is implemented, **the system shall** execute three nightly RAGAS runs in parallel for at minimum 7 consecutive days:

- `variant=baseline` — current production (flat reranker order, evidence-tier in shadow)
- `variant=evidence_tier_full` — `EVIDENCE_SHADOW_MODE=false` with all four dimensions enabled
- `variant=evidence_tier_temporal_only` — temporal decay only (`EVIDENCE_CONTENT_TYPE_ENABLED=false`, `EVIDENCE_PAGERANK_ENABLED=false`, U-shape disabled). This isolates the dimension with the strongest theoretical justification (information freshness) from the noisier ones.

The runs use the existing `rag_eval_results` infrastructure from SPEC-RAG-EVAL-001. Variants are selected via `RAG_EVAL_VARIANT` env var per nightly run.

### REQ-2 — Decision criteria

After the 7-day window, **the system shall** classify the result into one of three buckets:

1. **Positive (activate full)**: `evidence_tier_full` shows ≥ +0.02 mean improvement on RAGAS Context Precision **AND** ≥ +0.02 on Faithfulness, both with Wilcoxon signed-rank `p < 0.05` against `baseline` on the 50-curated subset.
2. **Mixed (activate temporal only)**: full variant fails REQ-2.1 but `evidence_tier_temporal_only` meets the same thresholds.
3. **Null (decommission OR retain-flags-off)**: neither variant beats baseline at the threshold above.

The thresholds match the SPEC-EVIDENCE-001 R8 acceptance criteria framing (Wilcoxon paired test on 50 curated queries) and are slightly above noise floor of typical RAG benchmarks (Tonic Validate / RAGAS published numbers cluster within ±0.01 on stable systems).

### REQ-3 — Rollout, gated

**If REQ-2 returns Positive**, **the system shall** ship the activation in three phases over 14 days:

- Phase 1 (days 1–3): set `EVIDENCE_SHADOW_MODE=false` for **5%** of `/retrieve` traffic via per-request hash on `org_id` (modulo 100 < 5). All other dimensions follow the chosen variant configuration. Monitor `retrieval_decision_record.flat_top_chunk_ids vs evidence_top_chunk_ids` divergence-per-request; alert in Grafana if `score_delta` p95 spikes > 0.5 or if `final_score=NaN` ever appears.
- Phase 2 (days 4–10): expand to 50%. Continue monitoring + collect user-feedback signal via `quality_score`/`feedback_count` on returned chunks (SPEC-KB-015 telemetry).
- Phase 3 (days 11–14): full 100% rollout. Default flag in `retrieve.py` flipped to `EVIDENCE_SHADOW_MODE=false`.

**If at any phase** the divergence alert fires OR the user-feedback signal regresses (e.g. mean quality_score for evidence-tier-served queries drops > 0.05 below baseline), **the system shall** automatically revert to shadow mode and escalate to operator review.

### REQ-4 — Decommission, if Null

**If REQ-2 returns Null** (and operator confirms after review), **the system shall** decommission the evidence-tier code path:

- Delete `klai-retrieval-api/retrieval_api/services/evidence_tier.py`.
- Delete the `evidence_tier_metadata` and `final_score` ChunkResult fields (no longer populated).
- Strip the `evidence_tier.apply()` call + shadow-mode branch from `retrieve.py:285-318`.
- Strip `EVIDENCE_*` env-var feature flags.
- Remove the `pagerank_weight` calculation and `entity_pagerank_max` payload-field plumbing if no other consumer exists (verify via grep).
- Strip evidence-related fields from `ChunkResult`/`RetrieveResponse` (Pydantic) — handle backwards compat: clients reading `final_score` get the `score` field instead, or accept the field's removal as part of the SPEC-controlled deprecation window.
- Remove SPEC-EVIDENCE-001-related telemetry: `decision_record["evidence_shadow_mode"]`, `shadow_eval` log line.

The decommission MUST land as a single PR with `before/after` performance proof: `total_ms` p95 reduction (current observed cost is ~0.5–1.5ms per request for `evidence_tier.apply` + `copy.deepcopy(reranked)`).

### REQ-5 — Retain-flags-off, if Null AND operator chooses retention

**If REQ-2 returns Null** and operator decides to keep the code (e.g. for future re-activation experiments), **the system shall** ensure `EVIDENCE_SHADOW_MODE` defaults to a **new** value `disabled` (not `true`), where:

- `EVIDENCE_SHADOW_MODE=disabled` → `evidence_tier.apply` is **NOT called**. Zero CPU cost. `final_score` populated by `score` directly. Default behaviour.
- `EVIDENCE_SHADOW_MODE=true` → existing shadow behaviour (compute, log, serve flat). For revival experiments only.
- `EVIDENCE_SHADOW_MODE=false` → live serve weighted scores. For ad-hoc per-org A/B experiments only.

This avoids paying the shadow CPU cost in steady state while preserving the activation path for a future SPEC.

### REQ-6 — Hard 30-day deadline

**If REQ-1 RAGAS data is not collected within 14 days** OR **REQ-2 decision is not made within 30 days of SPEC creation**, **the system shall** automatically default to REQ-4 (decommission) at day 30. Rationale: indefinite shadow mode is the worst outcome — it pays the cost without producing value, and Mistral/reranker drift makes the comparison stale every week.

The deadline is enforced by an explicit calendar entry + operator-set Grafana annotation on day 30 with the chosen decision recorded.

### REQ-7 — Document the outcome

**When** the decision is made (per REQ-2), **the system shall** update SPEC-EVIDENCE-001 status from `Completed` to one of:
- `Completed (activated)` — REQ-3 ran successfully, evidence-tier is live.
- `Completed (decommissioned per FOLLOWUP-001)` — REQ-4 ran, evidence-tier removed.
- `Completed (retained-flags-off per FOLLOWUP-001)` — REQ-5 ran, code retained but inactive.

Plus add a HISTORY row to SPEC-EVIDENCE-001 referencing this FOLLOWUP and the RAGAS metrics that drove the decision.

---

## Acceptance criteria

- [ ] Three RAGAS variants run nightly via `RAG_EVAL_VARIANT` for ≥ 7 consecutive days.
- [ ] `rag_eval_results` table contains rows for all three variants with all four RAGAS metrics populated.
- [ ] Wilcoxon paired test executed on the 50-curated subset; p-values reported.
- [ ] Decision recorded as Grafana annotation with explicit verdict (Positive / Mixed / Null).
- [ ] If Positive: 5% / 50% / 100% rollout completed within 14 days, with monitoring evidence (divergence histogram + feedback delta).
- [ ] If Null + decommission: evidence-tier code path removed; `total_ms` p95 reduction measured pre/post.
- [ ] If Null + retain: `EVIDENCE_SHADOW_MODE=disabled` becomes new default; CPU cost reduction measured.
- [ ] SPEC-EVIDENCE-001 status updated with HISTORY row.
- [ ] Operator-confirmed deadline tracking (Grafana annotation at day 30) — REQ-6 fired or not.

---

## Risk

| Risk | Severity | Mitigation |
|---|---|---|
| RAGAS testset overlaps with chunk-set, biasing toward evidence-tier or baseline | MEDIUM | Use the existing curated 50-query suite; do not add synthetic queries that share embeddings with the corpus. Measure per-query results, not just aggregates. |
| Mistral router drift during the 7-day window invalidates the comparison | MEDIUM | Pin `synthesis_model=klai-fast` for all three variants. Document any LiteLLM config change as an annotation. |
| RAGAS metric noise > effect size on small testset | MEDIUM | 7-day window collects ≥ 350 evaluations per variant (50 queries × 7 days). Wilcoxon assumes paired observations — same query, different variant. |
| Phase-3 100% rollout introduces a regression we did not catch in 50% phase | HIGH | Auto-revert to shadow on divergence/feedback alert; manual abort path documented. |
| Decommission accidentally removes a payload field still consumed by an external client | HIGH | Pre-flight `grep -rn "final_score\|evidence_tier_metadata"` across all repos before merging the decommission PR. Maintain a 1-week deprecation announcement window before field removal. |

---

## Implementation phases

| Phase | Trigger | Deliverable |
|---|---|---|
| 1 | This SPEC merged | `RAG_EVAL_VARIANT` configured for the three variants in `klai-knowledge-ingest` Procrastinate cron; baseline + 2 evidence variants run nightly. |
| 2 | Day 7 | Aggregate the data, run Wilcoxon, classify per REQ-2, update SPEC with verdict. |
| 3a (Positive) | Day 7+ | 5% canary rollout per REQ-3. |
| 3b (Null + decommission) | Day 7+ | Decommission PR per REQ-4. |
| 3c (Null + retain) | Day 7+ | Retain-flags-off PR per REQ-5. |
| 4 | Day 30 (forcing) | If no decision yet, REQ-6 forces decommission. |

---

## References

- Audit finding: `.moai/audits/retrieval-coupling-2026-05-06/findings/F4-evidence-shadow-mode-default.md`
- SPEC-EVIDENCE-001: `klai-retrieval-api/retrieval_api/services/evidence_tier.py` source + R9 shadow-mode definition.
- SPEC-RAG-EVAL-001: `klai-knowledge-ingest/knowledge_ingest/eval/` harness + `rag_eval_results` schema.
- `Lost in the Middle` (Liu et al. 2023, [arXiv:2307.03172](https://arxiv.org/abs/2307.03172)) — original U-shape ordering motivation.
- `GM-Extract` (Nov 2025, [arXiv:2511.13900](https://arxiv.org/html/2511.13900)) — recent finding that U-shape mitigations sometimes backfire on modern long-context LLMs. Supports the case for empirical validation rather than assumed-good ordering.
