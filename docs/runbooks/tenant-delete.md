# Tenant Deprovisioning — Owner & Admin Workflows

> Covers: SPEC-INFRA-TENANT-DELETE-001 — happy-path tenant delete, 16-step orchestrator, manual fallback, GDPR compliance.
> Grafana dashboard: https://grafana.getklai.com/d/klai-deprovisioning (if available)

## Overview

Tenant deprovisioning is a one-way permanent delete of a workspace. It removes the workspace from all 11 external systems (Caddy routing, LibreChat container, MongoDB, Meilisearch, Redis, Qdrant graph, FalkorDB, Scribe S3, LiteLLM team, Zitadel, Moneybird) plus the workspace database row in portal_orgs. The audit trail (`tenant_lifecycle_events`) survives the hard delete by design for GDPR audit purposes.

Expected duration: **~30 seconds** for the full 16-step orchestrator running to completion.

## Happy path: Owner self-service

An owner (workspace admin) initiates deletion via the danger zone page.

### Owner workflow

1. **Open danger zone**: Navigate to `/admin/danger-zone` (owner-only page).
2. **Read the warning**: Workspace name, member count, knowledge base count, all data destroyed, all API keys invalidated.
3. **Confirm deletion**: Type the workspace slug exactly (deliberate action, no autocomplete).
4. **Submit**: Click "Permanent verwijderen" (NL) or "Permanently delete" (EN).
5. **Status page**: Auto-redirect to `/admin/deprovisioning-status` with a spinner.
6. **Poll until done**: Page polls `GET /api/admin/org/me/deprovision-status` every 2 seconds.
   - `status: deprovisioning` → spinner continues.
   - HTTP 404 → orchestrator succeeded, workspace gone, page redirects to `/tenant-deleted`.
   - `status: failed_deprovisioning` with `last_failure` → error message, contact support.

### Server-side events

When owner clicks "Delete":

1. `DELETE /api/admin/org/me` endpoint is called.
2. **202 Accepted** response (not 200 OK) — orchestrator queued, request returns immediately.
3. Server-side:
   - `portal_orgs` row transitions to `deprovisioning` (all future requests for this workspace get 403 `tenant_deleting`).
   - `invalidate_tenant_slug_cache()` purges the slug from Redis.
   - 16-step orchestrator begins in background (via `BackgroundTasks`).

All team members of the workspace immediately see a 403 with message "This workspace is being deleted by the owner."

## Happy path: Platform admin

Support staff or fraud investigators can delete a workspace on behalf of an owner.

### When to use

- Owner requests manual deletion (support ticket).
- Fraud investigation: malicious tenant, abuse, or data breach.
- E2E testing cleanup: CI/CD pipeline deprovisioning orphan test tenants.

### Admin workflow

1. **Get the workspace slug**: From the organization name, email, or support ticket.
2. **Make the API call**:

```bash
curl -X DELETE "https://my.getklai.com/api/admin/orgs/{slug}/deprovision" \
  -H "Authorization: Bearer $PLATFORM_ADMIN_TOKEN" \
  -H "Content-Type: application/json"
```

**Expected response** (202 Accepted):
```json
{
  "status": "queued",
  "org_slug": "{slug}"
}
```

3. **Verify in logs**: Follow the orchestrator in VictoriaLogs (see § Monitoring).

### Response codes

| HTTP | `error` field | When |
|---|---|---|
| 202 | — | Deprovision queued. |
| 403 | — | Caller is not a platform-admin (not in the platform org). |
| 404 | — | Organization slug not found. |
| 409 | `already_deprovisioning` | Workspace is already being deleted (by owner or another admin). |

## Monitoring during deprovision

Check VictoriaLogs for the orchestrator's progress:

```
service:portal-api AND deprovision AND org_slug:{slug}
```

This query returns one structured log line per step. Expected pattern:

```
deprovisioning_started        org_id={id}, slug={slug}, actor_type=owner|platform_admin
deprovisioning_step_start     step=_mark_deprovisioning, slug={slug}
deprovisioning_step_start     step=_delete_caddy_upstream, slug={slug}
deprovisioning_step_start     step=_delete_librechat_container, slug={slug}
... (15 more steps) ...
deprovisioning_complete       org_id={id}, slug={slug}
```

If you see `deprovisioning_failed`:
```
deprovisioning_failed         step=<name>, error=<truncated>, org_id={id}, slug={slug}
```

The workspace has transitioned to `failed_deprovisioning`. See § Failure recovery.

### Per-step events (reference)

| # | Step | Resource | Expected log event |
|---|---|---|---|
| 0 | `_mark_deprovisioning` | Postgres + cache | `deprovisioning_step_start` |
| 1 | `_delete_caddy_upstream` | Caddy | `deprovisioning_step_start` |
| 2 | `_delete_librechat_container` | Docker | `deprovisioning_step_start` |
| 3 | `_delete_librechat_filesystem` | Filesystem | `deprovisioning_step_start` |
| 4 | `_drop_mongodb_database` | MongoDB | `deprovisioning_step_start` |
| 5 | `_drop_mongodb_user` | MongoDB | `deprovisioning_step_start` |
| 6 | `_delete_meilisearch_index` | Meilisearch | `deprovisioning_step_start` |
| 7 | `_flush_redis_tenant_keys` | Redis | `deprovisioning_step_start` |
| 8 | `_delete_qdrant_points` | Qdrant | `deprovisioning_step_start` |
| 9 | `_delete_falkordb_graph` | FalkorDB | `deprovisioning_step_start` |
| 10 | `_delete_scribe_artifacts` | Garage S3 | `deprovisioning_step_start` |
| 11 | `_delete_litellm_team` | LiteLLM | `deprovisioning_step_start` |
| 12 | `_archive_moneybird_subscription` | Moneybird | `deprovisioning_step_start` |
| 13 | `_delete_personal_kb` | docs-app | `deprovisioning_step_start` |
| 14 | `_delete_zitadel_oidc_app` | Zitadel | `deprovisioning_step_start` |
| 15 | `_delete_zitadel_org` | Zitadel | `deprovisioning_step_start` |
| 16 | `_finalize_postgres_delete` | Postgres (audit + hard-delete) | `deprovisioning_step_start` |

Each step is idempotent. Internal retry: 3 attempts with exponential backoff (1s, 2s, 4s) on transient errors.

## Failure recovery: `failed_deprovisioning`

When a step fails after 3 internal retries, the workspace transitions to `failed_deprovisioning`. The `last_failure` JSONB field contains:

```json
{
  "step": "_delete_caddy_upstream",
  "error": "connection refused",
  "attempt": 3,
  "failed_at": "2026-05-03T12:34:56Z"
}
```

### Admin retry endpoint

Retry the deprovisioning with this API call:

```bash
curl -X POST "https://my.getklai.com/api/admin/orgs/{slug}/retry-deprovisioning" \
  -H "Authorization: Bearer $PLATFORM_ADMIN_TOKEN" \
  -H "Content-Type: application/json"
```

**Expected response** (202 Accepted):
```json
{
  "status": "queued"
}
```

The orchestrator re-runs from the beginning. Each step is idempotent, so already-deleted resources are skipped (files already gone, MongoDB user already dropped, etc.). The failed step is attempted again.

### When to retry vs investigate manually

| Scenario | Action |
|---|---|
| **Transient error** (connection timeout, service temporarily down) | Retry immediately via the endpoint above. |
| **Last step was `_archive_moneybird_subscription` (Moneybird API down)** | Wait 5 minutes and retry (Moneybird may have recovered). |
| **Last step was `_delete_zitadel_oidc_app` or `_delete_zitadel_org` (Zitadel API down)** | Wait 5 minutes and retry. |
| **Last step was `_delete_caddy_upstream` (file permission, Caddy reload failed)** | Check § Manual cleanup first. Caddy may be in an inconsistent state. |
| **Last step was `_delete_librechat_container` (Docker API error)** | Check § Manual cleanup first. Container may be partially removed. |
| **Multiple retries still failing (all 3 failed)** | Check § Manual cleanup and see which step is blocking. Remove it manually, then retry. |

## Manual cleanup if endpoints fail

Last resort: SSH to `core-01` and remove resources by hand. Use these commands **only** if automatic retry has failed multiple times.

### Caddy

Workspace-specific routing config and reload:

```bash
ssh core-01 "rm /opt/klai/caddy/tenants/{slug}.caddyfile && docker restart klai-core-caddy-1"
```

Verify Caddy is responsive:
```bash
ssh core-01 "docker logs --tail 20 klai-core-caddy-1 | grep -i 'error\|reload'"
```

### LibreChat Docker container

```bash
ssh core-01 "docker rm -f librechat-{slug}"
```

Verify it's gone:
```bash
ssh core-01 "docker ps -a | grep librechat-{slug}"
```

Should return nothing (no output).

### LibreChat filesystem

```bash
ssh core-01 "rm -rf /opt/klai/librechat/{slug}/"
```

### MongoDB database + user

Drop the entire tenant database and the associated user:

```bash
ssh core-01 'docker exec klai-core-mongo-1 mongosh \
  -u $MONGO_ROOT_USERNAME -p "$MONGO_ROOT_PASSWORD" \
  --authenticationDatabase admin \
  --eval "
    db.getSiblingDB(\"librechat-{slug}\").dropDatabase();
    db.getSiblingDB(\"librechat-{slug}\").dropUser(\"librechat-{slug}\");
  "'
```

Verify the database is gone:
```bash
ssh core-01 'docker exec klai-core-mongo-1 mongosh \
  -u $MONGO_ROOT_USERNAME -p "$MONGO_ROOT_PASSWORD" \
  --authenticationDatabase admin \
  --eval "db.adminCommand(\"listDatabases\") | select(.databases[] | select(.name | contains(\"librechat-{slug}\")))"'
```

Should return empty array.

### Meilisearch index

```bash
curl -X DELETE "http://core-01:7700/indexes/{slug}" \
  -H "Authorization: Bearer $MEILISEARCH_MASTER_KEY"
```

Verify it's gone:
```bash
curl -H "Authorization: Bearer $MEILISEARCH_MASTER_KEY" "http://core-01:7700/indexes" | jq '.results[] | select(.uid == "{slug}")'
```

Should return nothing.

### Redis tenant keys

Scan and delete all keys matching the tenant pattern:

```bash
ssh core-01 "docker exec klai-core-redis-1 redis-cli --scan --pattern 'configs:{slug}:*' | \
  xargs -I {} docker exec klai-core-redis-1 redis-cli UNLINK {}"
```

Verify:
```bash
ssh core-01 "docker exec klai-core-redis-1 redis-cli --scan --pattern 'configs:{slug}:*' | wc -l"
```

Should return 0.

### Qdrant vector points

Delete all points with `org_id` filter from both `klai_knowledge` and `klai_focus` collections:

```bash
curl -X POST "http://core-01:6333/collections/klai_knowledge/points/delete" \
  -H "Content-Type: application/json" \
  -d '{
    "filter": {
      "must": [
        {
          "key": "org_id",
          "match": {
            "value": {org_id}
          }
        }
      ]
    }
  }'
```

Repeat for `klai_focus` collection (replace `klai_knowledge` with `klai_focus`).

Verify points were deleted:
```bash
curl "http://core-01:6333/collections/klai_knowledge/points/search" \
  -H "Content-Type: application/json" \
  -d '{
    "vector": [0.1, 0.2],
    "limit": 1,
    "filter": {
      "must": [
        {
          "key": "org_id",
          "match": {
            "value": {org_id}
          }
        }
      ]
    }
  }' | jq '.result | length'
```

Should return 0.

### FalkorDB graph

Wipe all nodes and edges with `group_id = org_id`:

```bash
curl -X POST "http://core-01:8001/internal/v1/orgs/{org_id}/wipe-graph" \
  -H "X-Internal-Secret: $KNOWLEDGE_INGEST_SECRET" \
  -H "Content-Type: application/json"
```

Expected response:
```json
{
  "status": "ok",
  "nodes_deleted": 42
}
```

### Scribe S3 artifacts

Delete all objects under the tenant prefix:

```bash
aws s3 rm "s3://klai-scribe/{slug}/" --recursive \
  --endpoint-url "http://core-01:9000" \
  --region us-east-1
```

Or with minio client (if aws cli not available):

```bash
mc rm --recursive minio-klai/klai-scribe/{slug}/
```

Verify the prefix is empty:
```bash
aws s3 ls "s3://klai-scribe/{slug}/" \
  --endpoint-url "http://core-01:9000" \
  --region us-east-1
```

Should list nothing.

### LiteLLM team

Delete the team (identified by `litellm_team_id` or resolved via team alias = workspace slug):

```bash
curl -X POST "http://core-01:4000/team/delete" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "team_ids": ["{litellm_team_id}"]
  }'
```

If the team ID is unknown, list teams first:
```bash
curl "http://core-01:4000/team/list?team_alias={slug}" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY"
```

### Moneybird subscription

No API delete — archive the contact and stop the subscription via the web UI or API:

```bash
curl -X PATCH "https://api.moneybird.com/api/v2/{admin_id}/contacts/{contact_id}" \
  -H "Authorization: Bearer $MONEYBIRD_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "contact": {
      "archived": true
    }
  }'
```

And stop the subscription:
```bash
curl -X PATCH "https://api.moneybird.com/api/v2/{admin_id}/recurring_sales_invoices/{subscription_id}" \
  -H "Authorization: Bearer $MONEYBIRD_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "recurring_sales_invoice": {
      "frequency_type": "stopped"
    }
  }'
```

(These IDs are typically stored in the workspace record or Moneybird customer ID / subscription ID fields.)

### Zitadel OIDC app

Delete the LibreChat OIDC app via Zitadel Management API:

```bash
# First get the app ID:
curl -s "http://core-01:8080/management/v1/projects/PROJECTID/apps?query=displayName:librechat-{slug}" \
  -H "Authorization: Bearer $ZITADEL_SA_TOKEN" | jq '.apps[0].id'

# Then delete it:
curl -X DELETE "http://core-01:8080/management/v1/projects/PROJECTID/apps/{app_id}" \
  -H "Authorization: Bearer $ZITADEL_SA_TOKEN"
```

### Zitadel org

Delete the tenant org (cascades to all users and grants):

```bash
curl -X DELETE "http://core-01:8080/management/v1/orgs/{org_id}" \
  -H "Authorization: Bearer $ZITADEL_SA_TOKEN" \
  -H "x-zitadel-orgid: {org_id}"
```

### PostgreSQL rows

Hard-delete the workspace from all tables:

```bash
ssh core-01 'docker exec klai-core-postgres-1 psql -U klai -d klai -c "
  DELETE FROM portal_users WHERE org_id = {org_id};
  DELETE FROM portal_groups WHERE org_id = {org_id};
  DELETE FROM portal_products WHERE org_id = {org_id};
  DELETE FROM portal_templates WHERE org_id = {org_id};
  DELETE FROM portal_orgs WHERE id = {org_id};
"'
```

This will cascade to dependent tables with FK constraints. Verify:

```bash
ssh core-01 'docker exec klai-core-postgres-1 psql -U klai -d klai -c "
  SELECT COUNT(*) FROM portal_orgs WHERE id = {org_id};
"'
```

Should return 0.

### Audit emit (manual only for GDPR erasure)

If you manually cleaned up resources and want to emit an audit entry (normally done by the orchestrator):

```bash
ssh core-01 'docker exec klai-core-postgres-1 psql -U klai -d klai -c "
  INSERT INTO tenant_lifecycle_events (
    event_type, org_id_snapshot, org_slug_snapshot, org_name_snapshot,
    actor_user_id, actor_type, properties
  )
  VALUES (
    '\''deprovisioned'\'',
    {org_id},
    '\''{slug}'\'',
    '\''(manual cleanup)'\'',
    '\''manual'\'',
    '\''system'\'',
    '{\"reason\": \"manual-cleanup-after-orchestrator-failure\"}'\''::\jsonb
  );
"'
```

## GDPR & compliance context

### What survives the hard-delete

The `tenant_lifecycle_events` audit table **intentionally has no foreign key** to `portal_orgs`. When the workspace is hard-deleted, the audit row **persists indefinitely**. This is by design:

- **Audit trail**: Proof that the workspace existed, when it was created/deprovisioned, and by whom.
- **Compliance**: GDPR-erasure requests require evidence of deletion.

For the 7-year business record retention (Dutch fiscal law), the audit entry is sufficient proof.

### How to satisfy a GDPR erasure request

If a user or business request legal erasure beyond what the normal delete provides:

1. **Normal delete is already done**: The workspace row is gone.
2. **Logs are auto-purged**: VictoriaLogs rotates on a 30-day retention policy. After 30 days, the workspace's logs are automatically gone.
3. **Audit trail erasure (if necessary)**: To hard-purge the audit entry (not normally required):

```bash
ssh core-01 'docker exec klai-core-postgres-1 psql -U klai -d klai -c "
  DELETE FROM tenant_lifecycle_events WHERE org_slug_snapshot = '\''{slug}'\'';
"'
```

**Warning**: This destroys audit evidence. Only do this on explicit GDPR legal erasure request with proper authorization.

4. **Moneybird records**: Moneybird retains financial records per Dutch law (7-year retention). Contact Moneybird directly for erasure requests.

## Edge cases

### `failed_rollback_complete` org being deprovisioned

A workspace previously failed provisioning and has been soft-deleted (`deleted_at` is set, `provisioning_status = failed_rollback_complete`). An admin initiates deprovisioning:

- The workspace is already in a failed state with partial external resources.
- Deprovisioning will attempt to clean up again.
- Idempotent steps (file already gone, MongoDB DB already dropped) succeed silently.
- The workspace transitions to `failed_deprovisioning` only if a step fails the 3-retry limit.

### Owner deletes during active billing cycle

Moneybird subscription is still active and mid-cycle:

1. The `_archive_moneybird_subscription` step stops the subscription.
2. Moneybird may have outstanding invoices for the current period.
3. Those invoices **remain** (Dutch fiscal law requires 7-year retention).
4. Owner is responsible for handling any refunds or adjustments via Moneybird dashboard.

### Concurrency: two parallel DELETE clicks

1. Owner clicks "Delete" at 12:00:00.000.
2. Owner clicks again at 12:00:00.500 (before page redirects).
3. **First request**: `SELECT ... FOR UPDATE` succeeds, transitions to `deprovisioning`, returns 202.
4. **Second request**: `SELECT ... FOR UPDATE` blocks until first request commits.
5. On second request's `SELECT`, the row is already `deprovisioning` → second request fails with 409 `already_deprovisioning`.
6. Frontend error message: "This workspace is already being deleted."

### Tenant with no Moneybird subscription (free tier)

If `portal_orgs.moneybird_subscription_id` is NULL:

1. The `_archive_moneybird_subscription` step detects NULL.
2. It is a no-op (no API call) but logs a warning: `moneybird_subscription_not_configured`.
3. Step does NOT fail. Deprovisioning continues.

This is the correct behavior for free-tier workspaces.

## Runbook summary

| Scenario | Command |
|---|---|
| **Owner deletes workspace** | Open `/admin/danger-zone`, type slug, click delete. |
| **Admin deletes for user** | `curl -X DELETE https://my.getklai.com/api/admin/orgs/{slug}/deprovision -H "Authorization: Bearer $PLATFORM_ADMIN_TOKEN"` |
| **Monitor orchestrator** | VictoriaLogs: `service:portal-api AND deprovision AND org_slug:{slug}` |
| **Retry failed deprovision** | `curl -X POST https://my.getklai.com/api/admin/orgs/{slug}/retry-deprovisioning -H "Authorization: Bearer $PLATFORM_ADMIN_TOKEN"` |
| **Manually clean Caddy** | `ssh core-01 "rm /opt/klai/caddy/tenants/{slug}.caddyfile && docker restart klai-core-caddy-1"` |
| **Manually clean MongoDB** | `ssh core-01 'docker exec klai-core-mongo-1 mongosh -u $MONGO_ROOT_USERNAME -p "$MONGO_ROOT_PASSWORD" --authenticationDatabase admin --eval "db.getSiblingDB(\"librechat-{slug}\").dropDatabase()"'` |
| **GDPR hard-purge logs** | VictoriaLogs 30d auto-rotation (manual purge out-of-scope). |
| **GDPR hard-purge audit** | `ssh core-01 'docker exec klai-core-postgres-1 psql -U klai -d klai -c "DELETE FROM tenant_lifecycle_events WHERE org_slug_snapshot = '\''{slug}\''"'` (legal request only). |

## References

- SPEC: `.moai/specs/SPEC-INFRA-TENANT-DELETE-001/spec.md`
- Orchestrator: `klai-portal/backend/app/services/provisioning/deprovisioning_orchestrator.py`
- Steps: `klai-portal/backend/app/services/provisioning/deprovisioning_steps.py`
- Audit helper: `klai-portal/backend/app/services/audit/tenant_lifecycle.py`
- Endpoints: `klai-portal/backend/app/api/admin/deprovision_org.py`
- Frontend: `klai-portal/frontend/src/components/ui/delete-org-modal.tsx`, `danger-zone.tsx`, `deprovisioning-status.tsx`
- State machine: `klai-portal/backend/app/services/provisioning/state_machine.py`
