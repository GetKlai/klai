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
#   compose-up.sh                          — pull + up all services
#   compose-up.sh <service-name>           — pull + up single service
#   compose-up.sh --no-deps <svc>          — pull + up without service deps
#   compose-up.sh --force-recreate <svc>   — pull + up with --force-recreate
#                                            (drops Python module cache for
#                                            services whose code lives in
#                                            bind-mounted .py files)
#
# When to use --force-recreate:
#   `docker compose up -d` only recreates a container when the compose
#   DEFINITION changed (volume list, env-vars, image tag). Bind-mount
#   FILE CONTENT changes are invisible to compose. For services that
#   import bind-mounted Python files at module load (e.g. litellm with
#   klai_knowledge.py / klai_context.py / klai_chat_prompts.py
#   / klai_retrieval_telemetry.py / custom_router.py vendored on /app/), a
#   bind-mount-content rsync followed by `up -d` is a no-op: Python keeps
#   the cached module from the previous boot and the new code never runs.
#   --force-recreate forces a fresh container, which drops the cache and
#   reimports from disk. Tracked under
#   `bind-mount-content-vs-python-module-cache` in the process pitfalls.
#
# Exit code mirrors the underlying `docker compose` exit code; on
# successful compose-up, a non-zero exit from audit-orphan-snapshot.sh
# is logged but does NOT fail the deploy (snapshot is detective, not
# preventive — REQ-2d).

set -euo pipefail

# Pre-flight: refuse to run if /opt/klai is missing or compose-file absent.
# Better a fail-fast with a clear error than a silent partial deploy.
if [[ ! -f /opt/klai/docker-compose.yml ]]; then
    echo "ERROR: /opt/klai/docker-compose.yml not found — was deploy-compose.yml run?" >&2
    exit 2
fi

cd /opt/klai

POSTGRES_CONTAINER="${KLAI_POSTGRES_CONTAINER:-klai-core-postgres-1}"
NO_DEPS_FLAG=""
FORCE_RECREATE_FLAG=""
SERVICE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-deps)
            NO_DEPS_FLAG="--no-deps"
            shift
            ;;
        --force-recreate)
            FORCE_RECREATE_FLAG="--force-recreate"
            shift
            ;;
        *)
            if [[ -z "$SERVICE" ]]; then
                SERVICE="$1"
            else
                echo "ERROR: unexpected argument '$1' — usage: compose-up.sh [--no-deps] [--force-recreate] [service]" >&2
                exit 2
            fi
            shift
            ;;
    esac
done

litellm_prisma_migrate_enabled() {
    grep -Eq 'USE_PRISMA_MIGRATE:[[:space:]]*"?True"?' /opt/klai/docker-compose.yml
}

check_litellm_prisma_migration_baseline() {
    if ! litellm_prisma_migrate_enabled; then
        return 0
    fi

    echo "Checking LiteLLM Prisma migration baseline before recreate..."
    if ! docker ps --format '{{.Names}}' | grep -qx "$POSTGRES_CONTAINER"; then
        echo "ERROR: $POSTGRES_CONTAINER is not running; refusing to recreate litellm with USE_PRISMA_MIGRATE=True" >&2
        exit 1
    fi

    local migration_table
    if ! migration_table="$(
        docker exec "$POSTGRES_CONTAINER" \
            psql -U litellm -d litellm -v ON_ERROR_STOP=1 -tAc \
            "SELECT to_regclass('public._prisma_migrations')"
    )"; then
        echo "ERROR: cannot inspect LiteLLM _prisma_migrations table; refusing to recreate litellm" >&2
        exit 1
    fi
    migration_table="$(echo "$migration_table" | tr -d '[:space:]')"
    if [[ "$migration_table" != "public._prisma_migrations" && "$migration_table" != "_prisma_migrations" ]]; then
        echo "ERROR: LiteLLM USE_PRISMA_MIGRATE=True but public._prisma_migrations is missing." >&2
        echo "Refusing to recreate litellm; baseline the DB first or remove USE_PRISMA_MIGRATE to rollback." >&2
        exit 1
    fi

    local migration_count
    if ! migration_count="$(
        docker exec "$POSTGRES_CONTAINER" \
            psql -U litellm -d litellm -v ON_ERROR_STOP=1 -tAc \
            "SELECT count(*) FROM public._prisma_migrations"
    )"; then
        echo "ERROR: cannot count LiteLLM Prisma migrations; refusing to recreate litellm" >&2
        exit 1
    fi
    migration_count="$(echo "$migration_count" | tr -d '[:space:]')"
    if ! [[ "$migration_count" =~ ^[0-9]+$ ]] || (( migration_count < 1 )); then
        echo "ERROR: LiteLLM public._prisma_migrations is empty; prisma migrate deploy may fail on a non-empty schema." >&2
        echo "Refusing to recreate litellm; baseline the DB first or remove USE_PRISMA_MIGRATE to rollback." >&2
        exit 1
    fi
    echo "LiteLLM Prisma migration baseline OK ($migration_count applied migrations)"
}

if [[ -z "$SERVICE" || "$SERVICE" == "litellm" ]]; then
    check_litellm_prisma_migration_baseline
fi

if [[ -n "$SERVICE" ]]; then
    echo "Pulling $SERVICE..."
    # Pull is best-effort. Some services intentionally have no
    # registry image and `docker compose pull` exits non-zero:
    #   - retrieval-api: image klai/retrieval-api:local — tag-aliased
    #     locally from ghcr.io/getklai/retrieval-api:latest before this
    #     script runs (see retrieval-api.yml workflow `docker tag`).
    #   - bge-m3-sparse on gpu-01: built from local context.
    # For these the existing image is already up-to-date in the local
    # daemon; we proceed to `up -d` which uses what's there.
    if ! docker compose pull "$SERVICE" 2>&1; then
        echo "WARN: pull failed for $SERVICE (likely a locally-tagged image like klai/<svc>:local) — proceeding with existing local image"
    fi
    if [[ -n "$FORCE_RECREATE_FLAG" ]]; then
        echo "Recreating $SERVICE with --remove-orphans --force-recreate..."
    else
        echo "Recreating $SERVICE with --remove-orphans..."
    fi
    # shellcheck disable=SC2086
    docker compose up -d --remove-orphans $NO_DEPS_FLAG $FORCE_RECREATE_FLAG "$SERVICE"
else
    echo "Pulling all services..."
    if ! docker compose pull 2>&1; then
        echo "WARN: bulk pull had failures (likely klai/<svc>:local-tagged services) — proceeding with existing local images"
    fi
    if [[ -n "$FORCE_RECREATE_FLAG" ]]; then
        echo "Recreating all services with --remove-orphans --force-recreate..."
    else
        echo "Recreating all services with --remove-orphans..."
    fi
    # shellcheck disable=SC2086
    docker compose up -d --remove-orphans $FORCE_RECREATE_FLAG
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
