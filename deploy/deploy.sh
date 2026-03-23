#!/usr/bin/env bash
# Deploy script for core-01
#
# NOTE: The docker-compose.yml now lives in the monorepo (GetKlai/klai/deploy/).
# Pull the latest compose file from there before running a full deploy:
#   git -C /opt/klai pull   (if you cloned the monorepo to /opt/klai)
# or copy it from your local checkout.
#
# This script handles the secrets side: it decrypts .sops env files locally
# and pipes them to the server via SSH. The compose file itself is version-
# controlled and does not contain any secrets.
#
# Usage: ./deploy.sh [service]  (no argument: all services)
set -euo pipefail

DEPLOY_DIR="$(dirname "$0")"
COMPOSE_DIR="/opt/klai"
SOPS_KEY="${SOPS_AGE_KEY_FILE:-$HOME/.config/sops/age/keys.txt}"

if [ ! -f "$SOPS_KEY" ]; then
    echo "ERROR: age private key not found at $SOPS_KEY"
    exit 1
fi

# Load config for server address
CONFIG_PLAIN="$DEPLOY_DIR/config.env"
CONFIG_SOPS="$DEPLOY_DIR/config.sops.env"
if [ -f "$CONFIG_PLAIN" ]; then
    # shellcheck source=/dev/null
    source "$CONFIG_PLAIN"
elif [ -f "$CONFIG_SOPS" ] && command -v sops &>/dev/null; then
    eval "$(SOPS_AGE_KEY_FILE="$SOPS_KEY" sops --decrypt --output-type dotenv "$CONFIG_SOPS")"
else
    echo "ERROR: No config found. Decrypt config.sops.env first."
    exit 1
fi

# Deploy main .env: escape $ -> $$ so docker-compose variable substitution passes values through.
# Values in SOPS are stored as-is (e.g. bcrypt hash $2a$14$...).
# Docker-compose reads .env and converts $$ back to $ when substituting into compose YAML.
deploy_main_env() {
    local src="$DEPLOY_DIR/.env.sops"
    local dst="$COMPOSE_DIR/.env"

    if [ ! -f "$src" ]; then
        echo "[main] No .env.sops found, skipping"
        return
    fi

    echo "[main] Decrypting and deploying to $SERVER_HOST:$dst..."
    SOPS_AGE_KEY_FILE="$SOPS_KEY" \
        sops --decrypt --input-type dotenv --output-type dotenv "$src" \
        | sed 's/\$/\$\$/g' \
        | ssh "$SERVER_HOST" "cat > $dst && chmod 600 $dst"
    echo "[main] Done."
}

deploy_service_env() {
    local service=$1
    local src="$DEPLOY_DIR/$service/.env.sops"
    local dst="$COMPOSE_DIR/$service/.env"

    if [ ! -f "$src" ]; then
        echo "[$service] No .env.sops found, skipping"
        return
    fi

    echo "[$service] Decrypting and deploying to $SERVER_HOST:$dst..."
    SOPS_AGE_KEY_FILE="$SOPS_KEY" \
        sops --decrypt --input-type dotenv --output-type dotenv "$src" \
        | ssh "$SERVER_HOST" "cat > $dst && chmod 600 $dst"
    echo "[$service] Done."
}

case "${1:-all}" in
    main)
        deploy_main_env
        ;;
    zitadel|litellm|caddy|klai-mailer|klai-connector)
        deploy_service_env "$1"
        ;;
    all)
        deploy_main_env
        for svc in zitadel litellm caddy klai-mailer klai-connector; do
            deploy_service_env "$svc"
        done
        ;;
    *)
        echo "Usage: $0 [main|zitadel|litellm|caddy|klai-mailer|klai-connector|all]"
        exit 1
        ;;
esac

echo "Done. Start services with: ssh $SERVER_HOST 'cd $COMPOSE_DIR && docker compose up -d'"
