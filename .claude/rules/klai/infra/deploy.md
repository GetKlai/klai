---
paths:
  - "**/Dockerfile"
  - "**/docker-compose*.yml"
  - ".github/**/*.yml"
  - "**/*.sh"
---
# Deployment & CI/CD

## CI deploy verification (CRIT)
CI green ≠ production rollout. After `gh run watch --exit-status`:
1. Check container age: `docker ps --format '{{.Names}}\t{{.Status}}'`
2. Verify health endpoint or logs: `docker logs --tail 20 <ctr>`
3. Bundle timestamp for frontend: `ls -lt /srv/klai-portal/assets/*.js | head -3`

### Server rollout verification
Frontend: newest `.js` timestamp must match deploy time. If old, rsync target may be wrong.
```bash
ssh core-01 "ls -lt /srv/klai-portal/assets/*.js | head -3"
ssh core-01 "grep -l 'expected_keyword' /srv/klai-portal/assets/*.js"
```
Backend: container `CreatedAt` must be recent, health must return `{"status":"ok"}`.
```bash
ssh core-01 "docker ps --filter name=portal-api --format 'table {{.Names}}\t{{.Status}}\t{{.CreatedAt}}'"
ssh core-01 "curl -s http://localhost:8010/health"
```
Never skip verification — even for trivial changes or successful local builds.

## docker-compose.yml sync
CI service workflows do NOT copy compose to server — only pull image + restart.
`deploy-compose.yml` auto-syncs when `deploy/docker-compose.yml` changes on main.
Manual: `scp deploy/docker-compose.yml core-01:/opt/klai/docker-compose.yml`

## Atomic env writes (CRIT)
Never `cat >` or `echo >` to a live `.env`. Write-to-temp + validate + `mv`:
```bash
cat > /opt/klai/.env.new << 'EOF'
...
EOF
chmod 600 /opt/klai/.env.new && mv /opt/klai/.env.new /opt/klai/.env
```

## GHCR auth stale deploys
`docker pull` fails silently without `set -e` → old image runs. Store `GHCR_READ_PAT` in SOPS.
Alternative: build on server from public repo (sparse checkout + `docker build`).

## Alembic heads after merge
Two branches with migrations → multiple heads → `alembic upgrade head` fails.
Fix: `alembic merge heads -m "merge heads"`. Use `IF NOT EXISTS` in all DDL.

## Renovate
Schedule: Monday 05:00 Amsterdam. Automerge: patch (any), minor (devDeps only).
Docker images: grouped manual PR. Trigger: `gh workflow run renovate.yml`.

## Trivy scanning
Every Docker build workflow needs `scan` job after `build-push` with `security-events: write`.

## No manual server edits (CRIT)
Never edit compose/env on server — repo is source of truth. CI overwrites on next push.

## Secret recovery from containers (CRIT)
After env wipe: DO NOT restart containers. Recover values first:
`docker exec <ctr> printenv VAR_NAME` — values lost after restart.
Non-container vars (KUMA_TOKEN_*, GRAFANA_CADDY_HASH) invisible to this method.

## No architecture change in migration (CRIT)
Migration = same services, different server. NEVER consolidate or redesign during a move.
Source: SPEC-GPU-001 — agent replaced TEI + Infinity with single Infinity (GPU memory leak, no metrics).
