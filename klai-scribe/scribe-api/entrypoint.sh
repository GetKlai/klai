#!/bin/sh
# scribe-api container entrypoint.
#
# Runs alembic migrations before starting the server. Fail-loud: any
# alembic error aborts the container so orchestration / observability
# sees the failure instead of silently booting with a stale schema.
#
# Without this entrypoint the Dockerfile CMD launched uvicorn directly,
# so new columns were dormant until a manual:
#   docker exec klai-core-scribe-api-1 alembic upgrade head
# SPEC-SEC-HYGIENE-001 scribe-slice was bitten exactly by this pattern
# (see scribe-deploy-no-alembic pitfall in .claude/rules/klai/pitfalls/process-rules.md).
#
# SPEC-SEC-AUDIT-2026-04 C5 / SPEC-SEC-HYGIENE-001 fix: mirrors the portal-api
# entrypoint.sh pattern (SPEC-CHAT-TEMPLATES-CLEANUP-001).
#
# SPEC-SEC-WEBHOOK-001 REQ-6: uvicorn launch delegated to shared launcher.
# The Caddy IP resolution and --proxy-headers logic now lives in a single
# script (scripts/uvicorn-launch.sh) shared across all klai backend services.
set -eu

echo "[entrypoint] Running alembic upgrade head..."
alembic upgrade head
echo "[entrypoint] Migrations applied."

exec /app/scripts/uvicorn-launch.sh app.main:app \
    --host 0.0.0.0 \
    --port 8020 \
    "$@"
