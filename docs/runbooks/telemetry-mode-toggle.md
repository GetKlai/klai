# Runbook — Toggling a tenant's telemetry mode

Status: live since 2026-05-08 (SPEC-PRIVACY-QUERY-SHADOW-001).

## What this runbook covers

How a Klai operator switches a tenant between `off` / `shadow` / `full`
telemetry modes for active debug, post-deploy validation, or to recover
from a tenant who flipped to `full` and forgot to reset.

This is the operator escape hatch for the same toggle that tenant-admins
own via `https://<tenant>.getklai.com/admin/settings`. Both paths share
the same DB-write + audit-log + cache-invalidation behaviour.

## Mode reference (canonical)

| Mode | What it persists | When to use |
|------|------------------|-------------|
| `off` | Nothing — no shadow row, no gap-event, no query log | Tenant has explicit data-minimization requirement and accepts losing operational telemetry |
| `shadow` (default) | Embedding + symbolic features (tokens, lang, has_brand, …); 7d TTL. Raw query NEVER persisted | Default for all tenants. Lets Klai do quality monitoring without raw-text retention |
| `full` | Everything: raw query in `decision_record` log, raw `query_text` in `portal_retrieval_gaps`, raw `query_resolved` in retrieval-log Redis blob — all 7d TTL | Active debug only. Audit-log row records the operator and reason; alert fires after 14 days |

## How to toggle (operator path — internal admin)

```bash
ssh core-01
TOKEN="$(printenv PORTAL_INTERNAL_SECRET)"

# Check current state
curl -sS http://portal-api:8010/internal/admin/orgs/<org_id>/telemetry-level \
  -H "Authorization: Bearer $TOKEN" | jq .

# Switch to 'full' for active debug
curl -sS -X POST \
  http://portal-api:8010/internal/admin/orgs/<org_id>/telemetry-level \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"level":"full","reason":"Investigating ticket #1234"}'

# Switch back to 'shadow' when done
curl -sS -X POST \
  http://portal-api:8010/internal/admin/orgs/<org_id>/telemetry-level \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"level":"shadow","reason":"Closing debug session for ticket #1234"}'
```

Cache propagation: the LiteLLM hook picks up the new level within ~30s
via the `kb_ver:{org_id}:*` Redis-key invalidation. No restart needed.

## How to toggle (tenant path — self-service)

A tenant-admin opens
`https://<tenant>.getklai.com/admin/settings`, scrolls to the
"Privacy & telemetrie" card, picks the level from the dropdown, clicks
Save. The audit-log records `operator_kind='tenant_admin'`,
`reason='tenant self-service via admin UI'`,
`operator_user_id=<zitadel sub>`.

Operator action: usually NONE. Self-service is the preferred path for
non-emergency toggles.

## Verifying the change took effect

```bash
# 1. DB state
ssh core-01 "docker exec klai-core-postgres-1 psql -U klai -d klai \
  -c \"SELECT slug, telemetry_level FROM portal_orgs WHERE id = <org_id>;\""

# 2. Cache state (kb_ver pointer should be deleted)
ssh core-01 "docker exec klai-core-redis-1 redis-cli \
  KEYS 'kb_ver:<org_id>:*'"

# 3. Audit row
ssh core-01 "docker exec klai-core-postgres-1 psql -U klai -d klai \
  -c \"SELECT details FROM portal_audit_log
        WHERE org_id = <org_id> AND action = 'telemetry_level_changed'
        ORDER BY created_at DESC LIMIT 3;\""

# 4. Live behavior — wait 60s, then run a /retrieve and tail VictoriaLogs:
#    service:retrieval-api AND request_id:<your_test_id>
#    In 'full' mode the decision_record event has coreference_rewrite.{original,resolved}
#    In 'shadow'/'off' the same event has retention_class='metadata' and no query text
```

## Alerts that route to this runbook

- `privacy_tenant_stuck_in_full` — fires when any tenant has been in
  `full` for >14 days. Triage: list affected tenants, check the audit
  reason, decide with the operator whether to flip back.
- `privacy_shadow_drop_burst` — fires when shadow-store INSERTs drop
  >1/s for 5m. Triage: group by `reason`, check post-deploy SQL ran,
  verify Postgres health.

## Common failure modes

### Cache doesn't invalidate

The level changes in the DB but the LiteLLM hook keeps using the old
level. Symptom: a /retrieve call >60s after the flip still emits
content fields in `full` mode (or strips them in `shadow`).

Cause: the SCAN+DEL of `kb_ver:{org_id}:*` failed (Redis blip mid-flip).

Fix: manually delete the keys:
```bash
ssh core-01 "docker exec klai-core-redis-1 redis-cli \
  --scan --pattern 'kb_ver:<org_id>:*' | \
  xargs -r docker exec -i klai-core-redis-1 redis-cli DEL"
```

### Tenant-admin gets 403 on the dropdown

The user is not the `admin` role on the tenant's PortalUser row. Either
elevate the role OR have the tenant ask an existing org-admin to flip.
The internal-admin path (operator) is always available as a backup.

### `telemetry.query_shadow` table missing

A fresh deploy ran `alembic upgrade head` but the post-deploy SQL was
not applied. Symptom: `telemetry_shadow_drop_total{reason="db_error"}`
spikes; `service:retrieval-api AND message:"undefined_table"`.

Fix:
```bash
ssh core-01 "docker exec -i klai-core-postgres-1 psql -U klai -d klai" \
  < klai-portal/backend/alembic/versions/post_deploy_g5h6i7j8k9l0.sql
```

(Or via the canonical wrapper: `scripts/apply_post_deploy_sql.sh g5h6i7j8k9l0`.)

## Reset cadence and review

Privacy posture review checklist for the on-call rotation, weekly:

1. Open the `Privacy — Telemetry mode distribution` Grafana dashboard.
2. Confirm the per-tenant pie shows `shadow` as dominant; flag any
   tenant in `full` for >5 days.
3. Spot-check the audit-log for entries with `operator_kind='operator'`
   in the last week — every operator-side flip should have a real
   ticket/reason.

## Background / why the modes exist

See `.moai/specs/SPEC-PRIVACY-QUERY-SHADOW-001/` for the full SPEC,
research notes, and acceptance criteria. Key context: GDPR Article
5(1)(c) data minimization. Default `shadow` lets Klai do operational
quality monitoring without persisting customer-facing query text.
