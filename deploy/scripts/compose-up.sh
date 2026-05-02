#!/usr/bin/env bash
# /opt/klai/scripts/compose-up.sh
#
# Canonical deploy-wrapper for klai-core compose stack.
# Replaces ad-hoc `docker compose up -d <svc>` calls in service-deploy
# workflows with a single shared mechanism that:
#
#   1. Pulls the targeted service image (or all images if no service given)
#   2. Recreates with --remove-orphans so containers no longer in
#      docker-compose.yml are cleaned up automatically (klasse-A only —
#      provisioning-managed klasse-B containers carry their own labels
#      and are NOT touched by --remove-orphans)
#   3. Emits a post-deploy orphan-snapshot event to VictoriaLogs via
#      audit-orphan-snapshot.sh so detection runs on every deploy
#
# SPEC-INFRA-CONTAINER-HYGIENE-001 REQ-3.
#
# Usage:
#   compose-up.sh                   — pull + up all services
#   compose-up.sh <service-name>    — pull + up single service
#   compose-up.sh --no-deps <svc>   — pull + up without service deps
#
# Exit code mirrors the underlying `docker compose` exit code; on
# successful compose-up, a non-zero exit from audit-orphan-snapshot.sh
# is logged but does NOT fail the deploy (snapshot is detective, not
# preventive — REQ-2d).

set -euo pipefail

cd /opt/klai

NO_DEPS_FLAG=""
SERVICE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-deps)
            NO_DEPS_FLAG="--no-deps"
            shift
            ;;
        *)
            if [[ -z "$SERVICE" ]]; then
                SERVICE="$1"
            else
                echo "ERROR: unexpected argument '$1' — usage: compose-up.sh [--no-deps] [service]" >&2
                exit 2
            fi
            shift
            ;;
    esac
done

if [[ -n "$SERVICE" ]]; then
    echo "Pulling $SERVICE..."
    docker compose pull "$SERVICE"
    echo "Recreating $SERVICE with --remove-orphans..."
    # shellcheck disable=SC2086
    docker compose up -d --remove-orphans $NO_DEPS_FLAG "$SERVICE"
else
    echo "Pulling all services..."
    docker compose pull
    echo "Recreating all services with --remove-orphans..."
    docker compose up -d --remove-orphans
fi

# REQ-2d post-deploy orphan snapshot. Best-effort — snapshot failure
# does NOT fail the deploy. The snapshot script emits structlog-events
# to stdout; Alloy picks them up into VictoriaLogs.
if [[ -x /opt/klai/scripts/audit-orphan-snapshot.sh ]]; then
    /opt/klai/scripts/audit-orphan-snapshot.sh "${SERVICE:-all}" || \
        echo "WARN: post-deploy orphan-snapshot failed (deploy itself succeeded)" >&2
else
    echo "WARN: /opt/klai/scripts/audit-orphan-snapshot.sh not installed yet — skipping post-deploy snapshot" >&2
fi
