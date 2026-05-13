---
id: SPEC-INFRA-TENANT-DELETE-002
version: "0.1.0"
status: draft
created: 2026-05-05
updated: 2026-05-05
author: Mark Vletter
priority: high
related:
  - SPEC-INFRA-TENANT-DELETE-001 (parent, base 17-step orchestrator)
  - SPEC-CODEBASE-AUDIT-001 (parent audit, Cluster F)
---

# SPEC-INFRA-TENANT-DELETE-002: 7 GDPR purge gaps (G1-G7)

## Summary

Sluit 7 PII-purge gaps gevonden in audit Cluster F. Tenant-deprovisioning achterhaalt zeven stores die niet automatisch gepurged worden, plus één code-bug in step 8 (Qdrant filter-key mismatch).

## Motivation

Per `reports/audit-2026-05-04/multi-tenancy-gdpr.md`:

| ID | Severity | Store | Issue |
|---|---|---|---|
| G1 | HIGH | `portal_join_requests` | Niet in step 16 DELETE-list |
| G2 | HIGH | `portal_org_allowed_domains` | Niet in step 16 DELETE-list |
| G3 | HIGH | `knowledge.*` connector-KB content | Alleen personal-KB gepurged via klai-docs |
| G4 | HIGH | Qdrant `klai_focus` | step 8 filter-key bug (`org_id` vs `tenant_id`) |
| G5 | HIGH | `scribe.transcriptions` | Geen `org_id` kolom, geen wipe-stap |
| G6 | MED | klai-connector eigen schema | Sync-runs metadata blijft |
| G7 | HIGH | `research.*` schema + uploads | Manual cleanup-script, niet automatisch |

## Scope

### In scope

1. **G1+G2**: voeg `portal_join_requests` + `portal_org_allowed_domains` toe aan `_finalize_postgres_delete` DELETE-list (`klai-portal/backend/app/services/provisioning/deprovisioning_steps.py:530`); `@MX:WARN` block updaten
2. **G3**: nieuw endpoint `POST /internal/v1/orgs/{org_id}/wipe-postgres` op knowledge-ingest die `knowledge.{artifacts,embedding_queue,artifact_entities,derivations,kb_config,crawl_jobs,crawled_pages,page_links,crawl_domains,artifact_images}` purges WHERE org_id; aangeroepen door step 13a in deprovisioning_orchestrator
3. **G4 (CODE BUG FIX)**: in `deprovisioning_steps.py` step 8 — twee aparte deletes per Qdrant collection: `klai_knowledge` met `org_id` filter, `klai_focus` met `tenant_id` filter (matcht respectieve payload schemas)
4. **G5**: schemamigratie scribe — `ALTER TABLE scribe.transcriptions ADD COLUMN org_id VARCHAR(64) NOT NULL DEFAULT ''` + backfill via portal-api lookup; nieuwe `_delete_scribe_transcriptions` step ZONDER S3-blob delete (al gedekt door step 10)
5. **G6**: nieuw endpoint `POST /internal/v1/orgs/{org_id}/wipe-connector-state` op klai-connector die `connector.sync_runs` purges; aangeroepen door step 8a (na portal_connectors cascade-delete maar voor schema-orphan-cleanup)
6. **G7**: automatische call van `scripts/research_tenant_cleanup.py` als step 13b OF nieuwe `POST /internal/v1/orgs/{org_id}/wipe-research` endpoint op research-api

### Out of scope

- Moneybird soft-archive vs hard-delete (bewuste retentie NL fiscale 7 jaar)
- LibreChat per-tenant Mongo (al gepurged in step 4)

## Acceptance criteria

1. Per nieuw endpoint: dedicated test met seeded tenant-data, verify post-call rij-count = 0
2. Integration test: maak test-tenant met data in alle 7 stores, run deprovisioning, verifieer ALLE stores leeg
3. Regression-test in `tests/test_deprovisioning_steps.py` voor de DELETE-list aanvullingen + Qdrant filter-key fix
4. Audit-log: `tenant_lifecycle_events` toont nieuwe step-namen
5. Per pitfall `validator-env-parity`: geen nieuwe env-vars vereist — alleen nieuwe HTTP endpoints

## Risk

| Risk | Mitigatie |
|---|---|
| Schema-migration scribe op grote tabel | `ADD COLUMN ... DEFAULT '' NOT NULL` is niet-blocking in pg18; backfill via batch-script post-migrate |
| Cross-service endpoint-toegang faalt | Same X-Internal-Secret pattern als bestaande wipe-graph endpoint |
| G3 wipe-postgres parallel met running enrichment-tasks | Procrastinate task-cancel call voor wipe; documentatie in runbook |

## References

- `reports/audit-2026-05-04/multi-tenancy-gdpr.md` (G1-G7 detail)
- `klai-portal/backend/app/services/provisioning/deprovisioning_steps.py`
- `klai-knowledge-ingest/knowledge_ingest/routes/internal.py::wipe_org_graph` — canonical pattern
- `scripts/research_tenant_cleanup.py` — manual cleanup origineel
