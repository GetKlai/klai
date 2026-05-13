---
id: SPEC-TAXONOMY-MERGE-DETECT-001
version: "1.0.0"
status: approved
created: 2026-05-07
updated: 2026-05-07
author: Mark Vletter
priority: medium
related:
  - SPEC-TAXONOMY-V2-001 (HDBSCAN-based bootstrap — direct parent)
  - SPEC-TAXONOMY-V2-001-FOLLOWUP-001 (UMAP pre-reduce, batched naming)
---

# SPEC-TAXONOMY-MERGE-DETECT-001: Auto-consolidate bootstrap proposals to 5-9 parents

## Summary

Wijzig de bestaande `generate_bootstrap_proposals_v2` zodat hij de 14-15
fijnmazige clusters die HDBSCAN+naming oplevert automatisch consolideert tot
5-9 broadere parent-categorieën **vóór** het submit-moment. Operator ziet in
de portal-UI alleen de geconsolideerde set — geen extra knop, geen extra
review-stap. De feature is een Clio-stijl top-down hiërarchie-reductie
([Anthropic Clio paper](https://arxiv.org/html/2412.13678v1)) met
percentage-gebaseerde balans-constraints.

## Motivation

V2 bootstrap produceert technisch correcte clusters (zie SPEC-TAXONOMY-V2-001),
maar voor een KB met diverse content levert dat 14-15 fijnmazige nodes op.
[Miller's Law](https://lawsofux.com/millers-law/) en navigatie-best-practices
adviseren 5-9 hoofdcategorieën voor browsing. De gap zit in semantische
overlap die HDBSCAN niet detecteert (clustert op vector-density, niet op
concept-coherentie).

**Gevalideerd op Voys/support** (zie §Validated results): bestaande 15 base
clusters worden via één LLM-judge call geconsolideerd tot 7-8 parents binnen
de 5-9 doelband. Per-parent descriptions zijn productie-kwaliteit (gegenereerd
via dezelfde `generate_node_description` die nu al voor base clusters draait).

## Scope

### In scope

1. Eén nieuwe helper `_consolidate_to_parents` in `proposal_generator.py`:
   - Input: lijst van `(cluster_id, name, description, sample_titles, doc_count, centroid)`
     plus KB-description, target_min, target_max
   - Output: lijst van `ParentCategory` met `name`, `description`,
     `child_cluster_ids`, `rationale`, gecombineerde `sample_titles` en
     samengetelde `document_count`
   - Eén LLM-call (Clio-stijl group-and-assign) + parallelle
     `generate_node_description`-calls per parent
2. Wijziging in `generate_bootstrap_proposals_v2`:
   - Na huidige Step 7 (dedup duplicates) en vóór huidige Step 8 (submit): nieuwe
     Step 7.5 die `_consolidate_to_parents` aanroept
   - Submit-loop submit **parents** ipv **base clusters**
   - Payload van elke parent-proposal bevat `child_cluster_names` voor
     operator-transparantie ("samengevoegd uit: ['CRM-configuratie - diverse
     platforms', 'CRM-configuratie - sector-specifieke oplossingen', ...]")
3. Bestaande `BootstrapResult` houdt huidige velden, krijgt
   `base_clusters_found: int` extra (om verschil tussen pre-consolidate en
   post-consolidate te kunnen loggen)
4. Settings in `config.py`:
   - `taxonomy_consolidate_target_min: int = 5`
   - `taxonomy_consolidate_target_max: int = 9`
   - `taxonomy_consolidate_enabled: bool = True` (kill-switch)
5. Failure-handling: als consolidate-call faalt (LLM timeout, parse error)
   → fall back op de oorspronkelijke base clusters submitten, log warning.
   Bootstrap mag niet falen op consolidate-fout.
6. Logging: één extra structured event `bootstrap_consolidate_complete`
   met `base_clusters`, `parents`, `largest_parent_pct`, `largest_parent_clusters`
7. Tests:
   - Unit-test op `_consolidate_to_parents` met gemockte LLM
   - Integratietest: synthetic 12-cluster fixture → ~5-9 parents, balance
     constraints toegepast
   - Failure-fallback test: LLM timeout → base clusters submitted
   - Skip-test: als base_clusters ≤ target_max → consolidate wordt
     overgeslagen, base clusters direct submitted

### Out of scope

- Geen nieuwe endpoints. Geen frontend-wijzigingen. Geen DB-migratie.
- Geen "Maak er minder van"-knop. Operator klikt de bestaande bootstrap-knop;
  het systeem doet de consolidatie automatisch.
- Geen hiërarchische taxonomie (parent_id linked sub-nodes). Alle
  geconsolideerde nodes blijven plat in de DB; child cluster names komen
  alleen in de payload als metadata, niet als afzonderlijke nodes.
- Geen wijziging aan de approve-flow. Operator approved een parent-proposal
  zoals elke andere new_node-proposal — er is geen verschil aan portal-side.

## Validated results (from dry-run iterations)

Het dry-run script `klai-knowledge-ingest/knowledge_ingest/scripts/dry_run_merge_consolidate.py`
heeft de techniek geverifieerd in vier iteraties:

| Versie | Aanpak | Resultaat op Voys/support |
|---|---|---|
| v0.2 | Pairwise centroid similarity + per-pair LLM judge | 15 → 13 (te conservatief — judge zei 22x KEEP_SEPARATE op pairs > 0.79 sim) |
| v0.3 | Clio-stijl: group-and-assign single-call met name + samples | 15 → 7, maar parent #1 had 7 children + 133 docs (30% van totaal) |
| v0.4 | + percentage-based balance (max 25% docs / 33% clusters per parent) | 15 → 7, grootste parent 17% docs / 5 children — binnen caps |
| v0.5 | + per-parent descriptions via `generate_node_description` | 15 → 8, descriptions productie-kwaliteit |

**Voorbeeld output v0.5** (compleet rapport in run-output van 2026-05-07 19:11):

```
Telefonie-instellingen en -configuratie    (6 clusters, 99 docs, 22%)
  Vragen en handleidingen over het instellen en configureren
  van telefoonsystemen en -diensten.

CRM-configuratie en -integratie            (3 clusters, 44 docs, 10%)
  Vragen en handleidingen over het instellen en koppelen van
  CRM-systemen aan andere software.

Gespreksbeheer en notificaties             (1 cluster, 8 docs, 2%)
Accountbeheer en fraudepreventie           (1 cluster, 13 docs, 3%)
Nummerbeheer en -overdracht                (1 cluster, 26 docs, 6%)
App- en webphone-ondersteuning             (1 cluster, 27 docs, 6%)
Netwerkconfiguratie voor VoIP              (1 cluster, 34 docs, 8%)
Partnerprogramma's en samenwerkingen       (1 cluster, 13 docs, 3%)
```

**Stochasticiteit**: bij `temperature=0.2` varieert het exact aantal parents
±1 tussen runs (vorige run gaf 7, deze 8). Operator review absorbeert die
variatie. Geen reden om naar `temperature=0.0` te gaan — dat zou ook fouten
reproduceren.

## Acceptance criteria

### Functional (EARS)

1. **AC-1 (Ubiquitous)** — When `generate_bootstrap_proposals_v2` produces
   `proposals_to_submit` with `len > settings.taxonomy_consolidate_target_max`,
   the system shall call `_consolidate_to_parents` between Step 7 and Step 8
   instead of submitting base clusters directly.

2. **AC-2 (Optional feature)** — Where `proposals_to_submit` count
   `<= settings.taxonomy_consolidate_target_max`, the system shall skip
   consolidation and submit base clusters directly.

3. **AC-3 (Ubiquitous)** — `_consolidate_to_parents` shall produce parents
   such that:
   - Total count is between `target_min` and `target_max + 2` (hard cap)
   - No single parent contains more than ~25% of total docs OR more than
     ~33% of base clusters (soft caps; quality > balance per the prompt)
   - Each base cluster is assigned to exactly one parent

4. **AC-4 (Ubiquitous)** — Each parent shall receive a description via
   `generate_node_description(parent.name, None, sample_titles_from_children)`
   running in parallel after the group-and-assign call.

5. **AC-5 (Event-driven)** — When the consolidate LLM-call fails (timeout,
   parse error, HTTP error), the system shall log
   `bootstrap_consolidate_failed` with the error and fall back to submitting
   base clusters as proposals (existing behavior). Bootstrap MUST NOT fail
   on consolidate failure.

6. **AC-6 (State-driven)** — While `settings.taxonomy_consolidate_enabled
   is False`, the system shall behave bit-identical to pre-MERGE-DETECT
   (no consolidate call, base clusters submitted directly). This is the
   documented kill-switch.

7. **AC-7 (Ubiquitous)** — Each parent-proposal payload shall include:
   - `suggested_name`: parent.name
   - `description`: parent.description (LLM-generated, user-facing)
   - `document_count`: sum of children's doc_count
   - `sample_titles`: union of children's top sample titles, capped at 10
   - `child_cluster_names`: list of original base cluster names
     (for operator transparency)
   - `cluster_centroid`: weighted mean of children's centroids
     (doc-count-weighted, unit-normalised)

8. **AC-8 (Ubiquitous)** — The system shall emit one VictoriaLogs entry
   per consolidate call with stable key `bootstrap_consolidate_complete`
   containing `kb_slug`, `org_id`, `base_clusters`, `parents`,
   `largest_parent_doc_pct`, `largest_parent_cluster_count`, `latency_ms`.

9. **AC-9 (Backward compatibility)** — `BootstrapResult` shall keep its
   existing fields (`documents_scanned`, `proposals_submitted`,
   `clusters_found`, `reason`). New optional field `base_clusters_found: int`
   is added — equals `clusters_found` when no consolidation happened, or
   the pre-consolidate count when it did.

### Non-functional

10. **AC-10** — End-to-end bootstrap latency for a KB with 1000 docs that
    triggers consolidation SHALL stay within the existing 60-second budget
    from SPEC-TAXONOMY-V2-001 AC-10. Budget split: bootstrap ≤ 50s,
    consolidate ≤ 10s.

11. **AC-11** — The consolidate LLM-call SHALL add at most ~2x the base
    naming-call's token cost (one group-and-assign call ≈ same tokens as
    one batched naming call, plus K parent description calls run in parallel).

### Testing

12. **AC-12** — Unit test: `_consolidate_to_parents` with mocked LLM
    returning a valid grouping → ParentCategory list with correct
    aggregated `document_count`, correct `child_cluster_ids` set,
    correct `sample_titles` union (capped at 10).

13. **AC-13** — Unit test: malformed LLM response (missing `parents` key,
    invalid `child_cluster_ids`) → raises ValueError; caller catches and
    falls back to base clusters per AC-5.

14. **AC-14** — Unit test: LLM forgets to assign some clusters →
    `_consolidate_to_parents` collects unassigned clusters under an
    "Overig" parent (mirroring the dry-run script's defensive behavior).

15. **AC-15** — Integration test (mocked LLM): synthetic 12-cluster
    fixture → ≥ target_min parents, ≤ hard_cap parents (target_max + 2),
    every base cluster assigned exactly once, balance caps respected.

16. **AC-16** — Skip-test: `proposals_to_submit` with len 4 (below
    target_max=9) → `_consolidate_to_parents` is NOT called, base clusters
    submitted directly. Verifies AC-2.

17. **AC-17** — Failure-fallback test: mocked LLM raises Exception →
    bootstrap completes successfully, base clusters submitted as
    proposals, warning log emitted. Verifies AC-5.

## Technical approach

### File-by-file changes

| File | Change | Est. LoC |
|---|---|---|
| `klai-knowledge-ingest/knowledge_ingest/proposal_generator.py` | Add `_MERGE_CONSOLIDATE_SYSTEM_PROMPT_TEMPLATE` (mirror of script's `_GROUP_AND_ASSIGN_SYSTEM_PROMPT_TEMPLATE`). Add `_consolidate_to_parents` async helper. Modify `generate_bootstrap_proposals_v2` Step 7→8 to insert Step 7.5 (with skip + fallback per AC-2/AC-5). Add `ParentCategory` dataclass alongside `BootstrapResult`. | ~250 |
| `klai-knowledge-ingest/knowledge_ingest/portal_client.py` | `TaxonomyProposal` dataclass already supports proposal_type='new_node' as a string + cluster_centroid; add optional `child_cluster_names: list[str] \| None = None` field. `submit_taxonomy_proposal` includes `child_cluster_names` in payload when non-null. | ~10 |
| `klai-knowledge-ingest/knowledge_ingest/config.py` | Add `taxonomy_consolidate_target_min: int = 5`, `taxonomy_consolidate_target_max: int = 9`, `taxonomy_consolidate_enabled: bool = True`. | ~3 |
| `klai-knowledge-ingest/knowledge_ingest/routes/taxonomy.py` | `BootstrapResponse` adds `base_clusters_found: int = 0`. | ~3 |
| `klai-knowledge-ingest/tests/test_taxonomy_v2_bootstrap.py` | Add `TestConsolidate` class for AC-12 through AC-17. Reuse existing fixtures + mock-litellm pattern. | ~250 |
| `klai-knowledge-ingest/knowledge_ingest/scripts/dry_run_merge_consolidate.py` | KEEP — useful for ongoing prompt-tuning + threshold-experimentation on different KBs without redeploying. | unchanged |

**Total**: ~516 LoC (~266 production + ~250 tests). Geen frontend, geen
DB-migratie, geen schema-wijziging. Eén PR.

### Insertion point in `generate_bootstrap_proposals_v2`

Huidige flow ([proposal_generator.py:418-697](klai-knowledge-ingest/knowledge_ingest/proposal_generator.py#L418-L697)):

```
... Step 7 (dedup duplicates) ends at line ~614 with proposals_to_submit ...
... Step 8 (description-gen + submit) starts at line ~624 ...
```

Nieuwe Step 7.5 zit precies daartussen:

```python
# Step 7.5: consolidate to 5-9 parents (SPEC-TAXONOMY-MERGE-DETECT-001)
if (
    settings.taxonomy_consolidate_enabled
    and len(proposals_to_submit) > settings.taxonomy_consolidate_target_max
):
    try:
        parents = await _consolidate_to_parents(
            base_clusters=proposals_to_submit,  # list of (cid, name) pairs
            cluster_doc_lists=cluster_doc_lists,
            cluster_map=cluster_map,
            document_embeddings=document_embeddings,
            kb_description=kb_description,
            target_min=settings.taxonomy_consolidate_target_min,
            target_max=settings.taxonomy_consolidate_target_max,
        )
        # Replace proposals_to_submit with parents
        proposals_to_submit = parents  # type now: list[ParentCategory]
        consolidate_succeeded = True
    except Exception as exc:
        logger.warning(
            "bootstrap_consolidate_failed",
            kb_slug=kb_slug,
            error=str(exc),
            base_clusters=len(proposals_to_submit),
        )
        # AC-5: fall back to base clusters
        consolidate_succeeded = False
else:
    consolidate_succeeded = False
```

De daaropvolgende submit-loop check op `consolidate_succeeded` om de juiste
shape op te bouwen voor `TaxonomyProposal`.

### Prompt design

Hergebruik van het exact-gevalideerde prompt uit
`scripts/dry_run_merge_consolidate.py`. De prompt bevat:
- `_NAMING_CRITERIA` shared base (bug-fixes blijven gesynchroniseerd)
- Miller's Law-context voor target_min/target_max
- Anti-name-disagreement guard
- Percentage-gebaseerde balans-caps die scaleren met `total_docs` en
  `n_clusters`
- Hard cap op `target_max + 2`

Prompt-tekst staat in het script, wordt 1-op-1 overgenomen naar
`proposal_generator.py::_MERGE_CONSOLIDATE_SYSTEM_PROMPT_TEMPLATE`. **Geen
duplicatie** — het script kan ofwel verwijderd worden ofwel zijn prompt
importeren uit `proposal_generator`.

Aanbeveling: het script importeert vanuit `proposal_generator` (single
source of truth). Toekomstige tuning gebeurt aan productie-prompt; script
test automatisch wat productie doet.

### Per-parent description generation

Hergebruik van de exact-gevalideerde flow uit het script:

```python
async def _generate_parent_descriptions(
    parents: list[ParentCategory],
    cluster_doc_lists: dict[int, list[DocumentSummary]],
) -> None:
    desc_tasks = [
        generate_node_description(
            p.name,
            None,
            _round_robin_titles(p, cluster_doc_lists),
        )
        for p in parents
    ]
    descriptions = await asyncio.gather(*desc_tasks, return_exceptions=True)
    for p, desc in zip(parents, descriptions, strict=True):
        p.description = desc if isinstance(desc, str) else ""
```

Round-robin titles: 2 titles per child, capped at 10 totaal. Mirror van
de logic in het script.

### Approve-flow op portal-side: ongewijzigd

Operator approveert een parent-proposal exact zoals een gewone new_node:
- API: `POST /api/app/knowledge-bases/{slug}/taxonomy/proposals/{id}/approve`
- Functie: `_execute_proposal_action` ([taxonomy.py:511-556](klai-portal/backend/app/api/taxonomy.py#L511-L556))
- Action voor `proposal_type='new_node'`: maakt `PortalTaxonomyNode`
  met `name`, `description`, `created_by`. Werkt ongewijzigd voor parent-proposals.

`child_cluster_names` in de payload wordt **niet uitgevoerd** bij approve —
het is puur metadata voor de operator om te zien waar deze parent uit komt.
Frontend kan dit later zichtbaar maken (out of scope deze SPEC).

## Risks

| Risk | Impact | Mitigatie |
|---|---|---|
| LLM-judge faalt midden in consolidate | Bootstrap zou kunnen falen | AC-5: fallback naar base clusters submit. Bootstrap voltooit altijd. |
| Stochastische output: aantal parents wisselt tussen runs | Verwarring voor operator op regenerate | Operator review absorbeert variatie. Variatie is ±1 parent bij temperature=0.2 (gevalideerd in script-runs). |
| LLM hallucineert child_cluster_ids die niet bestaan | Crash op type validatie | Defensieve filter in helper: `if cid not in valid_cids: drop + log`. Mirror van script-implementatie. |
| LLM laat clusters ongetoegewezen | Verlies van content in proposal-set | Defensieve "Overig"-bucket per script-pattern (AC-14). Operator ziet log + kan reviewen. |
| Latency over 60s voor zeer grote KBs | Caller-timeout | Existing budget gehouden; consolidate is single-call (~5-10s). Worst-case: 50s bootstrap + 10s consolidate = exact budget. |
| Kill-switch nodig in productie als prompt regression veroorzaakt | Klanten met draaiende KBs raken nieuwe bootstrap kwijt | `taxonomy_consolidate_enabled=False` env override. AC-6. |
| Script-prompt en productie-prompt gaan drift | Tuning op script werkt niet op productie | Script importeert prompt uit productie. Single source of truth. |

## Deployment

1. Code-deploy met `taxonomy_consolidate_enabled=True` (default).
2. Smoke-test op `getklai/voys-help-notion` (14 nodes, al approved): bootstrap
   produceert 0 nieuwe proposals (al bestaande nodes — dedup filter werkt vóór
   consolidate).
3. Smoke-test op `voys/support`: verwacht 5-9 parent proposals (ipv huidige 15).
4. Monitor `bootstrap_consolidate_complete` event over 1 week — kijk naar:
   - Distribution van `parents` count (verwacht: 5-9 voor de meeste KBs)
   - Distribution van `largest_parent_doc_pct` (verwacht: < 30%)
   - `bootstrap_consolidate_failed` rate (verwacht: < 5%)
5. Als regressie: `taxonomy_consolidate_enabled=False` in env, container restart
   herstelt oude gedrag binnen 30s.

## References

- [proposal_generator.py:418-697](klai-knowledge-ingest/knowledge_ingest/proposal_generator.py#L418-L697)
  — `generate_bootstrap_proposals_v2` (insertion point)
- [proposal_generator.py:94-104](klai-knowledge-ingest/knowledge_ingest/proposal_generator.py#L94-L104)
  — `_NAMING_CRITERIA` shared base
- [scripts/dry_run_merge_consolidate.py](klai-knowledge-ingest/knowledge_ingest/scripts/dry_run_merge_consolidate.py)
  — gevalideerd reference-implementation. Prompt + helper-flow worden 1-op-1
  overgenomen naar productie.
- [Anthropic Clio paper (arxiv 2412.13678)](https://arxiv.org/html/2412.13678v1)
  — top-down hiërarchie inspiratie
- [OpenClio prompts.py](https://github.com/Phylliida/OpenClio/blob/main/openclio/prompts.py)
  — pattern-spiegel
- [Laws of UX — Miller's Law](https://lawsofux.com/millers-law/) — backup voor 5-9 doel
- [LLM-Assisted Topic Reduction for BERTopic (arxiv 2509.19365)](https://arxiv.org/html/2509.19365v1)
  — alternatief overwogen (iterative agglomerative) maar afgewezen ten gunste
  van Clio-stijl single-call

## History

- **v0.1.0** (2026-05-07): originele SPEC met first-class `proposal_type='merge'`
  proposals + `_execute_premerge` flow + 27 acceptance criteria. Verworpen
  als overengineered voor het werkelijke testdoel.
- **v0.2.0** (2026-05-07): dry-run script-only SPEC. Doel: techniek
  valideren zonder productiecode te raken.
- **v1.0.0** (2026-05-07): integratie SPEC. Techniek gevalideerd via vier
  script-iteraties (zie §Validated results). Implementatie volgt deze SPEC
  als handleiding.
