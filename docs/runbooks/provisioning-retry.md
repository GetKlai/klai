# Tenant Provisioning — Retry & Recovery

> Covers: SPEC-PROV-001 state machine, admin retry endpoint, stuck detector, manual cleanup.
> Grafana dashboard: https://grafana.getklai.com/d/klai-provisioning

## State machine at a glance

Each tenant signup runs through this sequence, with a DB checkpoint per step:

```
pending / queued
  → creating_zitadel_app
  → creating_litellm_team
  → creating_mongo_user
  → writing_env_file
  → creating_personal_kb
  → creating_portal_kbs
  → starting_container
  → writing_caddyfile
  → reloading_caddy
  → creating_system_groups
  → ready
```

On failure, the orchestrator's `AsyncExitStack` drains compensators in reverse
order and the row lands on one of two terminal states:

| State | Meaning | Action required |
|---|---|---|
| `ready` | Fully provisioned | None. |
| `failed_rollback_complete` | Provisioning failed, but the rollback cleaned up all external resources. Row is soft-deleted (`deleted_at` set) so the slug is released. | None normally — user can re-signup, or admin can POST retry endpoint (see below). |
| `failed_rollback_pending` | Provisioning AND rollback failed. External resources may still exist. | **Manual inspection required.** See § Manual cleanup. |

## When to use the retry endpoint

`POST /api/admin/orgs/{slug}/retry-provisioning`

Admin-only. Use it when:

- A signup failed during a known transient issue (LiteLLM down, Zitadel
  degraded) and the row is now in `failed_rollback_complete` — retry runs the
  sequence again.
- You (ops or dev) simulated a failure as part of a deploy smoke-test.

Do NOT use it for:

- A stuck tenant in an intermediate state (`creating_*`). Let the stuck detector
  pick it up (see below) — it will transition to `failed_rollback_pending`
  after 15 minutes and you can then inspect and clean up manually.
- A tenant already in `failed_rollback_pending`. The endpoint refuses with 409
  `manual_cleanup_required` — this is intentional.

### Response codes

| HTTP | `error` field | When |
|---|---|---|
| 202 | — | Retry queued, provision_tenant BackgroundTask scheduled. |
| 409 | `manual_cleanup_required` | State is `failed_rollback_pending`. Inspect external resources first. |
| 409 | `not_in_retryable_state` | State is not `failed_rollback_complete` (e.g. `ready`, `creating_*`). |
| 409 | `slug_in_use_by_new_org` | Another active tenant now owns the slug. See § Slug collision. |
| 403 | — | Caller is not an admin. |
| 404 | — | No row with this slug exists. |

## After a portal-api deploy

The stuck detector runs at startup. Check Grafana for any
`provisioning.stuck_recovered` events in the last hour:

```sql
SELECT created_at, org_id, properties->>'last_state' AS last_state,
       properties->>'stuck_since' AS stuck_since
FROM product_events
WHERE event_type = 'provisioning.stuck_recovered'
  AND created_at >= NOW() - INTERVAL '1 hour'
ORDER BY created_at DESC;
```

Each row is a provisioning run that the previous portal-api instance abandoned.
They are now in `failed_rollback_pending`. Follow § Manual cleanup.

## Manual cleanup (`failed_rollback_pending`)

Because the in-memory compensator state is lost when portal-api crashes, we
cannot safely auto-clean. For each stuck org, inspect and remove external
resources in reverse order of the state sequence:

1. **Find the last forward state:**
   ```sql
   SELECT properties->>'to_state' AS last_forward
   FROM product_events
   WHERE event_type = 'provisioning.state_transition'
     AND org_id = <org_id>
     AND properties->>'to_state' NOT LIKE 'failed%'
     AND properties->>'to_state' != 'rollback_start'
   ORDER BY created_at DESC
   LIMIT 1;
   ```
2. **Inspect each external resource that should NOT exist for this slug** based
   on the last forward state:
   - Zitadel OIDC app: `zitadel console → Applications → librechat-<slug>`
   - LiteLLM team: `curl http://litellm:4000/team/list` (from core-01)
   - MongoDB user: `mongosh --eval 'db.getUsers({filter:{user:"librechat-<slug>"}})'`
   - Docker container: `docker ps -a --filter name=librechat-<slug>`
   - Caddyfile: `ls /opt/klai/caddy/tenants/<slug>.caddyfile`
   - Tenant dir: `ls /opt/klai/librechat-data/<slug>`
3. **Remove each resource that exists.** The deprovisioning scripts in
   `klai-infra/scripts/deprovision-tenant.sh` (when available) handle most of
   this; otherwise remove by hand.
4. **Update the DB row** to `failed_rollback_complete` + set `deleted_at`:
   ```sql
   UPDATE portal_orgs
   SET provisioning_status = 'failed_rollback_complete',
       deleted_at = NOW()
   WHERE id = <org_id>;
   ```
5. **Optionally retry** via the admin endpoint, or let the user re-signup.

## Slug collision (`slug_in_use_by_new_org`)

Returned when an admin retry targets a soft-deleted failed row, but another
active row already owns the slug (because the user re-signed up successfully
between the failure and the retry).

Options:
- **Keep the soft-deleted row for audit.** Do nothing — the new org is already
  live. Close the retry ticket.
- **Hard-delete the soft-deleted row.** If audit retention is not needed:
  ```sql
  DELETE FROM portal_orgs
  WHERE id = <org_id>
    AND deleted_at IS NOT NULL
    AND provisioning_status = 'failed_rollback_complete';
  ```

## Grafana queries

Per-tenant timeline:
```sql
SELECT created_at, properties->>'from_state' AS from_state,
       properties->>'to_state' AS to_state,
       properties->>'step' AS step,
       (properties->>'duration_ms')::int AS duration_ms
FROM product_events
WHERE event_type = 'provisioning.state_transition'
  AND org_id = <org_id>
ORDER BY created_at ASC;
```

Failure rate (last 7d):
```sql
SELECT
  COUNT(*) FILTER (WHERE properties->>'to_state' = 'ready') AS succeeded,
  COUNT(*) FILTER (WHERE properties->>'to_state' = 'failed_rollback_complete') AS failed,
  COUNT(*) FILTER (WHERE properties->>'to_state' = 'failed_rollback_pending') AS stuck
FROM product_events
WHERE event_type = 'provisioning.state_transition'
  AND created_at >= NOW() - INTERVAL '7 days';
```

## VictoriaLogs queries

Trace a single provisioning run:
```
service:portal-api AND org_id:<org_id> AND event:provisioning_state_transition
```

Find rollback failures:
```
service:portal-api AND (event:rollback_zitadel_app_failed OR event:rollback_litellm_team_failed OR event:rollback_mongodb_user_failed OR event:rollback_container_removal_failed OR event:rollback_caddy_failed)
```

## References

- SPEC: `.moai/specs/SPEC-PROV-001/spec.md`
- Orchestrator: `klai-portal/backend/app/services/provisioning/orchestrator.py`
- State machine: `klai-portal/backend/app/services/provisioning/state_machine.py`
- Stuck detector: `klai-portal/backend/app/services/provisioning/stuck_detector.py`
- Retry endpoint: `klai-portal/backend/app/api/admin/retry_provisioning.py`
