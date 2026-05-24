#!/usr/bin/env bash
# Canonical portal-api production deploy.
#
# Runs schema work from the freshly pulled image before replacing the live app
# container. If Alembic or post-deploy SQL fails, the old portal-api container
# keeps serving traffic.

set -euo pipefail

IMAGE="${KLAI_PORTAL_API_IMAGE:-ghcr.io/getklai/portal-api:latest}"
COMPOSE_DIR="${KLAI_COMPOSE_DIR:-/opt/klai}"
POSTGRES_CONTAINER="${KLAI_POSTGRES_CONTAINER:-klai-core-postgres-1}"
PORTAL_CONTAINER="${KLAI_PORTAL_CONTAINER:-klai-core-portal-api-1}"

cd "$COMPOSE_DIR"

if [[ -f .env ]]; then
    # shellcheck disable=SC1091
    source .env
fi

if [[ -n "${GHCR_READ_PAT:-}" ]]; then
    echo "$GHCR_READ_PAT" | docker login ghcr.io -u "${GHCR_READ_USER:-mvletter}" --password-stdin
fi

echo "Pulling ${IMAGE}..."
docker pull "$IMAGE"

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
/opt/klai/scripts/compose-up.sh portal-api

echo "Waiting for portal-api to serve /health..."
for i in $(seq 1 30); do
    if docker exec "$PORTAL_CONTAINER" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/health', timeout=2)" >/dev/null 2>&1; then
        echo "portal-api health OK"
        break
    fi
    if [[ "$i" -eq 30 ]]; then
        echo "portal-api health check failed" >&2
        docker logs --tail=200 "$PORTAL_CONTAINER" >&2 || true
        exit 1
    fi
    sleep 2
done

oidc_status="$(curl -sS -o /dev/null -w '%{http_code}' https://my.getklai.com/api/auth/oidc/start)"
if [[ "$oidc_status" != "302" ]]; then
    echo "OIDC smoke check failed: expected 302, got $oidc_status" >&2
    exit 1
fi

echo "OIDC smoke check OK"
