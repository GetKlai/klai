# SPEC-INFRA-TENANT-DELETE-001 Progress

- **Started**: 2026-05-03
- **Branch**: feature/SPEC-INFRA-TENANT-DELETE-001 (forked from main)
- **Mode**: Solo (sub-agent), Camp 1 light (async + idempotent steps + retry)

## Phase Status

| Phase | Description | Status | Reference |
|-------|-------------|--------|-----------|
| 1 | State machine + Alembic migration + tenant_lifecycle_events model + 10 unit tests | ✅ DONE | commit abcb7506 |
| 2 | tenant_lifecycle audit helper + tests | ✅ DONE | commit (portal-api) |
| 3 | 16 deprovisioning steps with idempotent + retry pattern | ✅ DONE | commit (portal-api) |
| 4 | deprovisioning orchestrator + integration tests | ✅ DONE | commit d9e6de2d |
| 5 | Zitadel client extension (delete_org) | ✅ DONE | commit (portal-api) |
| 6 | Moneybird client (stop_subscription + archive_contact) | ✅ DONE | commit (portal-api) |
| 7 | knowledge-ingest wipe-graph endpoint | ✅ DONE | commit e808edd5 |
| 8 | admin/_get_caller_org 403-branch on deprovisioning state | ✅ DONE | commit 2e6d5b70 |
| 9 | API endpoints (owner + admin + retry + status) | ✅ DONE | commit 471da922 |
| 10 | Frontend DeleteOrgModal + Danger Zone page | pending | |
| 11 | Frontend status polling + tenant-deleted + 403-handler | pending | |
| 12 | e2e test against dev-stack (handled by /klai:auto Phase 5) | deferred | |
| 13 | docs + runbook | pending | |

## Phase 2 — Audit helper (portal-api)

**Files added:**
- `klai-portal/backend/app/services/audit/__init__.py`
- `klai-portal/backend/app/services/audit/tenant_lifecycle.py` — `emit_lifecycle_event()`
- `klai-portal/backend/tests/test_tenant_lifecycle_audit.py` — 11 tests

**Key design decisions:**
- Synchronous INSERT within caller's transaction (NOT fire-and-forget).
- Uses raw `text()` SQL + `CAST(:props AS jsonb)` per portal-backend.md.
- Validates `event_type` and `actor_type` with frozensets before DB hit.
- Fail-loud on DB error causes the enclosing transaction to roll back (R6 compliance).

## Phase 5 — Zitadel delete_org (portal-api)

**Files modified:**
- `klai-portal/backend/app/services/zitadel.py` — added `delete_org(org_id: str) -> None`

**Files added:**
- `klai-portal/backend/tests/test_zitadel_delete_org.py` — 5 tests

**Key design decisions:**
- `DELETE /management/v1/orgs` with `x-zitadel-orgid` header.
- 404 = idempotent (org already absent).
- All other non-2xx propagate via `raise_for_status()`.

## Phase 6 — Moneybird client (portal-api)

**Files added:**
- `klai-portal/backend/app/services/moneybird_client.py` — `MoneybirdClient`
- `klai-portal/backend/tests/test_moneybird_client.py` — 14 tests

**Key design decisions:**
- Fail-closed construction: raises `ValueError` at `__init__` if `moneybird_admin_id` or `moneybird_api_token` is empty/whitespace.
- Base URL derived from existing `settings.moneybird_admin_id` (no new settings fields needed).
- Factory function `get_moneybird_client()` instead of module-level singleton (avoids startup failure in dev/test).
- `stop_subscription`: PATCH with `{"recurring_sales_invoice": {"frequency_type": "stopped"}}`.
- `archive_contact`: PATCH with `{"contact": {"archived": True}}`.
- Both 404-idempotent. Non-404 errors propagate via `raise_for_status()`.

## Phase 7 — knowledge-ingest wipe-graph endpoint

**Files added:**
- `klai-knowledge-ingest/knowledge_ingest/routes/internal.py` — `POST /internal/v1/orgs/{org_id}/wipe-graph`
- `klai-knowledge-ingest/tests/test_wipe_graph.py` — 11 tests (5 unit + 6 endpoint)

**Files modified:**
- `klai-knowledge-ingest/knowledge_ingest/graph.py` — added `wipe_org_graph(org_id: str) -> int`
- `klai-knowledge-ingest/knowledge_ingest/app.py` — registered `internal.router`

**Key design decisions:**
- `wipe_org_graph` is synchronous (uses direct `falkordb` Python client, same pattern as `sweep_orphan_episodes_org_wide`).
- Cypher: `MATCH (n) WHERE n.group_id = $org_id … DETACH DELETE n RETURN count(nid) AS deleted`.
- No-op when `settings.graphiti_enabled = False` or FalkorDB unavailable (ImportError).
- Endpoint uses `asyncio.to_thread()` to call the sync function without blocking the event loop.
- Auth handled entirely by existing `InternalSecretMiddleware` — no per-route guard needed.
- Idempotent: successive calls return `{"nodes_deleted": 0, "status": "ok"}` after first wipe.

## Phase 3 — 17 deprovisioning steps (portal-api)

**Files added:**
- `klai-portal/backend/app/services/provisioning/deprovisioning_steps.py` — `STEPS` list + 17 idempotent async step functions (steps 0–16)
- `klai-portal/backend/tests/test_deprovisioning_steps.py` — 34 tests, one class per step

**Files modified:**
- `klai-portal/backend/app/core/config.py` — added `garage_s3_endpoint`, `garage_s3_access_key`, `garage_s3_secret_key`, `garage_s3_bucket`, `platform_org_slug`
- `klai-portal/backend/pyproject.toml` — added `boto3>=1.35,<2.0` and `qdrant-client>=1.12,<2.0`

**Key design decisions:**
- All steps use `@MX:NOTE: idempotent — al-weg = geen exception. SPEC R3.`
- Step 0 `from_state` includes `"deprovisioning"` itself (for retry idempotency).
- S3 step guarded by empty `garage_s3_endpoint` feature flag.
- Lazy imports inside step functions for Moneybird, Zitadel, docs-app, audit to avoid circular imports.

## Phase 4 — Deprovisioning orchestrator (portal-api)

**Files added:**
- `klai-portal/backend/app/services/provisioning/deprovisioning_orchestrator.py`
- `klai-portal/backend/tests/test_deprovisioning_orchestrator.py` — 9 test classes

**Key design decisions:**
- `litellm_team_id` resolved at deprovision-time via `GET /team/list?team_alias={slug}` (not stored in portal_orgs).
- `zitadel_oidc_app_id` resolved via POST to Zitadel app search (only `zitadel_librechat_client_id` stored).
- Retry policy: delays=[1s, 2s, 4s], retryable vs non-retryable exception distinction.
- `_mark_failed` transitions to `failed_deprovisioning` + populates `last_failure` JSONB.

## Phase 8 — _get_caller_org 403 guard (portal-api)

**Files modified:**
- `klai-portal/backend/app/api/admin/__init__.py` — `allow_during_deprovisioning: bool = False` kwarg added; raises 403 `tenant_deleting` when `provisioning_status == 'deprovisioning'`

**Files added:**
- `klai-portal/backend/tests/test_auth_deprovisioning_block.py` — 9 tests

**Key design decisions:**
- `set_tenant` is NOT called when the 403 guard fires.
- Only the status-polling endpoint uses `allow_during_deprovisioning=True`.

## Phase 9 — Deprovision API endpoints (portal-api)

**Files added:**
- `klai-portal/backend/app/api/admin/deprovision_org.py` — 4 endpoints + 3 helper functions
- `klai-portal/backend/tests/test_deprovision_endpoints.py` — 25 tests

**Files modified:**
- `klai-portal/backend/app/api/admin/__init__.py` — `deprovision_org_router` included

**Key design decisions:**
- `SELECT FOR UPDATE` on target org row before state mutation (concurrency guard).
- `_guard_entry_state` blocks `already_deprovisioning` + non-entry states with 409.
- `_require_platform_admin` checks `caller_org.slug == settings.platform_org_slug`.
- `retry-deprovisioning` resets `last_failure = NULL` before re-queuing.
- Status endpoint uses `allow_during_deprovisioning=True` (only exception to Phase 8 guard).

## Implementation Notes

- **Camp 1 light fail-strategy**: each step idempotent + 3 internal retries; all critical (no fail-soft). On final failure → status `failed_deprovisioning` + `last_failure` jsonb populated.
- **Hard-delete on portal_orgs** as final step; audit emit BEFORE the delete in same transaction.
- **Two endpoints sharing one orchestrator**: `DELETE /api/admin/org/me` (owner) + `DELETE /api/admin/orgs/{slug}/deprovision` (platform-admin).
- **Zitadel cascade confirmed**: `DELETE /management/v1/orgs` cascades users + grants. No per-user step needed.
- **Moneybird** (NOT Stripe): use `moneybird_subscription_id` from portal_orgs. API call exact form to be confirmed during step implementation.
- **Graphiti wipe**: call `klai-knowledge-ingest::POST /internal/v1/orgs/{org_id}/wipe-graph` (Phase 7 endpoint) from the deprovisioning orchestrator step.
- **Frontend confirm pattern**: copy `delete-kb-modal.tsx` (type-slug-to-confirm).
