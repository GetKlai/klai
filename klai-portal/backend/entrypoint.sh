#!/bin/sh
# portal-api container entrypoint.
#
# Runs alembic migrations before starting the server. Fail-loud: any
# alembic error aborts the container so orchestration / observability
# sees the failure instead of silently continuing with a stale schema.
#
# Introduced by SPEC-CHAT-TEMPLATES-CLEANUP-001 after SPEC-CHAT-TEMPLATES-001
# required manual `docker exec ... alembic upgrade head` on production.
set -eu

echo "[entrypoint] Running alembic upgrade head…"
alembic upgrade head
echo "[entrypoint] Migrations applied. Starting uvicorn."

exec uvicorn app.main:app --host 0.0.0.0 --port 8010 "$@"
