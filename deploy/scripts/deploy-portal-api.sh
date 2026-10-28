#!/usr/bin/env bash
# Canonical portal-api production deploy.
#
# Usage: deploy-portal-api.sh ghcr.io/getklai/portal-api:<git sha>
#
# Runs schema work from the new image before replacing the live app container.
# If Alembic or post-deploy SQL fails, the old portal-api container keeps
# serving traffic. The container swap itself goes through
# deploy-with-rollback.sh (SPEC-INFRA-AUTO-ROLLBACK-001 S2), which puts the
# previous image back when compose-up.sh or the /health probe is red.
#
# Rule R3: that rollback is image-only. It never runs `alembic downgrade` and
# never undoes post-deploy SQL, so the previous image runs on the newer schema.
# The expand-then-contract check (scripts/check-expand-contract.py) is what
# keeps that safe.
#
# Exit codes are deploy-with-rollback.sh's (0 deployed, 3 rolled_back,
# 4 rollback_failed); 1 for anything that failed before the swap, or for the
# OIDC smoke check after a deploy that was otherwise green.

set -euo pipefail

if [[ $# -ne 1 || "$1" != *:* || "$1" == *:latest ]]; then
    echo "usage: ${0##*/} <image-ref-with-sha>   (rule R1: the exact sha, never :latest)" >&2
    exit 1
fi
IMAGE="$1"
# The tag deploy/docker-compose.yml runs portal-api from.
COMPOSE_TAG="${IMAGE%:*}:latest"
COMPOSE_DIR="${KLAI_COMPOSE_DIR:-/opt/klai}"
POSTGRES_CONTAINER="${KLAI_POSTGRES_CONTAINER:-klai-core-postgres-1}"
# Installed next to this script, in /opt/klai/scripts on core-01.
DEPLOY_WITH_ROLLBACK="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/deploy-with-rollback.sh"

cd "$COMPOSE_DIR"

# Refuse to start a deploy that the disk cannot finish.
#
# On 2026-09-18 the root filesystem hit 100%. The pull and `alembic upgrade
# head` both succeeded, and the deploy then died at the `mktemp -d` below with
# ENOSPC — after the schema had already moved and before the post-deploy SQL
# ran. Failing late is the expensive direction here: the half that landed is the
# irreversible half.
#
# 4 GiB is roughly six times the 637 MB portal-api image, which covers the pull,
# the layer extraction, and the migration's own temp files with room to spare. It
# is a floor against a full disk, not a capacity plan: the Grafana rule
# core01_disk_usage_high is what should catch the slope long before this fires.
#
# Three paths, because three different filesystems could be the one that fills:
# the compose directory, the temp directory mktemp actually writes to, and the
# image store. Under the containerd snapshotter that store is
# /var/lib/containerd, not /var/lib/docker. All three are /dev/md2 on core-01
# today, which is exactly why measuring only one would keep passing on the day
# that stops being true.
required_free_kib=$((4 * 1024 * 1024))
available_kib="$(df -Pk . "${TMPDIR:-/tmp}" /var/lib/containerd 2>/dev/null \
    | awk 'NR > 1 && (min == "" || $4 + 0 < min) { min = $4 + 0 } END { print min + 0 }')"
if [[ "$available_kib" -lt "$required_free_kib" ]]; then
    echo "ERROR: $(( available_kib / 1024 / 1024 )) GiB free on the tightest of" >&2
    echo "       $(pwd), ${TMPDIR:-/tmp} and /var/lib/containerd; need 4 GiB to deploy safely." >&2
    echo "       Refusing to start: a deploy that fails mid-flight can leave migrations applied" >&2
    echo "       with post-deploy SQL unapplied. Free space first, then re-run." >&2
    exit 1
fi

if [[ -f .env ]]; then
    # shellcheck disable=SC1091
    source .env
fi

echo "Pulling ${IMAGE}..."
docker pull "$IMAGE"
# `compose run` below takes the image from the compose tag, so point it at the
# new image first. deploy-with-rollback.sh records the previous image from the
# running container, not from this tag, so the way back is unaffected.
docker tag "$IMAGE" "$COMPOSE_TAG"

echo "Running portal-api Alembic migrations from the new image..."
docker compose run --rm --no-deps --entrypoint alembic portal-api upgrade head

tmp_dir="$(mktemp -d)"
extract_ctr=""
cleanup() {
    if [[ -n "$extract_ctr" ]]; then
        docker rm -f "$extract_ctr" >/dev/null 2>&1 || true
    fi
    rm -rf "$tmp_dir"
}
trap cleanup EXIT

echo "Extracting post-deploy SQL from the new image..."
extract_ctr="$(docker create --entrypoint sh "$IMAGE" -c 'true')"
docker cp "$extract_ctr:/repo/klai-portal/backend/alembic/versions" "$tmp_dir/versions"

apply_sql() {
    local sql_file="$1"
    echo "  [apply] $(basename "$sql_file")"
    docker exec -i "$POSTGRES_CONTAINER" psql -U klai -d klai -v ON_ERROR_STOP=1 < "$sql_file"
}

echo "Applying portal-api post-deploy SQL..."
bootstrap="$tmp_dir/versions/post_deploy_rls_raise_on_missing_context.sql"
if [[ -f "$bootstrap" ]]; then
    apply_sql "$bootstrap"
fi

while IFS= read -r sql_file; do
    name="$(basename "$sql_file")"
    case "$name" in
        *_rollback_*)
            echo "  [skip] $name (rollback script)"
            ;;
        post_deploy_rls_raise_on_missing_context.sql)
            echo "  [skip] $name (bootstrap already applied)"
            ;;
        *)
            apply_sql "$sql_file"
            ;;
    esac
done < <(find "$tmp_dir/versions" -maxdepth 1 -name 'post_deploy_*.sql' | sort)

echo "Recreating portal-api..."
"$DEPLOY_WITH_ROLLBACK" portal-api "$IMAGE" --probe http://portal-api:8010/health || exit $?

oidc_status="$(curl -sS -o /dev/null -w '%{http_code}' https://my.getklai.com/api/auth/oidc/start)"
if [[ "$oidc_status" != "302" ]]; then
    echo "OIDC smoke check failed: expected 302, got $oidc_status" >&2
    exit 1
fi

echo "OIDC smoke check OK"
