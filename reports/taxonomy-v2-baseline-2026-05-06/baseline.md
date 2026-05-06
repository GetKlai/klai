# V2 Bootstrap Baseline — voys/support — 2026-05-06

**SPEC**: SPEC-TAXONOMY-V2-001-FOLLOWUP-001 (Phase A)
**Trigger time**: 2026-05-06 12:37:45 UTC
**Target KB**: `voys/support` (Voys tenant, end-to-end test KB with help.voys.nl + wiki.redcactus.cloud, 6967 chunks indexed)
**Deployed code**: PR #408 commit `49d59b19` — `taxonomy_bootstrap_v2_enabled=True`
**Pre-state**: 0 taxonomy nodes, 4 pending proposals from a V1 run earlier the same day (kept as comparison data, not contaminating the V2 dedup since dedup is against `portal_taxonomy_nodes` not against pending proposals).

---

## Raw metrics

| Metric | Value | Source |
|---|---|---|
| `documents_scanned` | 501 | endpoint response |
| `clusters_found` | **2** | `bootstrap_proposals_complete` log |
| `outlier_count` | 133 | log |
| `outlier_ratio` | 26.5% (133/501) | computed |
| `proposals_submitted` | 2 | response + log |
| `silhouette_score` | 0.279 | log |
| Wallclock latency | 4.7s | `httpx.post` start-to-end timing |
| HTTP status | 200 | response |
| `reason` field | null | response (i.e. NOT `all_duplicates`; both new) |

`request_id`: `5968f79a-23e2-4845-b725-fba86d1e5085` (for VictoriaLogs cross-trace).

### Latency vs SPEC budgets

- AC-10 (parent SPEC) budget: <60s for 1k docs. **Met** (4.7s).
- AC-11 budget: <180s for 7k docs. Not exercised — voys/support rolled up to 501 docs (≈14 chunks/doc avg) so the 7k-chunk path effectively ran as a 501-doc clustering job.

### Coverage of the 501 documents

| Group | Count | % |
|---|---|---|
| In clusters | 368 | 73.5% |
| Outliers (HDBSCAN noise) | 133 | 26.5% |

Two clusters covering 368 docs averages **184 docs per cluster** — these are extremely broad umbrella categories. For 501 documents from a help-desk KB, an information-architect would expect 5-12 distinct top-level categories, not 2. This is the central diagnostic finding.

---

## V2 proposals (full text)

### Proposal #29 — "Number porting processes"

- **Description**: (empty — V2 description regression)
- **Sample documents** (top-5 closest-to-centroid):
  - "Nummerovername: Hoe werkt dit en wat doen we?"
  - "CS | Uitportering nieuw - wel verdere telefoonnummers"
  - "Porteringsverzoek afgehandeld verwerken"
  - "CS | Uitportering nieuw - geen verdere telefoonnummers"
  - "Porteringsverzoek geen gehoor"

### Proposal #28 — "CRM and telephony integrations"

- **Description**: (empty — V2 description regression)
- **Sample documents** (top-5 closest-to-centroid):
  - https://wiki.redcactus.cloud/nl/49-bubble365-ingebedde-crm-apps
  - https://wiki.redcactus.cloud/nl/49
  - https://wiki.redcactus.cloud/nl/crm-software/Dealerkit
  - https://wiki.redcactus.cloud/nl/crm-software/office365
  - https://wiki.redcactus.cloud/nl/crm-software/recruitnow

---

## LLM-as-judge — V2 baseline

Model: `klai-fast`, temperature 0.1, seed 42. Run 3 times for AC-3 reproducibility.

### Per-proposal scores (1-5 scale, integer)

| Proposal | Run 1 | Run 2 | Run 3 | Stable? |
|---|---|---|---|---|
| **CRM and telephony integrations** | coh 5, clar 4, dist 5 | coh 5, clar 4, dist 5 | coh 5, clar 4, dist 5 | YES |
| **Number porting processes** | coh 5, clar 5, dist 5 | coh 5, clar 5, dist 5 | coh 5, clar 5, dist 5 | YES |

**AC-3 satisfied**: per-dimension integer scores are bit-identical across all three runs. Spread = 0.0 points, well within the SPEC's "within 0.5 points" budget.

### Aggregate scores (computed from per-dim averages)

| Dimension | Mean across proposals |
|---|---|
| Coherence | 5.0 |
| Clarity | 4.5 |
| Distinctness | 5.0 |
| **Overall (mean of dimension means)** | **4.83** |

### Judge summary (from run 2, representative)

> "Both categories demonstrate high coherence, clarity, and distinctness. No overlapping issues detected; the taxonomy is well-structured and domain-specific."

### Judge-script anomaly to fix in Phase B

In runs 1 and 3 the LLM emitted the JSON key `"overlap_score"` instead of the requested `"overall_score"` (run 2 used the correct key). Per-dimension scores were unaffected. **Fix**: in Phase B's judge-script update, do not trust the LLM's `overall_score` field — compute it deterministically from the per-dimension scores. The current `taxonomy_judge.py` will be updated accordingly.

---

## V1 reference data (existing 4 pending proposals from earlier today)

V1's `generate_bootstrap_proposals` ran ~11:29 UTC (proposals 24-27) before PR #408 deployed. These give a free V1-vs-V2 comparison on the same KB, same content, different algorithm.

### V1 proposals

| ID | Name | Description present? | Notable |
|---|---|---|---|
| 24 | "CRM integrations" | Yes | All 4 proposals share **identical** sample_titles (V1 quirk: `documents[:5]` once for all) |
| 25 | "Telefonie integraties" | Yes | Same sample_titles as #24 |
| 26 | "Software handleidingen" | Yes | Same sample_titles |
| 27 | "Partner portaal" | Yes | Same sample_titles |

### V1 judge result

| Dimension | Mean |
|---|---|
| Coherence | 3.0 |
| Clarity | 3.5 |
| Distinctness | **2.0** |
| **Overall** | **2.83** |

> "The taxonomy proposals suffer from significant overlap and vague category definitions. The first three categories are nearly indistinguishable, with 'Software handleidingen' being an overly broad catchall."

The judge correctly punishes V1 for the cross-contaminated sample_titles. V1 had description-generation though, which V2 lacks.

---

## V1 vs V2 baseline — side-by-side

| Metric | V1 | V2 |
|---|---|---|
| Proposals submitted | 4 | 2 |
| Description per proposal | populated | **empty (regression)** |
| Sample-title diversity per proposal | identical across all proposals | unique per cluster |
| Clusters covering content | "all 50 sampled" (no real clustering) | 2 of 501 docs (368 covered) |
| Doc coverage | unknown (only 50 of 501 sampled) | 73% (368/501) |
| LLM-judge overall | 2.83 | **4.83** |
| LLM-judge distinctness | 2.0 | 5.0 |

V2's higher judge score is partly **artifactual**: with only 2 proposals and clean per-cluster samples, distinctness scores trivially high. The judge cannot see what topics are MISSING — it only evaluates what's present. The real story is:

- **V1**: too many badly-supported categories, broad and overlapping → judge spots the overlap
- **V2**: too few categories, narrowly supported but missing entire topic areas → judge can't tell

**Both are inadequate, in opposite ways.** Phase B's UMAP+description+DBCV fixes should produce 5-15 well-distinguished clusters with descriptions — both broader coverage AND maintained per-cluster quality.

---

## Diagnoses / triggers for Phase B

1. **HDBSCAN-on-1024-dim is broken in practice.** 26.5% outliers + only 2 clusters from a 501-doc help-desk KB confirms the BERTopic best-practice is not theoretical: UMAP-pre-reduction is required. Phase B Commit B1 is the fix.
2. **V2 description regression is real.** Both proposals have empty descriptions; admin gets no context for approval. Phase B Commit B2 is the fix.
3. **Silhouette 0.279 isn't very informative.** It rates the 2 clusters as moderately separable, but says nothing about whether they SHOULD be 2 clusters. Phase B Commit B3 (DBCV) is the fix — DBCV is HDBSCAN-aware.
4. **The LLM-as-judge has a coverage blind-spot.** It scores what's there, not what's missing. Phase C should add a complementary metric: % of corpus reachable from any proposed cluster (= 1 - outlier_ratio). The follow-up V2.1 measurement should track BOTH judge score AND coverage ratio, and AC-12 (≥+0.3 judge improvement) should be augmented with "AND outlier_ratio not worse".

---

## Reproducibility evidence (AC-3)

The judge script produced bit-identical per-dimension integer scores across 3 runs of V2. Aggregate via dimension means is therefore deterministic. The `summary` field varies in wording run-to-run (small natural-language variation under temperature=0.1) but does not affect the numbers.

Computed `overall_score` for V2: **4.83** (deterministic from per-dim scores).

---

## Files

- This report: `reports/taxonomy-v2-baseline-2026-05-06/baseline.md`
- Judge script: `scripts/taxonomy_judge.py`
- V2 proposals input: `reports/taxonomy-v2-baseline-2026-05-06/v2_proposals.json`
- V1 proposals input: `reports/taxonomy-v2-baseline-2026-05-06/v1_proposals.json`
- V2 raw judge runs: `reports/taxonomy-v2-baseline-2026-05-06/v2_judge_runs.json`
- V1 raw judge run: `reports/taxonomy-v2-baseline-2026-05-06/v1_judge_run.json`

---

## AC mapping

| AC | Status | Evidence |
|---|---|---|
| AC-1 (log event with required fields) | PASS | `bootstrap_proposals_complete` event logged with clusters_found, outlier_count, silhouette_score, proposals_submitted, kb_slug, org_id |
| AC-2 (baseline.md with metrics + proposals + judge score) | PASS | this document |
| AC-3 (judge reproducible within 0.5 points) | PASS | per-dim integer scores bit-identical across 3 runs (spread=0.0) |
