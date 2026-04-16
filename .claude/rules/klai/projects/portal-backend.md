---
paths:
  - "klai-portal/backend/**/*.py"
---
# Portal Backend Patterns

## FRONTEND_URL controls OAuth redirect URIs (CRIT)

`FRONTEND_URL` in portal-api env is NOT cosmetic — it is used to construct OAuth callback URLs
(Google Drive, Microsoft, etc.). A wrong value causes `redirect_uri_mismatch` for every user.

- Must match the actual login domain: `https://my.getklai.com`
- Registered Google/Microsoft OAuth redirect URIs must match exactly: `https://my.getklai.com/api/oauth/.../callback`
- `config.py` falls back to `https://portal.{domain}` if FRONTEND_URL is empty — this fallback is wrong for production
- `{tenant}.getklai.com` is NOT the portal URL — that is the per-tenant view; `my.getklai.com` is the login URL

**Why:** Root cause of April 2026 incident: `FRONTEND_URL=https://getklai.com` → callback pointed to `getklai.com` which is unrouted → 50x OAuth errors per affected user.

**Prevention:** After any domain change, verify: `docker exec portal-api printenv FRONTEND_URL` matches `https://my.getklai.com`. Never derive the portal URL from Caddy wildcard config or Zitadel redirect URIs — check servers.md.

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

## Locale propagation pattern

Propagate locale through OAuth/redirect flows via query parameter, not browser state. Pattern used in IDP intent signup:

1. Frontend sends `locale` in the request body
2. Backend validates with a `@field_validator` against `_SUPPORTED_LOCALES`, defaulting to `"nl"`
3. Locale is embedded in the `success_url` as a query param before redirecting to Zitadel
4. Callback reads it as `locale: str = Query(default="nl")` and validates again
5. All redirects and cookie payloads carry the locale forward

**Rule:** OAuth callback endpoints must not rely on session state or browser cookies for locale — it must travel through the redirect URL as a validated query parameter.

## portal-api scripts/ not in Docker image (MED)
`klai-portal/backend/scripts/` is NOT copied into the container (no `COPY scripts/` in Dockerfile).
Data migration scripts in `scripts/` cannot be run via `docker exec portal-api python scripts/foo.py`.
Workaround: pass inline via `docker exec portal-api python3 -c "$(cat scripts/foo.py)"` or add `COPY scripts/ scripts/` to the Dockerfile.
