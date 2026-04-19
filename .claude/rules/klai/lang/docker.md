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
- **portal-api uses explicit `environment:` block** — env vars are NOT auto-forwarded from `.env`. Adding a key to `.env` alone has no effect. Always add `NEW_VAR: ${NEW_VAR}` to the portal-api `environment:` block in `deploy/docker-compose.yml`. Verify: `docker compose config portal-api | grep -A 60 'environment:'`

## URL-encoded passwords
- Special chars (`/`, `+`, `=`) in passwords break `redis://` and `postgres://` URL parsing.
- Prefer separate `HOST`/`PORT`/`PASSWORD` vars over combined URLs.

## Presigned URLs with Docker-internal hostnames (HIGH)

S3 SDKs (minio, boto3) generate presigned URLs using the endpoint they were configured with. If the endpoint is a Docker-internal hostname (e.g., `garage:3900`), the presigned URL is unreachable from browsers.

**Why:** The SDK has no concept of "public URL" — it uses the endpoint verbatim. Inside Docker Compose, services resolve each other by container name, but browsers cannot.

**Prevention:** Never serve presigned URLs to browsers when the S3 endpoint is Docker-internal. Use reverse proxy (Caddy) + S3 website mode instead. See `platform/garage.md` for the Garage-specific pattern.

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

## Non-root USER and host volume ownership (HIGH)

Adding `USER app` to a Dockerfile does NOT change ownership of host-mounted Docker volumes. The host directory retains its original owner (typically root). The non-root container user cannot write to it and fails silently or with "Permission denied".

**Why:** Docker bind-mounts and named volumes retain host filesystem ownership. `chown` in Dockerfile only applies to the image layer, not the mounted path.

**Fix sequence when adding USER to an existing service:**
```bash
# 1. Find the host volume path
docker volume inspect <volume-name> | grep Mountpoint

# 2. Chown on the host BEFORE container recreate
sudo chown -R 1000:1000 /var/lib/docker/volumes/<volume>/_data

# 3. Recreate container
docker compose up -d <service>
```

**Prevention:** Any time you add `USER app` (uid 1000) to a Dockerfile that mounts a volume, immediately chown the host volume dir before deploy. Validate with `docker exec <ctr> touch /path/to/volume/test` after recreate.
