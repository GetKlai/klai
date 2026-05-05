#!/bin/bash
# SPEC-INGEST-ALEMBIC-001: run alembic upgrade head before starting the service.
# Mirrors klai-connector/entrypoint.sh (scribe-deploy-no-alembic pitfall).
set -eu

# prepend_sys_path = . in alembic.ini resolves imports; PYTHONPATH is a belt-and-suspenders guard.
export PYTHONPATH=.

echo "[entrypoint] Running alembic upgrade head..."
alembic upgrade head
echo "[entrypoint] Migrations applied."

exec "$@"
