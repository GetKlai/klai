# SPEC-KB-027 — Implementation Plan

## Task decomposition

### Stap 1: R2 — Verwijder dead code + fix backfill proposals

**Files:**
- `klai-knowledge-ingest/knowledge_ingest/routes/ingest.py`
- `klai-knowledge-ingest/knowledge_ingest/taxonomy_tasks.py`
- `klai-knowledge-ingest/tests/test_taxonomy_classifier.py` (of nieuw test file)

**Wijzigingen:**
1. Verwijder het `asyncio.create_task(maybe_generate_proposal(...))` blok in `ingest.py`
2. Verwijder de `_background_tasks` set en `maybe_generate_proposal` import uit `ingest.py` (als niet elders gebruikt — controleer)
3. In `taxonomy_tasks.py` `_run_backfill()` Phase 2: voeg `unmatched_summaries: list[DocumentSummary] = []` toe, vul bij elke batch waar `node_ids = []`
4. Aan het einde van Phase 2: `await maybe_generate_proposal(org_id, kb_slug, unmatched_summaries, taxonomy_nodes)`
5. Update return dict om `proposals_submitted: int` toe te voegen

**Testbaar via:**
- Unit test: `_run_backfill` met 5 unmatched docs → `maybe_generate_proposal` gecalled met 5 docs
- Unit test: `_run_backfill` met 0 unmatched docs → `maybe_generate_proposal` niet gecalled

---

### Stap 2: R3 — doc_count verwijdering

**Files:**
- `klai-portal/backend/app/models/taxonomy.py`
- `klai-portal/backend/app/api/taxonomy.py`
- `klai-portal/backend/alembic/versions/<new_migration>.py`
- Frontend: zoek naar `doc_count` in `klai-portal/frontend/src/`

**Wijzigingen:**
1. Verwijder `doc_count` uit `PortalTaxonomyNode` SQLAlchemy model
2. Verwijder `doc_count` uit `TaxonomyNodeOut` Pydantic schema
3. Verwijder alle `doc_count` mutaties in `delete_taxonomy_node()` en `_execute_merge()`
4. Nieuwe Alembic migratie: `op.drop_column("portal_taxonomy_nodes", "doc_count")`
5. Frontend: vervang `node.doc_count` door coverage-stats data of verberg het veld

**Volgorde:** model → API → migratie → frontend

---

### Stap 3: R1 — Query classificatie in research-api

**Files:**
- `klai-focus/research-api/app/services/retrieval_client.py`
- `klai-focus/research-api/app/services/retrieval.py`
- `klai-focus/research-api/app/core/config.py`

**Wijzigingen:**
1. Voeg `taxonomy_retrieval_min_coverage: float = 0.3` en `knowledge_ingest_url: str` toe aan config
2. Maak nieuwe functie `get_taxonomy_filter(query: str, kb_slug: str, org_id: str) -> list[int] | None`:
   - Parallel: `classify_query(...)` + `get_coverage(...)` via `asyncio.gather()`
   - Coverage check: `(total - untagged) / total >= min_coverage`
   - Retourneert `None` bij failure of lage coverage
3. In `retrieval_client.py` `retrieve()`: roep `get_taxonomy_filter()` aan, voeg taxonomy_node_ids toe aan request body

**Error handling:**
- `asyncio.wait_for(..., timeout=3.0)` per call
- Alle exceptions → log warning, return `None` (retrieval gaat door zonder filter)

**Testbaar via:**
- Unit test `get_taxonomy_filter`: mock classify + coverage endpoints
- Integration test: retrieval met taxonomy filter vs zonder → andere chunk subset

---

## Risico's

| Risico | Kans | Impact | Mitigatie |
|---|---|---|---|
| Frontend toont lege `doc_count` kolom na R3 | Hoog | Laag (visueel) | Frontend check vóór merge |
| Research-api heeft geen directe toegang tot knowledge-ingest | Laag | Hoog | Controleer docker-compose networking |
| Taxonomy filter sluit te veel chunks uit (coverage check te laat) | Gemiddeld | Gemiddeld | Default 30% is conservatief; logging bij gefilterde retrieval |
| Backfill proposal vuurt dubbel als backfill meerdere keren loopt | Laag | Laag | Portal dedupliceert proposals per naam (24h window) |

## Dependencies

- SPEC-KB-026 R4: `/ingest/v1/taxonomy/classify` endpoint — **aanwezig** (geïmplementeerd)
- SPEC-KB-026 R5: auto-categorise via Procrastinate — **aanwezig**
- `klai-retrieval-api` `taxonomy_node_ids` filter — **aanwezig** (search.py regel 182–190)
