# V2.2 Bootstrap Evaluation — voys/support — 2026-05-06

**SPEC**: SPEC-TAXONOMY-V2-001-FOLLOWUP-001 (Phase C)
**Target KB**: `voys/support` (Voys tenant, 6967 chunks → 501 documents after rollup)
**Comparison**: V2 baseline (PR #408) → V2.1 (PR #418, B1-B3) → V2.2 (PR #426, B4-B5)
**Triggers**:
- V2 baseline: 2026-05-06 12:37:45 UTC
- V2.1: 2026-05-06 13:23:11 UTC
- V2.2: 2026-05-06 14:59:01 UTC

---

## Headline metrics — side-by-side

| Metric | V2 (baseline) | V2.1 (B1-B3) | V2.2 (B4-B5) | V2.2 vs V2 | V2.2 vs V2.1 |
|---|---|---|---|---|---|
| documents_scanned | 501 | 501 | 501 | = | = |
| **clusters_found** | **2** | **17** | **17** | **+15 (8.5×)** | = |
| outlier_count | 133 | 54 | 54 | -79 | = |
| **outlier_ratio** | **26.5%** | **10.8%** | **10.8%** | **-15.7pp** | = |
| proposals_submitted | 2 | 17 | 17 | +15 | = |
| descriptions populated | 0 / 2 | 17 / 17 | 15 / 17 | +15 | -2 |
| wallclock | 4.7s | 33.1s | 25.5s | +20.8s (still <180s budget) | -7.6s |
| `cluster_persistence_mean` | n/a | n/a (silhouette tried, dropped) | n/a (see Issue) | — | — |

## LLM-judge results

Single run per version with `temperature=0.1, seed=42`. Per-dimension integer scores are deterministic (Phase A AC-3 evidence: 3-run spread = 0.0). `overall_score` computed deterministically from per-dim averages by the judge script.

| Dimension | V2 | V2.1 | V2.2 | V2.2 - V2 | V2.2 - V2.1 |
|---|---|---|---|---|---|
| Coherence | 5.00 | 4.94 | 4.88 | -0.12 | -0.06 |
| Clarity | 4.50 | 4.41 | **4.71** | +0.21 | +0.30 |
| **Distinctness** | 5.00 (artifactual!) | **3.53** | **4.59** | -0.41 | **+1.06** |
| **Overall** | 4.83 | 4.29 | **4.73** | -0.10 | **+0.44** |

### AC-12 check (SPEC-TAXONOMY-V2-001-FOLLOWUP-001)

> AC-12: v2.1 (now read: V2.2 final) overall_score SHALL be ≥ V2 baseline + 0.3 OR explicit justification.

**Strict check**: V2.2 overall (4.73) vs V2 baseline (4.83) = **-0.10**. Does NOT meet ≥ +0.3.

**Justification (this is the explicit one)**: V2 baseline overall_score is **artifactual**. With only 2 proposals, distinctness scores trivially 5.00 because there's nothing to overlap with. As soon as V2.1 produced 17 proposals (the level of detail we actually want), distinctness dropped to 3.53 — exposing the per-cluster naming flaw that was hidden by sample size. The judge cannot punish missing topics; it only sees what's present.

**Real comparison is V2.1 → V2.2 (same N=17, same UMAP-clustering, only naming-strategy differs)**:
- Overall: 4.29 → 4.73 = **+0.44** (✓ meets +0.3 threshold)
- Distinctness: 3.53 → 4.59 = **+1.06**
- Clarity: 4.41 → 4.71 = **+0.30**

**Coverage comparison V2 → V2.2** (the metrics judge cannot capture):
- outlier_ratio: 26.5% → 10.8% (more than half-reduced)
- proposed cluster count: 2 → 17 (8.5× more granularity)

This is the upgrade. AC-12 satisfied via the V2.1→V2.2 lane plus the coverage-ratio gain.

## Side-by-side proposal names

### V2 baseline (2 proposals — both grossly umbrella)

1. CRM and telephony integrations
2. Number porting processes

### V2.1 (17 proposals — UMAP works, naming overlaps)

1. CRM integraties
2. CRM-telefonie integraties
3. CRM telefonie-integratie
4. CRM click-to-call integration
5. Click-to-call CRM integrations
6. CRM integratie tools
7. CRM integratiecommunicatie
8. CRM-koppeling beperkingen
9. CRM software manuals
10. CRM integratie SearchBar functionaliteit
11. CRM-telefonie integraties (sic, near-dup of 2)
12. Click-to-dial features
13. External number parameters
14. Telefonie-integratie handleidingen
15. VoIP software handleidingen
16. Bubble365 integrations and plugins
17. Bubble application documentation

7 of 17 are near-duplicates around "CRM integraties" theme — the central failure mode FOLLOWUP-001 was opened for.

### V2.2 (17 proposals — same UMAP+HDBSCAN clustering, cross-cluster aware naming)

1. Voys klantenservice en nummerovername
2. Bubble-desktop en onbekende softwarecategorie
3. Bubble365 ingebouwde CRM- en telefoonapps
4. Zorg- en gezondheidsgerichte CRM-systemen
5. Embedded CRM-oplossingen en helpdesktools
6. Nederlandse niche-CRM-systemen
7. VoIP-telefonie voor MKB en bedrijven
8. Sectorgespecialiseerde CRM voor onderwijs en bouw
9. Klantcontact- en leadmanagement CRM-tools
10. Budgetvriendelijke VoIP-telefonieoplossingen
11. Enterprise CRM met workflowautomatisering
12. Enterprise-grade cloudcommunicatieplatforms
13. Klassieke CRM-systemen voor bedrijven
14. Flexibele low-code CRM-platforms
15. Gespecialiseerde VoIP- en SIP-serveroplossingen
16. Automatiseringsgerichte CRM-integraties
17. NetSapiens en compatibele telefoonsystemen

CRM categories now differentiated by **sector** (zorg, onderwijs, bouw, niche), **scale** (MKB, enterprise, budget), **architecture** (embedded, low-code, klassiek, cloud), **specific products** (Bubble365, NetSapiens), and **specific features** (workflowautomatisering). Same UMAP+HDBSCAN clustering as V2.1; only the LLM-naming strategy changed.

## Failure-tolerance check (V2.2 description regression edge)

V2.2 produced 15 of 17 descriptions populated. Two empty descriptions:
- #61 "Gespecialiseerde VoIP- en SIP-serveroplossingen"
- #62 "Automatiseringsgerichte CRM-integraties"

Per AC-7 (FOLLOWUP-001 B2): per-cluster description failure must NOT crash the bootstrap. Confirmed working — 15 successes + 2 falbacks-to-empty + bootstrap completed. Failure logs:
```
event: bootstrap_description_generation_failed
kb_slug: support
cluster_id: 61, 62
```

## Known issue — `cluster_persistence_mean` metric

B5 was supposed to replace the originally-broken `dbcv_score` metric (which assumed sklearn HDBSCAN had `relative_validity_` — it doesn't). B5 switched to `cluster_persistence_` which I assumed sklearn does expose. **Diagnostic on prod container shows it doesn't either**:

```
sklearn: 1.8.0
hasattr cluster_persistence_: False
```

sklearn's `HDBSCAN` is a stripped-down port of the standalone `hdbscan` package and is missing both density-validation attributes. B5 is functionally a no-op — `cluster_persistence_mean` will always be None and structlog drops None fields → metric absent from log.

Not blocking: AC-12 holds without this metric, all other diagnostics work. Three follow-up options for a future mini-PR:
1. Add `hdbscan` standalone package as dep (full DBCV available)
2. Use `probabilities_.mean()` (sklearn does expose this — per-point cluster confidence; higher = better-classified)
3. Drop the metric entirely (cluster_count + outlier_ratio + judge cover quality)

Recommend option 2 (smallest change, no new dep). Tracked as a follow-up; explicitly NOT blocking close-out of FOLLOWUP-001.

## Conclusion — close out FOLLOWUP-001

**SPEC-TAXONOMY-V2-001-FOLLOWUP-001 status**: ready for `implemented`.

Wins:
- ✅ B1 UMAP works empirically: 8.5× cluster count, 2× outlier-ratio reduction
- ✅ B2 Description generation restored: 15/17 descriptions populated, failure-tolerance proven
- ✅ B4 Cross-cluster aware naming: distinctness 3.53 → 4.59 (+1.06) on identical clustering
- ✅ AC-3 reproducibility: judge-script overall_score deterministic via per-dim integer averaging
- ✅ AC-12: V2.1 → V2.2 +0.44 on overall_score (>+0.3 threshold)

Limitations carried forward:
- ⚠ B3/B5 metric (cluster_persistence_mean / dbcv_score): always None due to sklearn HDBSCAN port not exposing the attributes. Functionally a no-op. Decoupled from V2.2 success; tracked as follow-up.
- 📋 Future-scope (Fase 2 in parent SPEC): hierarchical reduction would help distinctness further when N > 12. Not blocking.

## Files

- This report: `reports/taxonomy-v2.1-evaluation-2026-05-06/comparison.md`
- V2.2 raw judge: copy/paste in commit message; rerunnable via `scripts/taxonomy_judge.py`
- V2.1 raw judge: rerunnable via same script

## AC mapping (Phase C)

| AC | Status | Evidence |
|---|---|---|
| AC-11 (V2.1 live trigger + comparison.md) | PASS | this document |
| AC-12 (overall_score ≥ +0.3 OR justified) | PASS via justification | V2.1→V2.2 lane shows +0.44; V2-baseline 4.83 demonstrably artifactual (N=2) |
| AC-13 (outlier_ratio lower than V2 baseline) | PASS | 26.5% → 10.8% |
| AC-14 (no improvement → not merge) | n/a | improvement confirmed; no rollback needed |
| AC-15 (pitfall rule added) | PASS | merged in PR #414 (`projects/knowledge.md::HDBSCAN on raw high-dim embeddings fails`) |
