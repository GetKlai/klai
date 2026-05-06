# SPEC-TI-010 — Cleanup batch: cross-org markers + Redis hygiëne + scribe org_id + B-2/B-6/B-7/B-8/C-11

**Audit ref:** findings **B-2, B-5, B-6, B-7, B-8, B-9, B-10, A-9, C-3, C-4, C-5, C-6, C-7, C-8, C-11**
**Standards ref:** `standards.md` sections 4, 7, 9, 10, 11, 14
**Priority:** MED+LOW batch (15 findings, parallel implementeerbaar)
**Status:** Ready

## Goal

Eén SPEC die de overgebleven 15 MED+LOW findings clustert in 3 sub-werkpakketten. Elk sub-pakket = eigen agent in eigen worktree, parallel.

## Sub-pakket A: Cross-org markers + scribe org_id (5 findings)

### Findings: A-9, C-3, C-4, C-5, C-6, C-7, C-8

- **AC-A1 (A-9)** ALTER TABLE `scribe.transcriptions` ADD COLUMN `org_id varchar(255) NOT NULL DEFAULT ''`. Cat-D RLS policy. Update endpoints filteren op `(user_id, org_id)`. Use JWT resourceowner als bron.
- **AC-A2 (C-3)** `klai-portal/backend/app/services/invite_scheduler.py:_join_meeting`: split cross-org SELECT en per-tenant INSERT in twee sessions.
- **AC-A3 (C-4)** `klai-portal/backend/app/main.py:_run_stuck_detector`: wrap in `cross_org_session()` + `# cross-org-by-design:` comment.
- **AC-A4 (C-5)** `klai-portal/backend/app/api/internal_connectors.py`: voeg `# cross-org-by-design:` comment toe + lookup connector eerst, dan `set_tenant(db, connector.org_id)`.
- **AC-A5 (C-6)** `klai-connector/app/main.py` lifespan: `# cross-org-by-design:` comment + (toekomst) replace met `cross_org_session()` na SPEC-TI-002 land.
- **AC-A6 (C-7)** `klai-connector/app/services/sync_run_reaper.py::tick()`: idem.
- **AC-A7 (C-8)** `klai-scribe/scribe-api/app/services/reaper.py`: `# cross-user-by-design:` comment.

**Worktree:** `klai-cleanup-markers-scribe`.

## Sub-pakket B: Redis hygiëne + B-2 (4 findings)

### Findings: B-2, B-5, B-9, B-10

- **AC-B1 (B-2)** `klai-portal/backend/app/api/app_knowledge_bases.py:1228`: van `str(org.id)` naar `org.zitadel_org_id` op `preview_crawl` call.
- **AC-B2 (B-5)** `klai-portal/backend/app/services/litellm_cache.py:31-36` + `app/api/app_account.py:33-53,203`: parameter type van `int` naar `zitadel_org_id: str`. Update 4 call-sites. Add roundtrip test.
- **AC-B3 (B-9)** `klai-portal/backend/app/api/internal.py:846`: feedback idempotency-key `fb:{message_id}:{conversation_id}` → `fb:{org.id}:{conversation_id}:{message_id}`.
- **AC-B4 (B-10)** `klai-portal/backend/app/services/provisioning/deprovisioning_steps.py::_flush_redis_tenant_keys`: extend met `templates:{zitadel_org_id}:*`, `kb_ver:`, `kb_feature:`, `connector_rl:read:`, `connector_rl:write:`, `rl:`, `templates_rl:`. Pass `state.zitadel_org_id` mee.

**Worktree:** `klai-cleanup-redis`.

## Sub-pakket C: Identity-assertion uitbreiding + remainders (4 findings)

### Findings: B-6, B-7, B-8, C-11

- **AC-C1 (B-6)** `klai-portal/backend/app/api/internal.py::feature_knowledge`: vervang query-param `org_id` door Mongo-driven lookup OF nieuwe cross-tenant tabel `portal_users_librechat_index(librechat_object_id PK, org_id, zitadel_user_id)`. Laatste optie de aanbevolen route.
- **AC-C2 (B-7)** `klai-focus/research-api/app/services/qdrant_store.py::delete_by_source/notebook` + `scripts/backfill_notebook_visibility.py`: voeg `tenant_id: str` param toe en filter expliciet.
- **AC-C3 (B-8)** `klai-knowledge-ingest/knowledge_ingest/routes/stats.py`: identity-assertion adoption — zelfde pattern als SPEC-TI-003 maar specifiek voor stats endpoints.
- **AC-C4 (C-11)** `klai-portal/backend/app/api/admin/join_requests.py` token-approve path: per-IP rate-limit (10/hour) + WARNING log op failed verifies.

**Worktree:** `klai-cleanup-identity-misc`.

## Tests

Per sub-pakket eigen test-files met regression-coverage. Geen overlap.

## Operator-step

Sub-A heeft scribe-migration → operator post-deploy SQL.
Sub-B en Sub-C: geen migratie.

## Worktrees

3 parallelle worktrees, 3 PRs (één per sub-pakket).
