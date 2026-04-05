---
paths:
  - "klai-portal/backend/**/*.py"
---
# Portal Backend Patterns

## SQLAlchemy + RLS (CRIT)
- SQLAlchemy ORM adds implicit `RETURNING` to all inserts — breaks RLS tables with separate SELECT/INSERT policies.
- Use `text()` raw SQL for inserts on RLS-protected tables where the inserting role differs from the reading role.
- `::jsonb` casts conflict with SQLAlchemy `:param` — use `CAST(:param AS jsonb)` instead.

## Prometheus metrics in tests
- Never use the global `prometheus_client` registry in tests — causes `Duplicated timeseries`.
- Use a `CollectorRegistry` per instance via dataclass + `autouse` fixture that patches module-level singleton.

## sendBeacon endpoints
- `navigator.sendBeacon` cannot set `Authorization` headers.
- Design analytics endpoints as intentionally unauthenticated. Rate-limit at Caddy. Validate/clamp with Pydantic.

## Fire-and-forget writes (audit, analytics)
- Request-scoped session rolls back on any exception — audit entries are lost.
- Use an independent `AsyncSessionLocal()` session for writes that must survive caller exceptions.

## Status string contracts
- Status values (`recording`, `processing`, etc.) are cross-layer contracts: backend, frontend, i18n, polling, badges.
- Before renaming: `grep -r "old_value"` across the entire monorepo + all case variants.

## Event emission
- Event name must match the actual user action, not a configuration step.
- Before `COUNT(DISTINCT field)` in dashboards, verify the field is populated at emit time.
- Pre-auth events (`login`, `signup`) have no `org_id` — don't use org-based aggregation.

## SELECT FOR UPDATE in get-or-create patterns (CRIT)
Any "get or create" on a shared row (per-org keys, per-tenant state) MUST use `SELECT ... FOR UPDATE`.
Two concurrent requests that both see NULL will generate conflicting values — one silently overwrites the other.
SPEC-KB-020: plain `db.get(PortalOrg, org_id)` in `get_or_create_dek` allowed two requests to generate different DEKs, making the first connector's credentials permanently unreadable.
```python
# Correct pattern
result = await db.execute(
    select(PortalOrg).where(PortalOrg.id == org_id).with_for_update()
)
org = result.scalar_one_or_none()
```

## portal-api scripts/ not in Docker image (MED)
`klai-portal/backend/scripts/` is NOT copied into the container (no `COPY scripts/` in Dockerfile).
Data migration scripts in `scripts/` cannot be run via `docker exec portal-api python scripts/foo.py`.
Workaround: pass inline via `docker exec portal-api python3 -c "$(cat scripts/foo.py)"` or add `COPY scripts/ scripts/` to the Dockerfile.
