---
id: SPEC-TAXONOMY-V2-001-FOLLOWUP-001
version: "0.1.0"
status: implemented
created: 2026-05-06
updated: 2026-05-06
author: Mark Vletter
priority: high
related:
  - SPEC-TAXONOMY-V2-001 (parent — Clio-style adaptive bootstrap MVP)
  - SPEC-KB-024 (centroid-based classification, voor description-generation hergebruik)
---

# SPEC-TAXONOMY-V2-001-FOLLOWUP-001: Empirisch valideren en fixen van V2 bootstrap

## Summary

`SPEC-TAXONOMY-V2-001` (PR #408) is gemerged en deployed maar **niet empirisch gevalideerd** op echte data — alle tests in die PR gebruiken synthetische, expres goed-separable embeddings. Drie technische gaten bleven onontdekt:

1. **HDBSCAN op rauwe 1024-dim embeddings zonder UMAP** — bekend anti-pattern uit topic-modeling literatuur (curse of dimensionality maakt density-based clustering onbetrouwbaar)
2. **Description-generation regressie** — V1 genereerde een uitleg-tekst per categorie via een tweede LLM-call (`generate_node_description`); V2 zet `description=""`. Pure UX-regressie.
3. **Silhouette-score is verkeerde quality-metric** voor density-based clustering — DBCV (`hdb.relative_validity_`) is de juiste keuze

Deze SPEC dekt: **eerst empirisch meten op echte data**, **dan fixen**, **dan opnieuw meten** om de fix te valideren. Hergebruikt de bestaande `voys/support` KB (6967 chunks, 0 nodes) als testbed — perfect omdat we geen echte gebruikers hoeven te storen.

## Motivation

Process-pitfall die deze SPEC mede aanleiding gaf, vastgelegd in `pitfalls/process-rules.md` als onderdeel van deze SPEC:

> **agent-output-without-self-review (HIGH)**: When delegating non-trivial implementation to a subagent and the agent reports "all tests green", actually read the changed code yourself before committing. Subagent test-suites cover what they wrote, not what's correct. Especially for ML/algorithmic code where synthetic test fixtures rarely reflect real-world data shape.

Concreet: als ik `klai-knowledge-ingest/knowledge_ingest/clustering.py::cluster_documents_hdbscan` regel-voor-regel had gelezen voor het mergen, had ik gezien dat HDBSCAN direct op 1024-dim wordt aangeroepen zonder UMAP-pre-reduction. Dat is precies wat BERTopic best-practice aanmerkt als anti-pattern.

Verder: zonder echte gebruikers betekent niet "we kunnen niet valideren" — we hebben productie-data (`voys/support` met 6967 ingeste help.voys.nl + wiki.redcactus.cloud chunks) en kunnen daar zelf direct een bootstrap tegen draaien. Empirische validatie hoort altijd in de feedback-loop.

## Scope

### In scope

#### Fase A: empirische baseline (vóór fix)

1. **Direct trigger** van `bootstrap_proposals_v2` op `voys/support` via `docker exec klai-core-knowledge-ingest-1 curl localhost:8000/ingest/v1/taxonomy/bootstrap-proposals -H "X-Internal-Secret: ..."` — bypasst portal-auth en isoleert de bootstrap-flow
2. **Logs collecten**: `bootstrap_proposals_complete` event uit VictoriaLogs — `clusters_found`, `outlier_count`, `silhouette_score`, `proposals_submitted`
3. **Proposals lezen** via SQL of UI: namen + sample_titles per voorstel
4. **LLM-as-judge baseline-meting** — script dat klai-fast aanroept met:
   ```
   Hier zijn N voorgestelde taxonomy-categorieën voor een KB.
   Per categorie: naam + 5 sample-document-titels.
   Score elk op (1-5):
   - coherence (passen samples bij naam)
   - clarity (is naam duidelijk en niet generiek)
   - distinctness (overlapt categorie met andere)
   Geef ook een overall_score (gemiddelde).
   ```
5. **Baseline metrics** vastleggen in `reports/taxonomy-v2-baseline-2026-05-06/` (markdown)

#### Fase B: vijf fixes (twee PRs)

**B1-B3** landde in PR #418 (merged 2026-05-06).

**B4-B5** opgenomen in een follow-up PR na empirisch waarnemen van twee resterende issues tijdens Phase C V2.1 trigger:
- B4: cross-cluster aware batched naming (V2.1 op `voys/support` produceerde 17 clusters waarvan 7 bijna-identieke namen rond "CRM integraties" — UMAP+HDBSCAN vond geldige sub-clusters maar de per-cluster LLM-naming had geen awareness van andere clusters)
- B5: `dbcv_score` werd altijd None gelogd want sklearn 1.8 HDBSCAN heeft geen `relative_validity_` attribute (mijn ontwerp-aanname klopte niet). Vervangen door `cluster_persistence_mean` op basis van sklearn's beschikbare `cluster_persistence_`.

6. **UMAP-pre-reduction** voor HDBSCAN:
   - Dep `umap-learn>=0.5.6` in `klai-knowledge-ingest/pyproject.toml`
   - Helper `reduce_embeddings_umap(embeddings, n_components=10, n_neighbors=15)` in `clustering.py` — defaults uit BERTopic best-practice voor 1k-10k corpora
   - `cluster_documents_hdbscan` krijgt param `pre_reduce: bool = True` en roept reducer aan vóór HDBSCAN
   - Settings: `taxonomy_bootstrap_umap_n_components=10`, `taxonomy_bootstrap_umap_n_neighbors=15`
   - Graceful import-fallback: `try: import umap; except ImportError → log + run zonder reduction` zodat deploy niet breekt op één missing dep
7. **Description-generation in v2**:
   - Na de naming-fase, parallel `asyncio.gather` over `generate_node_description(name, parent_description=None, sample_titles)` per voorgestelde categorie
   - Hergebruik bestaande functie uit `klai-knowledge-ingest/knowledge_ingest/description_generator.py` (V1 gebruikt 'm al)
   - `BootstrapResult.proposals_submitted` aantal blijft hetzelfde; alleen de description-string in elk proposal wordt non-empty
   - Failure tolerance: als description-generatie faalt → `description=""` en proposal toch submitten (mirror V1 pattern)
8. **DBCV-score ipv silhouette**:
   - Vervang `silhouette_score(...)` door `hdb.relative_validity_` (sklearn HDBSCAN expose dit attribute na fit)
   - Hernoem log-veld `silhouette_score` → `dbcv_score` zodat oude en nieuwe metingen niet vermengen in dashboards
   - Test: assert dat `dbcv_score` aanwezig is in `bootstrap_proposals_complete` event en numeric of None

#### Fase C: re-measure en vergelijken

9. **Trigger v2.1** op `voys/support` (zelfde data, zelfde flow als Fase A)
10. **LLM-as-judge** opnieuw — vergelijking tegen baseline
11. **Beslis-rapport** in `reports/taxonomy-v2.1-evaluation-2026-05-06/`: tabel met v2 vs v2.1 op alle metrics. Als v2.1 duidelijk beter → mergen. Als gelijk of slechter → meer onderzoek voordat we de PR mergen.
12. **Pitfall-rule** toevoegen aan `.claude/rules/klai/pitfalls/process-rules.md::agent-output-without-self-review`

### Out of scope

- **Live trigger op klanten-tenants buiten voys/getklai** — geen echte gebruikers raken
- **Hiërarchische taxonomie** — Fase 2 van parent-SPEC blijft Fase 2
- **Outlier-driven re-bootstrap** — zelfde
- **Auto rebuild_kb** — zelfde
- **CI-integratie van LLM-as-judge** — dit is een one-shot validatie-script, niet permanent. Future scope.
- **Vervangen van bestaande v2 (rolback)** — feature-flag dekt het rollback-pad als nodig

## Acceptance criteria

### Empirical (Fase A — before fix)

1. **AC-1** — A live bootstrap trigger against `voys/support` SHALL produce a `bootstrap_proposals_complete` log event in VictoriaLogs with `clusters_found`, `outlier_count`, `silhouette_score`, `proposals_submitted` populated.

2. **AC-2** — The baseline measurement report `reports/taxonomy-v2-baseline-2026-05-06/baseline.md` SHALL include: raw metrics from AC-1, the full list of proposed category names with their sample titles, and the LLM-as-judge `overall_score` (average across all proposals on a 1-5 scale).

3. **AC-3** — The LLM-as-judge script SHALL be reproducible: same input → same scoring within 0.5 points (temperature=0.1, fixed model `klai-fast`).

### Code (Fase B — three fixes)

4. **AC-4 (Event-driven)** — When `cluster_documents_hdbscan` is called with `pre_reduce=True` (default), the system shall reduce embeddings via UMAP to `n_components=10` (default) before passing to HDBSCAN.

5. **AC-5 (Unwanted behavior)** — If the `umap` package is not importable, the system shall log `bootstrap_umap_unavailable_fallback` and continue clustering without pre-reduction (no crash, no failure of the bootstrap).

6. **AC-6 (Ubiquitous)** — For each proposal submitted by `generate_bootstrap_proposals_v2`, the system shall include a non-empty `description` string generated via `description_generator.generate_node_description`.

7. **AC-7 (Unwanted behavior)** — If `generate_node_description` fails for a particular cluster, the system shall submit that proposal with `description=""` and log `bootstrap_description_generation_failed`. The bootstrap as a whole MUST NOT fail.

8. **AC-8 (Ubiquitous)** — The `bootstrap_proposals_complete` log event shall include `dbcv_score` (float or None) instead of `silhouette_score`. The metric shall come from `hdb.relative_validity_`.

9. **AC-9** — Existing tests in `tests/test_taxonomy_v2_bootstrap.py` shall pass after the fixes (regression guard for the AC criteria of parent SPEC).

10. **AC-10** — Three new tests SHALL be added: (a) UMAP-fallback when import fails, (b) description-generation success and failure paths, (c) DBCV-score field present in completion log.

### Empirical (Fase C — after fix)

11. **AC-11** — A live trigger of v2.1 against `voys/support` SHALL produce a comparison report `reports/taxonomy-v2.1-evaluation-2026-05-06/comparison.md` with side-by-side metrics for v2 (baseline) vs v2.1.

12. **AC-12** — v2.1's `overall_score` from the LLM-as-judge SHALL be **≥ v2 baseline + 0.3** OR the report shall explicitly justify why a smaller delta is acceptable. (0.3 is roughly one half-step on a 1-5 scale — meaningfully better than noise.)

13. **AC-13** — v2.1's `outlier_count / doc_count` ratio SHALL be lower than v2 baseline (UMAP should reduce noise classification).

14. **AC-14 (Optional feature)** — Where v2.1 metrics show NO improvement, the PR shall not be merged. Instead, follow-up investigation is opened as a separate SPEC.

### Process

15. **AC-15** — `pitfalls/process-rules.md::agent-output-without-self-review` shall be added with rationale and source reference to this SPEC.

## Technical approach

### Fase A: empirical measurement

```bash
# 1. Trigger v2 directly (bypass portal auth)
ssh core-01 'docker exec klai-core-knowledge-ingest-1 sh -c \
  "curl -s -X POST http://localhost:8000/ingest/v1/taxonomy/bootstrap-proposals \
     -H \"X-Internal-Secret: \$INTERNAL_SECRET\" \
     -H \"Content-Type: application/json\" \
     -d {\"org_id\":\"368884765035593759\",\"kb_slug\":\"support\"}"'

# 2. Read proposals + log
ssh core-01 'docker exec klai-core-postgres-1 psql -U klai -d klai -c \
  "SELECT suggested_name, sample_titles FROM portal_taxonomy_proposals \
   WHERE kb_id IN (SELECT id FROM portal_knowledge_bases WHERE slug=\"support\") \
   AND status=\"pending\" ORDER BY created_at DESC;"'

# 3. LLM-as-judge — ad hoc Python script in klai-knowledge-ingest container
```

LLM-as-judge prompt template — `scripts/taxonomy_judge.py` (new):

```python
SYSTEM = """You are a taxonomy quality auditor. Score each proposed category
on three dimensions (1-5 scale):
- coherence: do the sample documents fit the category name?
- clarity: is the name specific and non-generic?
- distinctness: does this category clearly differ from the others?

Reply ONLY with JSON: {"scores": [{"name": "...", "coherence": N, "clarity": N, "distinctness": N}, ...], "overall_score": float, "summary": "..."}
"""
```

Use `temperature=0.1`, fixed model `klai-fast`, deterministic seed if available.

### Fase B: three commits

**Commit B1 — UMAP**

`clustering.py` additions:

```python
def reduce_embeddings_umap(
    embeddings: np.ndarray,
    n_components: int = 10,
    n_neighbors: int = 15,
    random_state: int = 42,  # reproducibility for tests
) -> np.ndarray:
    try:
        import umap
    except ImportError:
        logger.warning("bootstrap_umap_unavailable_fallback")
        return embeddings  # AC-5: graceful fallback
    reducer = umap.UMAP(
        n_components=n_components,
        n_neighbors=min(n_neighbors, max(2, len(embeddings) - 1)),
        metric="cosine",
        random_state=random_state,
    )
    return reducer.fit_transform(embeddings)


def cluster_documents_hdbscan(
    embeddings: np.ndarray,
    min_cluster_size: int = 5,
    pre_reduce: bool = True,
) -> tuple[np.ndarray, dict]:
    if pre_reduce:
        embeddings = reduce_embeddings_umap(embeddings)
    # ... rest unchanged, but metric switches to "euclidean" after UMAP
    #     since UMAP output is no longer cosine-meaningful
    ...
```

Note: UMAP-output is in a transformed space where euclidean distance approximates cosine on the original space. So after UMAP, switch HDBSCAN's metric from `"cosine"` to `"euclidean"`.

**Commit B2 — description**

`proposal_generator.py::generate_bootstrap_proposals_v2` after the naming-fase:

```python
# Generate descriptions in parallel
desc_tasks = [
    generate_node_description(
        name,
        parent_description=None,
        sample_titles=[doc.title for doc in cluster_doc_lists[cid][:5]],
    )
    for cid, name in proposals_to_submit
]
desc_results = await asyncio.gather(*desc_tasks, return_exceptions=True)

for (cid, name), desc in zip(proposals_to_submit, desc_results, strict=False):
    description = desc if isinstance(desc, str) else ""
    if not isinstance(desc, str):
        logger.warning(
            "bootstrap_description_generation_failed",
            kb_slug=kb_slug,
            cluster_id=cid,
            error=str(desc) if desc else None,
        )
    proposal = TaxonomyProposal(..., description=description)
    await submit_taxonomy_proposal(...)
```

**Commit B3 — DBCV**

```python
# In cluster_documents_hdbscan, after fit_predict:
dbcv = float(hdb.relative_validity_) if hasattr(hdb, "relative_validity_") else None
return labels, {
    "clusters_found": clusters_found,
    "outlier_count": outlier_count,
    "dbcv_score": dbcv,  # was: silhouette_score
}
```

Update all consumers in `proposal_generator.py` to use `dbcv_score`.

### Fase C: re-measurement

Repeat Fase A's commands against the v2.1 deploy. Run the same `taxonomy_judge.py` script. Generate `comparison.md`:

```markdown
| Metric | v2 baseline | v2.1 | Delta |
|---|---|---|---|
| clusters_found | X | Y | +/-Z |
| outlier_count | X | Y | -Z (lower better) |
| outlier_ratio | X% | Y% | -Z% (lower better) |
| dbcv_score | N/A | 0.XX | new metric |
| silhouette_score | 0.XX | N/A | dropped |
| overall_score (judge) | 3.X | 4.X | +0.X |
| coherence (avg) | 3.X | 4.X | +0.X |
| clarity (avg) | 3.X | 4.X | +0.X |
| distinctness (avg) | 3.X | 4.X | +0.X |
```

Plus side-by-side proposal-name listing zodat het mens-leesbare oordeel ook in het rapport staat.

## Risks

| Risk | Impact | Mitigatie |
|---|---|---|
| UMAP-output verschilt per run (stochastisch) | Niet-reproduceerbaar | `random_state=42` fixed in alle calls |
| `umap-learn` voegt build-tijd / image-grootte toe | Container-deploy traag | Wel een echte cost, maar acceptabel — image-size +~30MB; alternative is umap-learn-without-numba (~5MB minder) |
| LLM-as-judge is zelf onbetrouwbaar (zelfde model dat ook bootstrap doet) | Self-bias in eval | Acknowledge in rapport; future-scope: gebruik een ANDER model (bijv. gpt-oss als beschikbaar) als externe judge |
| Description-generatie verdubbelt LLM-cost per bootstrap | Klant-facturatie | Beperkt — N small calls (max ~20), parallel; <5s wallclock toevoeging |
| v2.1 scoort lager dan v2 op LLM-judge | Plan klopt niet | AC-14 maakt dit expliciet; PR niet mergen, follow-up SPEC openen om uit te zoeken waarom |
| HDBSCAN's `relative_validity_` wordt niet gevuld (te weinig clusters) | DBCV is None | Dat is acceptabel — None is een valide signaal; bestaande None-handling van silhouette werkt door |

## References

- Parent SPEC: `.moai/specs/SPEC-TAXONOMY-V2-001/spec.md`
- Parent PR: https://github.com/GetKlai/klai/pull/408 (merged 2026-05-06)
- [BERTopic best-practices — UMAP voor HDBSCAN](https://maartengr.github.io/BERTopic/getting_started/best_practices/best_practices.html)
- [UMAP for clustering — McInnes et al.](https://umap-learn.readthedocs.io/en/latest/clustering.html)
- [DBCV — Density-Based Clustering Validation (Moulavi et al. 2014)](https://www.dbs.ifi.lmu.de/~zimek/publications/SDM2014/DBCV.pdf)
- `.claude/rules/klai/projects/knowledge-ingest.md` — bestaande context
- `klai-knowledge-ingest/knowledge_ingest/description_generator.py` — bestaande descriptie-generator (V1 hergebruik)
- `klai-knowledge-ingest/knowledge_ingest/clustering.py` — V2 clustering helpers (te wijzigen)
