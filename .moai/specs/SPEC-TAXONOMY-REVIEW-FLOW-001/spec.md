---
id: SPEC-TAXONOMY-REVIEW-FLOW-001
version: "0.1.0"
status: draft
created: 2026-05-07
updated: 2026-05-07
author: Mark Vletter
priority: high
related:
  - SPEC-TAXONOMY-V2-001 (HDBSCAN bootstrap — direct parent)
  - SPEC-TAXONOMY-MERGE-DETECT-001 (auto-consolidate to 5-9 parents — created the centroid-aggregation issue)
  - SPEC-KB-024 (centroid-based classification — provides the matching primitive)
  - SPEC-KB-026 (auto-categorise flow that parents inherit at approve-time)
---

# SPEC-TAXONOMY-REVIEW-FLOW-001: Taxonomy review-and-apply UX + tagging-coverage fix

## Summary

Live test van SPEC-TAXONOMY-MERGE-DETECT-001 op Voys/support legde **zes issues**
bloot in de bestaande taxonomy review-and-apply flow. Eén is functioneel
**kritiek** (90% van chunks blijft untagged na approval). De andere vijf zijn
UX/UI gaps die de feature beoordeelbaar maar onbruikbaar maken voor de
operator-workflow ("approve, edit, see what got tagged, refine").

Deze SPEC bundelt alle zes issues in één samenhangende oplossing zodat we
ze in één deploy-cyclus kunnen fixen ipv zes losse PRs die elk hun eigen
deeltestje aanvragen.

**Geen productie-gebruikers gebruiken deze feature nu**, dus we hebben
ruimte voor data-shape wijzigingen in `portal_taxonomy_proposals.payload`
(JSONB, geen DB-migratie) en gedrag-wijzigingen in approve/reject zonder
backward-compat zorgen.

## Motivation

Live-test op Voys/support (ingelogd als operator, drukte de "Suggest
categories" knop, kreeg 7 parent proposals, approved enkele):

| Gemeten | Verwacht | Issue |
|---|---|---|
| 90% chunks `untagged` na approve van alle parents | <30% | #1 centroid aggregation breekt classificatie |
| Edit op approved-categorie → Save → "niet opgeslagen, ook niet na refresh" | Persistent | #2 coverage cache nooit geinvalideerd |
| Approve → proposal verdwijnt direct uit lijst | Blijft zichtbaar met ander label | #3 status=pending filter |
| Per individueel approve → directe classify-job | Eén batch-classify aan einde | #4 per-approve auto-categorise wasteful |
| Geen Edit-knop op proposal-row | Edit voor approve | #5 edit-before-approve missing |
| Twee aparte lijsten (proposals + approved categories), elk eigen edit-flow | Eén unified review-list | #6 UI architectuur |

Combinatie van #1, #5, #6 maakt de feature **operator-onbruikbaar**:
- LLM stelt een naam voor die de operator wil aanpassen → kan niet
- Operator approves → proposal verdwijnt → kan niet meer terug
- 90% van zijn KB blijft ongetagd → categorieën zijn lege schillen

## Scope

### In scope (één PR)

#### Issue 1: Tagging coverage fix (CRIT — feature is broken without)

1. **Centroid-aggregatie aanpassen** in `proposal_generator.py::_consolidate_to_parents`:
   - Geen single `parent.centroid` meer (doc-count-weighted mean)
   - In plaats daarvan: `parent.child_centroids: list[list[float]]` —
     lijst van alle individuele child cluster-centroids
2. **`TaxonomyProposal` payload uitbreiden** in `portal_client.py`:
   - `cluster_centroid: list[float] | None` blijft (backward compat / single-cluster)
   - `child_centroids: list[list[float]] | None = None` — nieuw veld voor parents
3. **Approve-flow aanpassen** in `klai-portal/backend/app/api/taxonomy.py::approve_proposal`:
   - Bestaand pad: één `enqueue_auto_categorise` met `cluster_centroid`
   - Nieuw pad: als `payload.child_centroids` set, één `enqueue_auto_categorise`
     **per child-centroid** (alle met dezelfde `node_id`). Resultaat: chunks
     dichtbij ANY child-centroid worden getagd onder de parent.
4. **Re-tag bestaande approved nodes**: kort migratie-script (operator-tool, niet
   prod-runtime) dat voor de 7 al-approved nodes opnieuw `enqueue_auto_categorise`
   triggert per child-centroid. Eenmalig, hand-gerund.

#### Issue 2: Coverage-cache invalidation (HIGH — bug)

5. **`update_taxonomy_node`**: invalideer `_coverage_cache[(zitadel_org_id, kb_slug)]`
   na succesvolle commit
6. **`delete_taxonomy_node`**: idem
7. **`approve_proposal`**: idem (nieuwe node komt in coverage)
8. **Helper extraheren**: `_invalidate_coverage_cache(zitadel_org_id, kb_slug)`
   zodat toekomstige writes het niet vergeten

#### Issue 3: Proposals blijven zichtbaar na approve (HIGH UX)

9. **Frontend `proposalsQuery`**: switch van `?status=pending` naar
   `?status=pending,approved,rejected` (of haal de filter weg en doe het
   client-side, zodat we ook approved/rejected kunnen tonen)
10. **Backend `list_taxonomy_proposals`**: accepteer comma-separated `status`
    of laat het filter weg als argument leeg is (default: alleen pending,
    backward compat)
11. **Sortering**: meest-recent-mutated eerst (created_at OR reviewed_at desc),
    zodat een net-approved proposal bovenaan blijft

#### Issue 4: Defer auto-categorise tijdens batch-approve (MED)

12. **`approve_proposal` accepteert optionele query param** `auto_categorise: bool = True`.
    Wanneer `false`: skip de `enqueue_auto_categorise` call.
13. **Frontend `handleApplyAll`** (de "Apply to knowledge base" knop): pass
    `?auto_categorise=false` op elke approve-call in de loop. Aan het einde
    triggert de bestaande `backfillMutation` één keer een full re-classify.
14. **Individuele approve-knop blijft ongewijzigd**: default `auto_categorise=true`
    (voor losse approves wil je nog steeds direct tagging zien).

#### Issue 5: Edit-before-approve in proposal-row (HIGH UX)

15. **Backend `approve_proposal` accepteert optionele body**:
    ```python
    class ApproveProposalRequest(BaseModel):
        title: str | None = None
        description: str | None = None
    ```
    Wanneer set, override deze de `payload.suggested_name` / `payload.description`
    voor de nieuwe `PortalTaxonomyNode`. Persisteert ook in de `proposal.title`
    en `proposal.payload` zodat re-fetch de operator-edit toont.
16. **Frontend Edit-knop** in proposal-row, naast Approve/Reject. Click expand't
    inline form met name + description velden (hergebruik `InlineEdit`-pattern
    uit categories-list). "Save & Approve" knop POST't approve met body.
    "Cancel" collapse't.

#### Issue 6: Unified review-list UI (MED — bouwt op #3, #5)

17. **Vervang de twee aparte lijsten** (proposals-list + categories-coverage-list)
    door één component: `<TaxonomyReviewList>` dat beide types items rendert:
    - Approved nodes: huidige coverage-row (chunks count, coverage bar, edit/delete)
    - Pending proposals: rij met "New" badge, name, description, doc_count,
      child_cluster_names (uit payload), Approve/Reject/Edit knoppen
    - Recently-reviewed proposals (approved/rejected, < 1 uur oud): rij met
      status-badge ("Approved", "Rejected"), undo-actie (rejected → re-pending)
18. **State management**: na approve transitions de rij van "pending" naar
    "approved" zonder dat hij verdwijnt. Korte animatie of color-fade is
    optioneel; minimum is dat de rij in de lijst blijft.
19. **Volgorde**: approved met content (chunks > 0) bovenaan, pending eronder,
    recently-rejected onderaan met `text-muted-foreground`.

### Out of scope

- **Geen DB-migratie** (alle wijzigingen passen in bestaande JSONB payload)
- **Geen wijziging aan retrieval-side** (taxonomy-aware retrieval kant)
- **Geen merge-twee-pending-proposals UI**: operator handelt overlap via
  edit + reject-de-andere
- **Geen hiërarchie/sub-categorieën**: blijft flat (parent_id niet gebruikt)
- **Geen background-deferred classification queue redesign**: we gebruiken
  de bestaande Procrastinate `run_auto_categorise` task per child-centroid

## Acceptance criteria

### Functional (EARS)

#### Issue 1 — Tagging coverage

1. **AC-1 (Ubiquitous)** — When `_consolidate_to_parents` returns a `ParentCategory`
   that aggregated 2+ base clusters, the produced `TaxonomyProposal.child_centroids`
   shall be a list of length N (where N = number of children), each entry being
   the unit-normalised centroid of one base cluster. The legacy `cluster_centroid`
   field is set to the doc-count-weighted mean of children (backward-compat).

2. **AC-2 (Ubiquitous)** — For single-child parents and non-consolidated proposals,
   `child_centroids` SHALL be `None` (or unset) and `cluster_centroid` SHALL contain
   the cluster's own centroid. Behaviour bit-identical to pre-MERGE-DETECT.

3. **AC-3 (Event-driven)** — When `approve_proposal` reads a payload with
   `child_centroids` set, the system shall enqueue **N parallel** `auto_categorise`
   jobs (one per child centroid) with the same `node_id`. The Procrastinate worker
   tags an artifact iff any of the N matches its first-chunk centroid above
   threshold (set-OR semantics — natural since they all write the same node_id).

4. **AC-4 (Ubiquitous)** — On a 14-child consolidated KB (Voys/support fixture-equivalent),
   the post-approve `untagged_percentage` SHALL drop below **30%** (was 90% pre-fix).
   Validated against live Voys/support after the migration script re-tags the 7
   existing approved parents.

5. **AC-5 (Migration tool)** — A one-shot operator script
   `klai-portal/backend/scripts/retag_consolidated_nodes.py` accepts
   `(org_zitadel_id, kb_slug)` and re-triggers `enqueue_auto_categorise` per
   child-centroid for every node in that KB whose source proposal had
   `child_centroids` set. Idempotent (running it twice produces same end state).

#### Issue 2 — Coverage cache invalidation

6. **AC-6 (Ubiquitous)** — After `update_taxonomy_node` commits successfully,
   `_coverage_cache[(zitadel_org_id, kb_slug)]` shall be invalidated. The next
   GET `/coverage` recomputes and returns the updated description.

7. **AC-7 (Ubiquitous)** — Same invalidation rule applies to:
   `delete_taxonomy_node`, `approve_proposal` (when a node is created),
   `_execute_premerge` (when a parent node is created from merge proposal).

8. **AC-8 (Unit test)** — Test fixture: create node, GET coverage (caches),
   PATCH description, GET coverage → returns new description (not stale cached one).

#### Issue 3 — Proposals visible after approve

9. **AC-9 (Event-driven)** — When the proposals-tab loads, it shall display
   approved + pending + rejected proposals. Each shows a status badge.

10. **AC-10 (Ubiquitous)** — After clicking Approve on a pending proposal,
    the row remains visible with status badge changed from "New"/"Pending"
    to "Approved". The user can continue reviewing other proposals without
    page refresh.

11. **AC-11 (Optional feature)** — Where a proposal was rejected within the
    last hour, the UI may show an "Undo" affordance that POSTs a new endpoint
    to set status back to pending (out-of-scope for MVP, listed for follow-up).

#### Issue 4 — Defer auto-categorise during batch

12. **AC-12 (Optional feature)** — Where the approve endpoint receives query
    param `auto_categorise=false`, it shall skip `enqueue_auto_categorise`.
    The endpoint always creates the node, sets status=approved, and commits.
    Default `auto_categorise=true` keeps current behavior for individual approves.

13. **AC-13 (Event-driven)** — When the frontend "Apply to knowledge base"
    button runs, it shall:
    - Loop over pending proposals, calling approve with `?auto_categorise=false`
    - After all approves succeed, trigger the existing `backfill` endpoint once
    - The backfill classifies all chunks against the now-complete taxonomy

14. **AC-14 (Latency budget)** — On a 7-parent / 14-child Voys-equivalent KB,
    "Apply to knowledge base" SHALL complete in under 90 seconds (was ~3-5 min
    with per-approve auto-categorise + final backfill).

#### Issue 5 — Edit before approve

15. **AC-15 (Ubiquitous)** — A proposal-row in the unified review-list shall
    include an Edit button (pencil icon) next to Approve and Reject.

16. **AC-16 (Event-driven)** — When the operator clicks Edit, the row expands
    inline to show editable name and description fields, with "Save & Approve"
    and "Cancel" buttons. "Cancel" collapses without changes.

17. **AC-17 (Event-driven)** — When the operator clicks "Save & Approve",
    the frontend POSTs to the approve endpoint with body `{title, description}`.
    Backend persists the override into both the proposal record (so reload
    reflects edit) and the new node.

18. **AC-18 (Ubiquitous)** — Edit-before-approve is purely a name/description
    override. It does NOT change `child_cluster_ids`, centroid, or doc_count
    in the payload — those reflect the LLM-proposed grouping which the operator
    is approving as-is.

#### Issue 6 — Unified review-list

19. **AC-19 (Ubiquitous)** — The taxonomy tab shall render a single list of
    rows (component: `<TaxonomyReviewList>`) containing both approved nodes
    and pending/recently-reviewed proposals.

20. **AC-20 (Ubiquitous)** — Each row shows: name, description, doc-count
    (or "Pending — N chunks expected" for non-yet-classified proposals),
    status badge, action affordances (Edit/Delete for approved, Edit/Approve/Reject
    for pending).

21. **AC-21 (Ubiquitous)** — The `<TaxonomyReviewList>` component reuses the
    existing `InlineEdit` pattern from the categories-list — both name and
    description are editable inline regardless of whether the row is approved
    or pending.

22. **AC-22 (Sorting)** — Default sort: approved-with-chunks first (descending
    chunk_count), then pending (newest first), then approved-without-chunks
    last (operator should review why), then rejected (muted, optional collapse).

### Non-functional

23. **AC-23** — Centroid-storage memory bound: storing N child-centroids per
    proposal is bounded by N ≤ taxonomy_consolidate_target_max + 2 (= 11) ×
    1024 floats × 4 bytes ≈ 45KB per parent proposal payload. Acceptable.

24. **AC-24** — Approve-loop with N=7 proposals + final backfill triggers
    7 + 1 = 8 HTTP calls (was 14 with per-approve auto-categorise). Latency
    well within 90s budget per AC-14.

### Backward compatibility

25. **AC-25** — Pre-existing single-cluster proposals (no `child_centroids` in
    payload) continue working with the legacy single-centroid auto-categorise
    path.

26. **AC-26** — The kill-switch from MERGE-DETECT-001 (`taxonomy_consolidate_enabled=False`)
    still works: bootstrap reverts to base-cluster proposals, which use the
    legacy single-centroid auto-categorise. AC-1/AC-2 inherently cover this.

### Testing

27. **AC-27** — Unit test for `_consolidate_to_parents`: verify that
    `child_centroids` is populated for multi-cluster parents and `None` for
    single-cluster fallback parents.

28. **AC-28** — Unit test for approve_proposal multi-centroid path: mocked
    `enqueue_auto_categorise`, assert called N times with the same node_id.

29. **AC-29** — Integration test for cache invalidation: GET coverage, PATCH
    node, GET coverage → asserts new description in response.

30. **AC-30** — Frontend component test for `<TaxonomyReviewList>`: renders
    approved, pending, rejected rows with correct affordances.

31. **AC-31** — E2E test (Playwright): trigger bootstrap → see proposals →
    edit one → approve → row stays visible with Approved badge → click "Apply
    to knowledge base" → see chunks tagged in coverage bars within 90s.

## Technical approach

### File-by-file changes

| File | Change | Est. LoC |
|---|---|---|
| `klai-knowledge-ingest/knowledge_ingest/portal_client.py` | `TaxonomyProposal` adds `child_centroids: list[list[float]] \| None = None`; submit_taxonomy_proposal includes it in payload | ~5 |
| `klai-knowledge-ingest/knowledge_ingest/proposal_generator.py` | `ParentCategory` dataclass adds `child_centroids: list[list[float]] = field(default_factory=list)`. `_consolidate_to_parents` populates per-child centroids alongside the existing aggregate `centroid`. submit-loop in `generate_bootstrap_proposals_v2` passes them through. | ~30 |
| `klai-portal/backend/app/api/taxonomy.py` — `approve_proposal` | Accept `body: ApproveProposalRequest \| None` (title, description overrides) and query `auto_categorise: bool = True`. Persist title/description overrides into proposal.title + payload before _execute_proposal_action. Multi-centroid path: when `payload.child_centroids` set, loop and enqueue per child. | ~50 |
| `klai-portal/backend/app/api/taxonomy.py` — `update_taxonomy_node`, `delete_taxonomy_node`, `_execute_premerge` | Call `_invalidate_coverage_cache(zitadel_org_id, kb_slug)` post-commit | ~15 |
| `klai-portal/backend/app/api/taxonomy.py` — `list_taxonomy_proposals` | Accept comma-separated `status` query param, default `pending`. Sort by `reviewed_at DESC NULLS LAST, created_at DESC`. | ~10 |
| `klai-portal/backend/scripts/retag_consolidated_nodes.py` (new) | Operator-tool: re-trigger enqueue_auto_categorise per child-centroid for already-approved consolidated nodes in a given KB | ~80 |
| `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/taxonomy.tsx` | `proposalsQuery`: drop `?status=pending`, use `?status=pending,approved,rejected` (or omit). Render unified list. Add Edit-state for proposals. handleApplyAll passes `?auto_categorise=false`. | ~150 |
| `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/taxonomy.tsx` | Extract or refactor `<TaxonomyReviewList>` from CoverageView to handle both proposal and approved states. | ~100 (refactor existing code, net new ~30) |
| `klai-portal/frontend/messages/{nl,en}.json` | i18n keys: `proposals_edit`, `proposals_save_and_approve`, `proposals_cancel`, `proposals_status_pending`, `_status_approved`, `_status_rejected` | ~10 |
| `klai-knowledge-ingest/tests/test_taxonomy_v2_bootstrap.py` | Tests for AC-27 (child_centroids populated) | ~80 |
| `klai-portal/backend/tests/test_taxonomy_proposals.py` | Tests for AC-28 (multi-centroid enqueue), AC-29 (cache invalidation), AC-12 (auto_categorise=false skip) | ~150 |

**Total**: ~680 LoC (~440 production + ~240 tests). One PR, no DB migration,
no schema change.

### Migration plan for existing approved nodes (Voys/support)

The 7 nodes Mark approved before this fix have only the aggregate parent
centroid stored. After this SPEC ships:

1. Operator runs `python scripts/retag_consolidated_nodes.py --org <id> --kb support`
2. Script reads each approved proposal's payload from `portal_taxonomy_proposals`
3. For each that has `child_centroids` set: re-trigger `enqueue_auto_categorise`
   per child centroid with the same `node_id`
4. For pre-fix proposals (no `child_centroids` in payload): script logs a
   warning — those nodes stay broken until next bootstrap re-runs and
   approves them. For Voys/support specifically: easier to delete the 7
   nodes + clear pending proposals + re-run bootstrap (we have no production
   users yet).

Mark's preference (no production users): **delete the 7 nodes + re-run bootstrap
after deploy**. The script is built for the eventual production case where we
can't just throw away approved state.

### UX redesign — visual sketch

```
┌──── Taxonomy ────┬──────────────┬────────┐
│  Categorieën & Coverage             [+ Add root]    [✨ Suggest]│
├─────────────────────────────────────────────────────────────────┤
│  CRM-configuratie                            ✏️ 🗑️  44 docs  10%│
│  Vragen en handleidingen over CRM-systemen…                     │
│  ████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░          │
├─────────────────────────────────────────────────────────────────┤
│  [NEW] Telefonie-instellingen en -configuratie    ✏️ ✓ ✗        │
│  Vragen en handleidingen over telefoonsystemen…                 │
│  Bevat: Telefoonconfig (3), Hardware, IP-telefoon (5 clusters)  │
├─────────────────────────────────────────────────────────────────┤
│  [NEW] Mobiele en webtoegang                       ✏️ ✓ ✗        │
│  ↓ in edit-mode:                                                 │
│  ┌─────────────────────────────────────┐ [Save & Approve]       │
│  │ name: Mobiele en webtoegang        │ [Cancel]                │
│  │ desc: Vragen over mobiele apps...   │                         │
│  └─────────────────────────────────────┘                         │
├─────────────────────────────────────────────────────────────────┤
│  [REJECTED] Overig                                  ↶ Undo      │
│  (rejected 5min ago — "merged_into_proposal_91")                │
├─────────────────────────────────────────────────────────────────┤
│  Untagged                                       4020 chunks 90%* │
│   * percentage drops to <30% after re-trigger fix lands         │
└─────────────────────────────────────────────────────────────────┘
                                                  [Apply to KB]
```

Approved en pending in dezelfde lijst, dezelfde inline-edit voor beide,
status badge as discriminator. "Apply to KB" knop blijft als batch-actie
voor alle pending proposals; doet auto_categorise=false voor elk en één
backfill aan het eind.

### Approve-flow detail (Issue 1 + Issue 4 + Issue 5 combined)

```python
class ApproveProposalRequest(BaseModel):
    title: str | None = None
    description: str | None = None

@router.post(".../approve")
async def approve_proposal(
    kb_slug: str,
    proposal_id: int,
    body: ApproveProposalRequest | None = None,  # Issue 5
    auto_categorise: bool = True,                # Issue 4
    ...
):
    proposal = ...  # fetch as before

    # Issue 5: persist operator overrides into proposal record
    if body and body.title:
        proposal.title = body.title.strip()
        proposal.payload = {**proposal.payload, "suggested_name": body.title.strip()}
    if body and body.description is not None:
        proposal.payload = {**proposal.payload, "description": body.description.strip()}

    new_node = await _execute_proposal_action(proposal, kb, caller_id, db)

    proposal.status = "approved"
    proposal.reviewed_by = caller_id
    proposal.reviewed_at = datetime.now(tz=UTC)
    await db.commit()

    # Issue 2: invalidate cache
    _invalidate_coverage_cache(org.zitadel_org_id, kb_slug)

    # Issue 1 + 4: multi-centroid auto-categorise, conditional
    if auto_categorise and new_node is not None:
        centroids = proposal.payload.get("child_centroids")
        if centroids:
            # multi-centroid path
            for centroid in centroids:
                await enqueue_auto_categorise(
                    org_id=str(org.zitadel_org_id),
                    kb_slug=kb_slug,
                    node_id=new_node.id,
                    cluster_centroid=centroid,
                )
        elif proposal.payload.get("cluster_centroid"):
            # legacy single-centroid path
            await enqueue_auto_categorise(
                org_id=str(org.zitadel_org_id),
                kb_slug=kb_slug,
                node_id=new_node.id,
                cluster_centroid=proposal.payload["cluster_centroid"],
            )

    return _proposal_out(proposal)
```

## Risks

| Risk | Impact | Mitigatie |
|---|---|---|
| Storing N centroids per proposal blows up payload size | Slow GETs | AC-23: max ~45KB per proposal × 9 proposals = 400KB. Acceptable for review-list. Frontend doesn't need centroids — payload-projection in list-endpoint can strip them if needed. |
| Multi-centroid auto-categorise queue saturates Procrastinate | Bootstrap latency | N ≤ 11 per parent × ~7 parents = ~77 jobs max. Existing queue handles many more. Within budget. |
| Edit-and-approve persists overrides into payload but doesn't sync to other consumers | Frontend re-fetch shows old data | After approve, both proposal + node are updated. Coverage cache invalidated (Issue 2). Frontend invalidates `taxonomy-proposals` + `taxonomy-nodes` queries (already does this). |
| Unified list is too dense at 20+ pending proposals | Scroll fatigue | Operator can collapse rejected section. Pending count is bounded by `taxonomy_consolidate_target_max + 2 = 11`. |
| Operator runs migration script twice | Duplicate enqueue calls | `enqueue_auto_categorise` is idempotent at the assignment layer (set semantics — same node_id added twice is no-op). Acceptable. |

## Decision Points

### DP1: Where does the approve-override get persisted?

**Optie A**: Override only the new node (`PortalTaxonomyNode.name` / `description`)
- Proposal stays with original LLM-suggested name in `payload` and `title`
- Re-fetch shows historic LLM-suggestion + the operator-edited node separately

**Optie B**: Override BOTH the new node AND the proposal record
- After approve: both reflect operator's edit
- Re-fetch shows operator's edit consistently

**Aanbeveling**: B. Fewer surprises; UI re-fetch is consistent.

### DP2: Should rejected proposals be visible at all?

**Optie A**: Show recently-rejected for 1h, then hide. Undo affordance.
- Good for "oops, didn't mean to reject"

**Optie B**: Hide rejected immediately, but keep DB record.
- Cleaner UI

**Aanbeveling**: A for MVP; let operators recover from misclicks.

### DP3: What happens to `child_centroids` when operator edits a multi-cluster proposal?

**Optie A**: Edit only changes name/description; child_centroids untouched.
- Tagging behavior is preserved as the LLM-proposed grouping.

**Optie B**: Operator can also "remove a child" from the parent (drop one
of the child_centroids).
- Power-user feature, more complex UI.

**Aanbeveling**: A for MVP. Out-of-scope for now.

## Estimation

| Issue | Backend LoC | Frontend LoC | Tests LoC |
|---|---|---|---|
| #1 Tagging coverage (centroids) | 50 | 0 | 80 |
| #2 Cache invalidation | 15 | 0 | 30 |
| #3 Proposals visible after approve | 10 | 30 | 20 |
| #4 Defer auto-categorise during batch | 5 | 5 | 30 |
| #5 Edit before approve | 50 | 80 | 50 |
| #6 Unified review-list | 0 | 100 | 30 |
| Migration script | 80 | 0 | 0 |
| i18n keys | 0 | 10 | 0 |
| **Totaal** | **~210** | **~225** | **~240** |

**Grand total: ~675 LoC**. Eén PR, geen DB-migratie. Implementatie-tijd
geschat: 4-6 uur incl. lokaal testen + smoke-test op Voys.

## Deployment

1. **Code-deploy**. Bestaande feature-flag `TAXONOMY_CONSOLIDATE_ENABLED=True`
   blijft staan; geen wijziging nodig.
2. **Voys/support cleanup** (Mark — geen production users):
   - DELETE alle taxonomy nodes uit de KB
   - DELETE alle pending proposals uit de KB
   - Trigger nieuwe bootstrap → krijgt nu proposals met `child_centroids`
   - Approve via nieuwe edit-flow → multi-centroid auto-categorise tagt chunks
3. **Verifieer**: coverage `untagged_percentage` < 30% na "Apply to knowledge base"

Voor toekomstige tenants met production state: `retag_consolidated_nodes.py` script
draaien om al-approved nodes te updaten zonder data-verlies.

## References

- [klai-knowledge-ingest/knowledge_ingest/proposal_generator.py:_consolidate_to_parents](klai-knowledge-ingest/knowledge_ingest/proposal_generator.py)
  — locatie waar `child_centroids` opgebouwd moet worden
- [klai-knowledge-ingest/knowledge_ingest/clustering.py:72-99](klai-knowledge-ingest/knowledge_ingest/clustering.py#L72-L99)
  — `classify_by_centroid` — threshold 0.82 + waarom diffuse parent-centroids missen
- [klai-portal/backend/app/api/taxonomy.py:885-925](klai-portal/backend/app/api/taxonomy.py#L885-L925)
  — `_make_coverage_response` + `_coverage_cache` (de cache die nooit invalideert)
- [klai-portal/backend/app/api/taxonomy.py:260-318](klai-portal/backend/app/api/taxonomy.py#L260-L318)
  — `update_taxonomy_node` — moet `_invalidate_coverage_cache` aanroepen
- [klai-portal/backend/app/api/taxonomy.py:475-485](klai-portal/backend/app/api/taxonomy.py#L475-L485)
  — `approve_proposal` — uitbreiden met body + auto_categorise param + multi-centroid
- [klai-portal/frontend/src/routes/app/knowledge/$kbSlug/taxonomy.tsx:400-405](klai-portal/frontend/src/routes/app/knowledge/$kbSlug/taxonomy.tsx#L400-L405)
  — `proposalsQuery` — drop `?status=pending` filter
- [klai-portal/frontend/src/routes/app/knowledge/$kbSlug/taxonomy.tsx:578-596](klai-portal/frontend/src/routes/app/knowledge/$kbSlug/taxonomy.tsx#L578-L596)
  — `handleApplyAll` — pass `auto_categorise=false`
- [klai-portal/frontend/src/routes/app/knowledge/$kbSlug/taxonomy.tsx:30-240](klai-portal/frontend/src/routes/app/knowledge/$kbSlug/taxonomy.tsx#L30-L240)
  — bestaande `CoverageView` met inline-edit pattern (te hergebruiken voor proposals)
- [.claude/rules/klai/projects/knowledge.md::Embedding pipeline](.claude/rules/klai/projects/knowledge.md)
  — chunking + embedding context (1500 chars, 200 overlap, bge-m3 1024-dim)
- SPEC-TAXONOMY-MERGE-DETECT-001 — parent SPEC; deze SPEC fixt de gaten die
  daar live in productie zijn ontdekt

## History

- **v0.1.0** (2026-05-07): initial draft after live-test feedback on Voys/support.
  Bundles 6 issues found in production walk-through into single SPEC for
  one-PR delivery.
