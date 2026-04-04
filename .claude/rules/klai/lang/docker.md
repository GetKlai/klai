---
paths:
  - "**/docker-compose*.yml"
  - "**/Dockerfile*"
  - "deploy/**"
  - "klai-infra/**"
---
# Docker & Container Rules

## Pre-flight before `docker compose up -d`
[HARD] Before running `up -d`, check env vars the container will receive:
```bash
docker compose config [service] | grep -A 60 'environment:'
```
After `up -d`, immediately check logs + health — do NOT proceed until both pass:
```bash
docker logs --tail 30 [container-name]
docker ps --filter name=[service] --format '{{.Names}}\t{{.Status}}'
```

## `restart` vs `up -d`
- `docker compose restart` does NOT re-read `.env` — use `up -d` after env changes.
- `up -d` recreates the container, injecting ALL vars from `.env` — including vars the service never received before.

## Image versions
- Never use versions from AI training data. Always `WebSearch` current stable version.
- Never use `:latest` in production — pin explicit version tags.
- Use `--no-cache` after dependency or base image changes.

## Environment files
- Atomic writes only: write to `.env.new`, validate, then `mv` — never `cat >` or `echo >` directly.
- SOPS is the single source of truth. Never manually edit `/opt/klai/.env` for permanent changes.
- Repo is source of truth for compose files. Never edit `docker-compose.yml` on the server.

## URL-encoded passwords
- Special chars (`/`, `+`, `=`) in passwords break `redis://` and `postgres://` URL parsing.
- Prefer separate `HOST`/`PORT`/`PASSWORD` vars over combined URLs.

## CI deploy verification
- CI green ≠ production deployed. After `gh run watch`, verify on the server: container age, health endpoint, logs.
- `set -e` in all deploy scripts — without it, `docker pull` failure silently continues with the old image.

## Alembic migrations
- After merging branches with migrations: `alembic heads` to detect multiple heads.
- Use `IF NOT EXISTS` in all migration DDL (policies, indexes, constraints).

## Server restart protocol
- Start and restart services with `docker compose up -d` or restart scripts, output visible in foreground.
- NEVER use `run_in_background=true` to start servers — hides startup failures, creates zombie processes with stale code.

## Recovery
- If `.env` is corrupted: recover vars from still-running containers via `docker exec <ctr> printenv`.
- NEVER restart containers before recovering — restart reads the broken `.env` and loses in-memory values.
