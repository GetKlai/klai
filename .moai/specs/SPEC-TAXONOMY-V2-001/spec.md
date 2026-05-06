---
id: SPEC-TAXONOMY-V2-001
version: "0.1.0"
status: approved
created: 2026-05-06
updated: 2026-05-06
author: Mark Vletter
priority: medium
related:
  - SPEC-KB-022 (taxonomy classification at ingest time)
  - SPEC-KB-024 (centroid-based classification)
  - SPEC-KB-026 (clustering & auto-categorise)
  - SPEC-KB-027 (taxonomy-aware retrieval)
  - SPEC-RAG-REBUILD-KB-001 (mass re-classify after taxonomy changes)
---

# SPEC-TAXONOMY-V2-001: Adaptive Clio-style taxonomy bootstrap

## Summary

Vervang de huidige single-shot taxonomy bootstrap (50-doc sample, hard-coded 3-8 categorieën) door een adaptieve, density-driven aanpak die schaalt met de diversiteit en grootte van een KB. Bouwt een (eventueel hiërarchische) taxonomie van het volledige corpus via embedding-clustering, waarna de LLM alleen nog clusters benoemt — niet zelf clustert.

Geïnspireerd op Anthropic's Clio (Dec 2024) en BERTopic best-practices. Hergebruikt bestaande Klai-onderdelen (bge-m3 embeddings in Qdrant, centroid storage, LiteLLM klai-fast model, `clustering.py`, `parent_id` boomstructuur in `portal_taxonomy_nodes`).

## Motivation

De huidige `generate_bootstrap_proposals` in `klai-knowledge-ingest/knowledge_ingest/proposal_generator.py` heeft drie productie-kwalitatieve gaten:

1. **`documents[:50]`** — willekeurige (chronologisch eerste) sample, ongeacht corpus-grootte. Voor `voys/support` (6967 chunks) is dat <1% — niche-topics buiten de eerste 50 worden structureel gemist.
2. **Hard-coded 3-8 categorieën in de system-prompt** — KB met 100 docs en KB met 10000 docs krijgen dezelfde range. Diversiteit van de content stuurt de uitkomst niet.
3. **Single-shot LLM-call** doet zowel clusteren als benoemen — twee taken die LLMs verschillend goed kunnen. Resultaat: oppervlakkige labels, missing topics, geen hiërarchie.

Plus: er is geen levende taxonomie — outliers tijdens normale ingest triggeren wel `maybe_generate_proposal` (≥3 unmatched), maar er is geen periodieke re-bootstrap die structurele content-shift opvangt.

Per `pitfalls/process-rules.md::scale-the-answer-to-the-problem`: dit is geen 5-minuten config-tweak. Voys/support produceert 100% untagged chunks → taxonomy-aware retrieval (SPEC-KB-027) heeft geen effect. Voor klanten met diverse, grote KBs is de huidige bootstrap onbruikbaar.

## Scope

### In scope (MVP — Fase 1)

1. **Density-based clustering** in `proposal_generator.py`:
   - Pak document-level embeddings van alle docs in de KB (rollup van chunks via gemiddelde, zoals `taxonomy_classifier` al doet)
   - Run HDBSCAN (`sklearn.cluster.HDBSCAN`, sklearn ≥1.3 — al in de stack via clustering.py)
   - `min_cluster_size = max(5, doc_count // 50)` — adaptief aan corpus-grootte
   - Cluster-count is uitkomst, niet input
2. **Per-cluster LLM-naming**:
   - Voor elk cluster: pak top-N closest-to-centroid documenten (N=8)
   - Eén LLM-call per cluster, parallel via `asyncio.gather`
   - Prompt: "Geef deze thematisch gegroepeerde documenten een naam (2-5 woorden)"
   - LLM krijgt KB-description (`kb.description`) als extra context-hint
3. **Outlier-handling**: HDBSCAN noise-label (-1) krijgt eigen cluster → wordt geproposed als generieke "Overig"-categorie OF (als #outliers > drempel) genegeerd voor Fase-1, opgehaald door Fase-3 (zie Future scope)
4. **Sampling**: top-N closest-to-centroid via Qdrant scroll met centroid-filter (vergelijkbaar met bestaande `load_centroids` flow)
5. **Backward-compat**: bestaande `POST /ingest/v1/taxonomy/bootstrap-proposals` endpoint behoudt zijn contract (response shape `{documents_scanned, proposals_submitted}`); nieuwe veld `clusters_found: int` toegevoegd
6. **Threshold-config** in `settings`: `taxonomy_bootstrap_min_cluster_size_floor`, `taxonomy_bootstrap_max_clusters` (cap voor extreem diverse KBs) — defaults zo gekozen dat huidige `getklai/voys-help-notion` (14 nodes) blijft werken zonder hertraining
7. **Diversity logging**: per bootstrap-call log `clusters_found`, `outlier_count`, `silhouette_score` (sklearn) naar VictoriaLogs voor offline tuning

### Future scope (Fase 2 — apart SPEC of follow-up)

- **Hiërarchische reductie** (Clio-recursie): wanneer Fase-1 >12 clusters oplevert, embed cluster-namen → re-cluster → super-categorieën met `parent_id`
- **Outlier-driven re-bootstrap**: scheduled job dat outliers van afgelopen 7 dagen verzamelt en triggers wanneer #outliers > drempel
- **Cluster-coherentie scoring** (LLM-as-judge per cluster): laag-coherente clusters worden bij naamgeving gemarkeerd "needs review"
- **UI: tree-view in CoverageWidget** voor hiërarchische taxonomieën
- **Gefaseerde `rebuild_kb`** automatisch na approval van bootstrap-proposals zodat bestaande chunks worden geclassificeerd tegen de nieuwe nodes (nu een handmatige stap per `knowledge.md::Taxonomy edits require rebuild_kb`)

### Out of scope

- Vervangen van per-document classification bij ingest (SPEC-KB-022/024 patroon blijft)
- Wijziging van de `portal_taxonomy_proposals` schema of approval-flow (zelfde proposals-tabel als nu)
- Synchronisatie tussen tenants (geen cross-tenant taxonomy-sharing)
- Migratie van bestaande KBs met taxonomy nodes naar hiërarchische structuur (nodes blijven plat tot admin handmatig parent_id zet)

## Acceptance criteria

### Functional (EARS)

1. **AC-1 (Ubiquitous)** — The system shall determine the number of taxonomy proposals from the corpus density via HDBSCAN clustering, not from a hard-coded range in the LLM prompt.

2. **AC-2 (Event-driven)** — When `POST /ingest/v1/taxonomy/bootstrap-proposals` is called for a KB, the system shall sample document-level embeddings from **all** documents in that KB (not the first 50).

3. **AC-3 (State-driven)** — While clustering, if `doc_count < 10`, the system shall return zero proposals and log `bootstrap_skipped_too_small_kb`. (Below this threshold, taxonomy is meaningless and the LLM call wastes budget.)

4. **AC-4 (Ubiquitous)** — For each cluster, the system shall send only the top-N (default N=8) documents closest to the cluster centroid to the naming-LLM, never the full cluster.

5. **AC-5 (Event-driven)** — When generating a category name, the system shall include `kb.description` (if non-empty) in the system-prompt as domain-context.

6. **AC-6 (Unwanted behavior)** — If the LLM returns a name that already exists (case-insensitive) in `portal_taxonomy_nodes` for this KB, the system shall not submit that proposal and shall log `bootstrap_proposal_skipped_duplicate_name`.

7. **AC-7 (Event-driven)** — When the cluster count exceeds `taxonomy_bootstrap_max_clusters` (default 20), the system shall keep only the top-K largest clusters and log `bootstrap_clusters_capped`.

8. **AC-8 (Optional feature)** — Where the new bootstrap path returns 0 proposals because all proposed names duplicate existing ones, the response shall be `{documents_scanned: N, proposals_submitted: 0, clusters_found: K, reason: "all_duplicates"}` so the caller can distinguish "bootstrap ran successfully but found nothing new" from "bootstrap failed".

9. **AC-9 (Ubiquitous)** — The system shall emit one VictoriaLogs entry per bootstrap call with stable key `bootstrap_proposals_complete` containing `clusters_found`, `outlier_count`, `silhouette_score`, `proposals_submitted`, `kb_slug`, `org_id`.

### Non-functional

10. **AC-10** — End-to-end bootstrap latency for a KB with 1000 docs SHALL complete in under 60 seconds (clustering + N parallel LLM calls).

11. **AC-11** — End-to-end bootstrap latency for a KB with 7000 docs SHALL complete in under 180 seconds.

12. **AC-12** — The new path SHALL NOT increase LLM budget per category compared to the current single-shot prompt by more than 15% (per-cluster naming is N small calls instead of one large call; net cost should be roughly equal or lower because the system-prompt is smaller per call).

### Backward compatibility

13. **AC-13** — Existing portal-side endpoint `POST /api/app/knowledge-bases/{slug}/taxonomy/bootstrap` SHALL keep its current response shape; `clusters_found` is added as a new optional field.

14. **AC-14** — Existing `getklai/voys-help-notion` (14 nodes) re-bootstrapped with the new code SHALL NOT propose names that duplicate any of the existing 14 (covered by AC-6).

15. **AC-15** — The classification path at ingest time (`routes/ingest.py::ingest_document`, lines 349-382) SHALL be untouched — it continues to classify against existing nodes regardless of how those nodes were created.

### Testing

16. **AC-16** — Unit test: HDBSCAN with synthetic embeddings (3 clear clusters of 20 vectors + 5 outliers) returns 3 clusters and labels outliers as -1.

17. **AC-17** — Unit test: cluster-count adapts to corpus size — a KB-fixture with 100 docs returns ≤8 clusters; one with 1000 docs returns >5 clusters; one with 9 docs returns 0 proposals (AC-3).

18. **AC-18** — Integration test (with mocked LiteLLM): full bootstrap flow on a 200-doc fixture KB writes N proposals to `portal_taxonomy_proposals` where N matches `clusters_found`.

19. **AC-19** — Regression test: `getklai/voys` (existing 6 nodes) re-bootstrapped → 0 new proposals (AC-14, all duplicates).

## Technical approach

### Architecture overview

```
Current (single-shot):                   New (Clio-style):

LLM ←─ [50 docs]                         All docs ──► embedding rollup
LLM ──► 3-8 names (json)                                │
                                                         ▼
                                          HDBSCAN ──► K clusters + outliers
                                                         │
                                          Top-8 per cluster (closest to centroid)
                                                         │
                                                         ▼
                                          K parallel LLM calls (one per cluster)
                                                         │
                                                         ▼
                                          K names → K proposals
```

### Component changes

| Component | Change |
|---|---|
| `klai-knowledge-ingest/knowledge_ingest/proposal_generator.py` | New `generate_bootstrap_proposals_v2`. Old function blijft tot rollout, dan deprecated. |
| `klai-knowledge-ingest/knowledge_ingest/clustering.py` | Hergebruiken — HDBSCAN-helper toevoegen als die er nog niet zit. |
| `klai-knowledge-ingest/knowledge_ingest/portal_client.py` | `fetch_kb_metadata` toevoegen om `kb.description` op te halen voor de prompt (nu wordt alleen `taxonomy_nodes` opgehaald). |
| `klai-knowledge-ingest/knowledge_ingest/config.py` | Drie nieuwe settings: `taxonomy_bootstrap_min_cluster_size_floor` (default 5), `taxonomy_bootstrap_max_clusters` (default 20), `taxonomy_bootstrap_top_n_per_cluster` (default 8). |
| `klai-knowledge-ingest/knowledge_ingest/routes/taxonomy.py` | `bootstrap_proposals` endpoint switcht naar v2; response model krijgt `clusters_found: int` veld. |
| `klai-portal/backend/app/api/taxonomy.py` | Response schema uitgebreid met `clusters_found`. |
| `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/taxonomy.tsx` | Toon `clusters_found` in de "X categorieën voorgesteld" toast. Geen structurele change. |

### Data flow

1. `bootstrap_proposals` endpoint ontvangt `{org_id, kb_slug}`
2. Fetch all document-level embeddings uit Qdrant via centroid-aware scroll (`payload.org_id == org_id AND payload.kb_slug == kb_slug`, group by `source_url`/`source_ref`, average vectors)
3. HDBSCAN: `min_cluster_size = max(5, doc_count // 50)`
4. Voor elk cluster: top-8 closest to cluster mean (cosine similarity)
5. `asyncio.gather` over N tasks — elk task is één LiteLLM call met system-prompt + 8 docs + KB-description
6. Filter duplicates (AC-6), generate descriptions (parallel, hergebruik bestaande `description_generator`)
7. Submit proposals via bestaande `submit_taxonomy_proposal` portal-client call
8. Return `{documents_scanned, proposals_submitted, clusters_found}`

### Algoritme: top-N closest to centroid

```python
# pseudocode — implementatie in clustering.py
def closest_to_centroid(
    cluster_indices: list[int],
    embeddings: np.ndarray,
    n: int = 8,
) -> list[int]:
    cluster_vecs = embeddings[cluster_indices]
    centroid = cluster_vecs.mean(axis=0)
    sims = cluster_vecs @ centroid / (np.linalg.norm(cluster_vecs, axis=1) * np.linalg.norm(centroid))
    top_n_local = np.argsort(-sims)[:n]
    return [cluster_indices[i] for i in top_n_local]
```

### LLM prompt (per cluster)

```
System: You are a knowledge taxonomy assistant. You are naming a cluster of
documents from a knowledge base. The knowledge base is described as:
{kb.description}

Given 8 example documents that thematically belong together, suggest a
concise category name (2-5 words) that captures their shared theme.
Prefer the user's domain language over generic labels.

Reply with ONLY a JSON object: {"category_name": "<string>"}

User: 8 documents in this cluster:
- <title 1>: <preview 1>
- ...
- <title 8>: <preview 8>
```

`max_tokens=50`, `temperature=0.3`. Mirrort de bestaande `_suggest_category_name` prompt qua structuur.

### Deployment & migration

1. Code-deploy: nieuwe `v2` functie achter feature-flag `taxonomy_bootstrap_v2_enabled` (default `True` in dev, `False` in prod)
2. Smoke-test op `getklai/voys-help-notion` (14 nodes) → verwacht 0 of 1 nieuwe proposal (AC-14)
3. Smoke-test op `voys/support` (0 nodes, 6967 chunks) → verwacht ~10-20 proposals
4. Flag flippen op prod
5. Monitor `bootstrap_proposals_complete` event in VictoriaLogs voor 1 week — kijk naar silhouette_score distributie
6. Old `generate_bootstrap_proposals` wordt removed in vervolg-PR na 2 weken stabiliteit

## Risks

| Risk | Impact | Mitigatie |
|---|---|---|
| HDBSCAN-import faalt (sklearn versie mismatch) | Bootstrap stuk | Pin sklearn ≥1.3 in pyproject.toml + import-guard in clustering.py |
| Voor heel kleine KBs (< 30 docs) levert HDBSCAN 0 clusters op | Geen taxonomy mogelijk | AC-3 dekt ondergrens; documenteer in UI dat taxonomy een minimum corpus-grootte vereist |
| LLM-cost stijgt onverwacht door meer clusters bij grote KBs | Klant-facturatie | AC-7 cap (max 20 clusters); LLM-budget per call is kleiner door minder docs in de prompt |
| Parallel asyncio.gather raakt LiteLLM rate-limit | Bootstrap faalt | Begrenzen via `asyncio.Semaphore(5)` rond gather |
| Outliers worden alle in één "Overig" categorie gestopt en daarmee onzichtbaar voor admin | Verlies van niche-topics | Fase-2 outlier-driven re-bootstrap vangt dit; voor MVP loggen we outlier_count zodat we het kunnen monitoren |
| Re-runs bij dezelfde KB produceren wisselende namen door LLM-stochasticiteit | Verwarring voor admin | `temperature=0.3` houdt het redelijk stabiel; alle proposals zijn proposals — admin keurt expliciet goed of af |
| Docs zonder zinvolle content (lege chunks, alleen-frontmatter) worden alsnog gesampled | LLM ziet ruis | `proposal_generator` filtert al op `content_preview` lengte ≥ 50 chars; behouden in v2 |
| Embedding-rollup per document via Qdrant scroll is traag voor 10k+ docs | AC-11 latency miss | Pre-compute en cache document-level embeddings tijdens ingest in een aparte Qdrant collection (Future scope) — voor nu: scroll met `with_vectors=True` is ~3s per 1k docs, binnen budget |

## References

- [Anthropic Clio paper (arxiv:2412.13678)](https://arxiv.org/html/2412.13678v1) — primaire inspiratiebron voor de hiërarchische clusteringsmethode
- [BERTopic best practices](https://maartengr.github.io/BERTopic/getting_started/best_practices/best_practices.html) — `min_cluster_size` defaults voor 1k-10k corpora
- [TaxoAdapt (ACL 2025)](https://aclanthology.org/2025.acl-long.1442.pdf) — adaptive corpus-driven taxonomy expansion
- `klai-knowledge-ingest/knowledge_ingest/proposal_generator.py` — huidige implementatie (te vervangen)
- `klai-knowledge-ingest/knowledge_ingest/clustering.py` — bestaande clustering helpers (uitbreiden)
- `klai-knowledge-ingest/knowledge_ingest/taxonomy_classifier.py` — document-level embedding rollup (hergebruiken)
- `.claude/rules/klai/projects/knowledge-ingest.md::Taxonomy edits require rebuild_kb` — context voor Fase-2 future scope
- `.claude/rules/klai/pitfalls/process-rules.md::scale-the-answer-to-the-problem` — rationale waarom dit een SPEC verdient en geen 5-minuten patch
