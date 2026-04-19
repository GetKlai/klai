#!/usr/bin/env bash
# SPEC-VEXA-003 — idempotent Vexa DB bootstrap for Klai.
#
# Ensures the klai-system user + api_token exist in the Vexa postgres
# database with the correct scopes. Safe to re-run after a DB wipe,
# a fresh deploy, or as part of an automated recovery script.
#
# Reads VEXA_API_KEY from /opt/klai/.env and upserts:
#   users(email='klai-system@klai.internal') with max_concurrent_bots=10
#   api_tokens(token=$VEXA_API_KEY, user_id=above, scopes=bot|browser|tx)
#
# Run on core-01 after any vexa-related deploy that may touch the vexa DB:
#   ssh core-01 /opt/klai/deploy/vexa/bootstrap.sh
#
# Exit 0 on success (including when nothing had to change).
# Exit non-zero on any failure.

set -euo pipefail

ENV_FILE="${ENV_FILE:-/opt/klai/.env}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-klai-core-postgres-1}"
API_GATEWAY_CONTAINER="${API_GATEWAY_CONTAINER:-klai-core-api-gateway-1}"
VEXA_USER_EMAIL="klai-system@klai.internal"
VEXA_USER_NAME="Klai Portal System"
VEXA_USER_MAX_CONCURRENT="${VEXA_USER_MAX_CONCURRENT:-10}"
TOKEN_NAME="klai-portal-api"
TOKEN_SCOPES='{bot,browser,tx}'

log() { printf '[vexa-bootstrap] %s\n' "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

[ -r "$ENV_FILE" ] || die "cannot read $ENV_FILE"

# Source VEXA_API_KEY only; avoid pulling other env into this shell.
VEXA_API_KEY=$(grep -E '^VEXA_API_KEY=' "$ENV_FILE" | head -1 | cut -d= -f2-)
[ -n "${VEXA_API_KEY:-}" ] || die "VEXA_API_KEY not set in $ENV_FILE"

docker inspect "$POSTGRES_CONTAINER" >/dev/null 2>&1 \
    || die "$POSTGRES_CONTAINER not running"

# Refuse to run against a vexa DB that still has a mismatched legacy token.
# We upsert the token, but if an existing row with the same token points at
# a different user, that's a broken state we refuse to paper over silently.
existing_user_id=$(
    docker exec -i "$POSTGRES_CONTAINER" psql -U vexa -d vexa -qtA \
        -c "SELECT user_id FROM api_tokens WHERE token='$VEXA_API_KEY';" 2>/dev/null || true
)

log "Upserting klai-system user..."
user_id=$(
    docker exec -i "$POSTGRES_CONTAINER" psql -U vexa -d vexa -qtA <<SQL
INSERT INTO users (email, name, max_concurrent_bots, data)
VALUES ('$VEXA_USER_EMAIL', '$VEXA_USER_NAME', $VEXA_USER_MAX_CONCURRENT, '{}'::jsonb)
ON CONFLICT (email) DO UPDATE SET
  name = EXCLUDED.name,
  max_concurrent_bots = EXCLUDED.max_concurrent_bots
RETURNING id;
SQL
)
[ -n "${user_id:-}" ] || die "failed to resolve user_id"
log "  → user_id = $user_id"

if [ -n "$existing_user_id" ] && [ "$existing_user_id" != "$user_id" ]; then
    die "token is already bound to user_id=$existing_user_id (expected $user_id); refuse to rebind"
fi

log "Upserting klai-portal-api token..."
docker exec -i "$POSTGRES_CONTAINER" psql -U vexa -d vexa -qtA <<SQL >/dev/null
INSERT INTO api_tokens (token, user_id, scopes, name)
VALUES ('$VEXA_API_KEY', $user_id, ARRAY['bot','browser','tx'], '$TOKEN_NAME')
ON CONFLICT (token) DO UPDATE SET
  user_id = EXCLUDED.user_id,
  scopes  = EXCLUDED.scopes,
  name    = EXCLUDED.name;
SQL
log "  → token scopes = $TOKEN_SCOPES"

# api-gateway caches token validation (Redis, 60s TTL). If scopes were updated,
# kick it so the new scopes are live immediately instead of on next TTL expiry.
if docker inspect "$API_GATEWAY_CONTAINER" >/dev/null 2>&1; then
    log "Restarting $API_GATEWAY_CONTAINER to flush token cache..."
    docker restart "$API_GATEWAY_CONTAINER" >/dev/null
fi

log "Bootstrap complete."
