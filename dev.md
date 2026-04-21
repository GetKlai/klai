# Dev environment (dev.getklai.com)

Parallel dev stack on core-01, fully isolated from production data.
Built for the `feat/chat-first-redesign` branch. Every push to `feat/*` auto-deploys.

---

## Architecture at a glance

```
┌──────────────────────── core-01 ────────────────────────┐
│                                                         │
│  Caddy                                                  │
│  ├─ getklai.getklai.com ─────→ portal-api     → klai    │
│  │                             /srv/portal    (prod)    │
│  └─ dev.getklai.com ─────────→ portal-api-dev → klai_dev│
│                                /srv/portal-dev  (dev)   │
│                                                         │
│  Shared: Postgres instance, Redis, MongoDB, Zitadel,    │
│          knowledge-ingest, retrieval-api, connector,    │
│          LibreChat (dev reuses librechat-getklai)       │
└─────────────────────────────────────────────────────────┘
```

| Component | Prod | Dev |
|---|---|---|
| Frontend dist | `/opt/klai/portal-dist/` | `/opt/klai/portal-dev/` |
| Backend container | `klai-core-portal-api-1` | `klai-core-portal-api-dev-1` |
| Backend image | `ghcr.io/getklai/portal-api:latest` | `ghcr.io/getklai/portal-api:dev` |
| Database | `klai` (user `portal_api`) | `klai_dev` (user `portal_api_dev`) |
| Secrets in `.env` | `PORTAL_API_*` | `PORTAL_API_DEV_*` |
| LibreChat | `librechat-getklai` | **shared** (dev hergebruikt prod) |
| MongoDB | shared | shared |

---

## Deploy flow

Push to any `feat/*` branch → two workflows trigger:

| Workflow | Trigger paths | Does |
|---|---|---|
| `portal-api-dev.yml` | `klai-portal/backend/**`, `deploy/docker-compose.dev.yml`, workflow file | ruff + pyright → build `:dev` image → SSH core-01 → pull + `docker compose up -d portal-api-dev` |
| `portal-frontend-dev.yml` | `klai-portal/frontend/**`, workflow file | eslint → `npm run build` → rsync `dist/` → `/opt/klai/portal-dev/` |

Manual trigger:
```bash
gh workflow run portal-api-dev.yml --ref feat/chat-first-redesign
gh workflow run portal-frontend-dev.yml --ref feat/chat-first-redesign
```

Watch:
```bash
gh run list --branch feat/chat-first-redesign --limit 3
gh run watch <run-id> --exit-status
```

### Caddyfile is NOT auto-deployed

The `caddy.yml` workflow only triggers on `main`. If you change `deploy/caddy/Caddyfile`
on feat, you must manually scp and reload:

```bash
scp deploy/caddy/Caddyfile core-01:/tmp/Caddyfile.new
ssh core-01 "cp /opt/klai/caddy/Caddyfile /opt/klai/caddy/Caddyfile.backup.$(date +%s) && \
             mv /tmp/Caddyfile.new /opt/klai/caddy/Caddyfile && \
             docker exec klai-core-caddy-1 caddy validate --config /etc/caddy/Caddyfile && \
             docker restart klai-core-caddy-1"
```

The current dev routing block is inside `handle @dev-host` with a `route {}`
wrapper (plain nested `handle /api/*` did not match — Caddy quirk).

---

## Database

`klai_dev` on the shared Postgres instance (`klai-core-postgres-1`).
Superuser is `klai`, not `postgres`.

### Inspect

```bash
ssh core-01 "docker exec klai-core-postgres-1 psql -U klai -d klai_dev -c '\dt'"
ssh core-01 "docker exec klai-core-postgres-1 psql -U klai -d klai_dev -c 'SELECT email, role FROM portal_users;'"
```

### Reseed from prod (full wipe + refill)

Used once when bootstrapping dev. Schema via `pg_dump --schema-only`, then
selected org's rows copied over. Tables with `org_id` column:
`portal_orgs, portal_users, portal_user_products, portal_knowledge_bases,
portal_groups, portal_group_products, portal_user_kb_access, portal_connectors`.
Junction tables (no `org_id`) require a `JOIN portal_groups ON group_id` path:
`portal_group_memberships, portal_group_kb_access`.

The org is renamed to `Dev` and `slug = 'dev'` so:
- `workspace_url = https://dev.getklai.com` (same host → no redirect loop)
- Chat iframe URL rewrites `dev → getklai` in `routes/app/index.tsx` so it
  loads `chat-getklai.getklai.com` (prod LibreChat).

### Reset a single user's role or state

```bash
ssh core-01 "docker exec klai-core-postgres-1 psql -U klai -d klai_dev -c \
  \"UPDATE portal_users SET role='admin' WHERE email='you@getklai.com';\""
```

### New migrations

Alembic revision IDs must be real hex — never hand-type them. Use:
```bash
python3 -c "import uuid; print(uuid.uuid4().hex[:12])"
```

The existing Alembic history on prod is broken (duplicate IDs). `alembic upgrade head`
will fail. For `klai_dev`, apply new tables manually via `CREATE TABLE` SQL or
`pg_dump -t <table>` + restore.

---

## Common operations

### Check dev container health
```bash
ssh core-01 "docker ps --filter name=portal-api-dev --format '{{.Names}} {{.Status}}'"
ssh core-01 "docker logs klai-core-portal-api-dev-1 --tail 30"
```

### Restart dev backend
```bash
ssh core-01 "cd /opt/klai && docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --force-recreate portal-api-dev"
```

### Verify bundle is served
```bash
ssh core-01 "cat /opt/klai/portal-dev/index.html | grep -oE 'assets/index-[^\"]*'"
curl -sI https://dev.getklai.com/ | head -3
curl -s https://dev.getklai.com/api/me | head -1  # expect {"detail":"Not authenticated"}
```

### View Caddy routing for dev
```bash
ssh core-01 "grep -A 15 '@dev-host' /opt/klai/caddy/Caddyfile"
```

### Inspect what dev secrets are set
```bash
ssh core-01 "grep '^PORTAL_API_DEV_' /opt/klai/.env | sed 's/=.*/=***/'"
```

---

## Known limitations

| Thing | Status | Why |
|---|---|---|
| Chat iframe | Shared with prod | LibreChat per-tenant provisioning has bugs (MongoDB auth, Zitadel OIDC app). Frontend rewrites `dev → getklai` on purpose. |
| Templates/rules in chat | Prod context | LibreChat container talks to prod `portal-api` (shared), not `portal-api-dev`. System prompts come from `klai`, not `klai_dev`. |
| IMAP listener | Disabled in dev | Prevents competing with prod for the shared mailbox. |
| Provision new tenants from dev | Broken | `provision_tenant()` has pre-existing bugs (MongoDB user auth, Zitadel). Don't run it. |
| User signup on dev | Not tested | Dev is seeded by hand from prod. New users via Zitadel likely land without an org row. |

---

## Files that matter

| Path | What |
|---|---|
| `deploy/docker-compose.dev.yml` | `portal-api-dev` service definition |
| `deploy/caddy/Caddyfile` (lines ~178-210) | `@dev-host` routing block |
| `.github/workflows/portal-api-dev.yml` | Backend build + deploy workflow |
| `.github/workflows/portal-frontend-dev.yml` | Frontend build + deploy workflow |
| `klai-portal/frontend/src/routes/app/index.tsx` (line ~35) | Chat tenant rewrite `dev → getklai` |
| `/opt/klai/.env` on core-01 | Shared prod + dev secrets (`PORTAL_API_DEV_*`) |
| `/opt/klai/docker-compose.dev.yml` on core-01 | Synced by `portal-api-dev.yml` workflow |
| `/opt/klai/caddy/Caddyfile` on core-01 | Bind-mounted into Caddy; **not** in the caddy Docker image |

---

## Making dev truly isolated (future work)

If dev needs its own chat data without touching prod:

1. New LibreChat container `librechat-dev` on a per-tenant Caddyfile (`chat-dev.getklai.com`)
2. Separate MongoDB database (`librechat-dev`) with own user
3. Separate Zitadel OIDC app for the dev LibreChat
4. Write `librechat-dev.env` under `/opt/klai/librechat/dev/`
5. Update `klai_dev.portal_orgs.librechat_container = 'librechat-dev'`
6. Remove the `dev → getklai` rewrite in frontend

Scope: ~1–2 hours if the existing `provision_tenant()` bugs are fixed first.
