## SPEC-GRAFANA-METRICS Progress

- Started: 2026-03-24T00:00:00Z
- Phase 1 complete: manager-strategy analysis done, plan approved by user
- Phase 1.6 complete: 12 acceptance criteria registered
- Phase 1.7 complete: stub files created (events.py model, events.py service, migration)
- Phase 2 complete: 13 files created/modified
  - NEW: alembic/versions/p6q7r8s9t0u1_add_product_events_table.py
  - NEW: app/models/events.py
  - NEW: app/services/events.py
  - NEW: tests/test_emit_event.py
  - NEW: deploy/grafana/provisioning/datasources/portal-postgres.yaml
  - NEW: deploy/grafana/provisioning/dashboards/klai-business.json
  - NEW: deploy/grafana/provisioning/dashboards/klai-product.json
  - NEW: deploy/grafana/provisioning/dashboards/klai-health.json
  - NEW: deploy/grafana/sql/grafana-reader-setup.sql
  - MOD: klai-portal/backend/app/api/signup.py (emit signup event)
  - MOD: klai-portal/backend/app/api/auth.py (emit login event)
  - MOD: klai-portal/backend/app/api/billing.py (emit billing events)
  - MOD: klai-portal/backend/app/api/meetings.py (emit meeting events)
- Phase 2.5 complete (2026-04-03): additional events + dashboard panel
  - MOD: klai-portal/backend/app/api/connectors.py (emit knowledge.uploaded on trigger_sync)
  - MOD: deploy/grafana/provisioning/dashboards/klai-product.json (Feature Adoption panel)
  - Commit: 18234fc (pushed to main, CI green, deployed to core-01)

## Event Coverage Status (12 SPEC events)

| Event | Implemented | Location | Notes |
|-------|-------------|----------|-------|
| signup | Yes | signup.py | AC-2 |
| login | Yes | auth.py | AC-3, no org_id (pre-auth) |
| billing.plan_changed | Yes | billing.py | AC-4 |
| billing.cancelled | Yes | billing.py | AC-4 |
| meeting.started | Yes | meetings.py | AC-5 |
| meeting.completed | Yes | meetings.py | AC-5 |
| meeting.summarized | Yes | meetings.py | AC-5 |
| knowledge.uploaded | Yes | connectors.py | Added 2026-04-03 |
| notebook.created | Yes | research-api/notebooks.py | Added 2026-04-04 |
| notebook.opened | Yes | research-api/notebooks.py | Added 2026-04-04 |
| source.added | Yes | research-api/sources.py | Added 2026-04-04 (file + URL) |
| knowledge.queried | Yes | retrieval-api/retrieve.py | Added 2026-04-04 (skip notebook scope) |

## Phase 3 complete (2026-04-04): cross-service events via portal DB
  - NEW: klai-focus/research-api/app/services/events.py (asyncpg event emitter)
  - NEW: klai-retrieval-api/retrieval_api/services/events.py (asyncpg event emitter)
  - MOD: klai-focus/research-api/app/core/config.py (portal_events_dsn setting)
  - MOD: klai-focus/research-api/app/api/notebooks.py (notebook.created, notebook.opened)
  - MOD: klai-focus/research-api/app/api/sources.py (source.added for file + URL)
  - MOD: klai-retrieval-api/retrieval_api/config.py (portal_events_dsn setting)
  - MOD: klai-retrieval-api/retrieval_api/api/retrieve.py (knowledge.queried)
  - MOD: klai-retrieval-api/pyproject.toml (asyncpg dependency)
  - MOD: deploy/docker-compose.yml (PORTAL_EVENTS_DSN + net-postgres for retrieval-api)

## Status: Complete (12/12 events, 3/3 dashboards)
