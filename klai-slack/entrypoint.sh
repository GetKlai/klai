#!/bin/sh
# Apply migrations before serving, fail loud: a migration error stops the
# container instead of running new code on a stale schema. Mirrors
# klai-connector/entrypoint.sh.
set -eu

echo "[entrypoint] Running alembic upgrade head"
alembic upgrade head
echo "[entrypoint] Migrations applied"

exec "$@"
