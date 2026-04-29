#!/bin/sh
# portal-api container entrypoint.
#
# Runs alembic migrations before starting the server. Fail-loud: any
# alembic error aborts the container so orchestration / observability
# sees the failure instead of silently continuing with a stale schema.
#
# Introduced by SPEC-CHAT-TEMPLATES-CLEANUP-001 after SPEC-CHAT-TEMPLATES-001
# required manual `docker exec ... alembic upgrade head` on production.
#
# SPEC-SEC-WEBHOOK-001 REQ-6: uvicorn launch delegated to shared launcher.
# The Caddy IP resolution and --proxy-headers logic now lives in a single
# script (scripts/uvicorn-launch.sh) shared across all klai backend services.
set -eu

echo "[entrypoint] Running alembic upgrade head…"
alembic upgrade head
echo "[entrypoint] Migrations applied."

exec /app/scripts/uvicorn-launch.sh app.main:app \
    --host 0.0.0.0 \
    --port 8010 \
    "$@"
