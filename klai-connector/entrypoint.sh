#!/bin/sh
# klai-connector container entrypoint.
#
# Runs alembic migrations before starting the server. Fail-loud: any
# alembic error aborts the container so orchestration / observability
# sees the failure instead of silently continuing with a stale schema.
#
# Introduced after migration 006_add_org_id_to_sync_runs shipped in the
# image but never landed in production: the GitHub workflow does
# `docker compose up -d klai-connector` only — no alembic step. Live
# `Sync now` clicks failed with "column sync_runs.org_id does not exist"
# until a manual `docker exec ... alembic upgrade head` was run by hand.
#
# Same root cause as the scribe-deploy-no-alembic pitfall
# (.claude/rules/klai/pitfalls/process-rules.md). Mirrors the canonical
# pattern from klai-portal/backend/entrypoint.sh.
set -eu

echo "[entrypoint] Running alembic upgrade head…"
alembic upgrade head
echo "[entrypoint] Migrations applied."

# Hand off to the original CMD args. Keeps the Dockerfile flexible:
# anything passed after `entrypoint.sh` runs as the long-lived process.
exec "$@"
