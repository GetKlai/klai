#!/bin/sh
# Does this LibreChat config boot on this image?
#
# usage: librechat-config-boot.sh <image-ref> <config-file>
#
# Why this exists: on merge, deploy-librechat-config.yml copies librechat.yaml
# to the host and restarts every tenant chat container, which still run the
# image pinned in deploy/docker-compose.yml. A config that moved to a newer
# upstream schema (a new top-level `projects` block) made the running image
# exit at startup on "Unrecognized key(s) in object: 'projects'". Only a
# reviewer's question and a manual boot caught it. This script is that manual
# boot: the image, the Klai entrypoint and the config, a throwaway MongoDB,
# and a wait for /health.
#
# Fails (exit 1) when the config-validation error appears in the log, when the
# container exits, or when /health is not 200 within BOOT_TIMEOUT seconds
# (default 120). Prints the log tail on failure. Exit 2 is a usage or setup
# problem, never a verdict on the config.
#
# Minimum environment, all fake. Each was found by reading the entrypoint, the
# compose service and the tenant .env generator (generators.py):
#   MONGO_URI                 the throwaway MongoDB below; LibreChat does not
#                             serve /health before it is connected.
#   JWT_SECRET, JWT_REFRESH_SECRET
#                             the server refuses to start without both.
#   CREDS_KEY, CREDS_IV       32-byte hex key and 16-byte hex IV; v0.8.8+
#                             refuses to start on any other length.
#   HOST, PORT                listen on all interfaces, port 3080; /health is
#                             probed from inside the container, so nothing is
#                             published on the runner.
#   DOMAIN_CLIENT, DOMAIN_SERVER
#                             https://chat.example.com, used for URLs only.
#   MEILI_MESSAGES_INDEX, MEILI_CONVOS_INDEX
#                             tenants always set both, and setting them runs
#                             the entrypoint's tenant-scoped Meili transform,
#                             so a pin that breaks that transform fails here.
#   MEILI_HOST, MEILI_MASTER_KEY, MEILI_NO_SYNC
#                             example.com host and fake key; SEARCH stays
#                             unset (as in provisioning) and MEILI_NO_SYNC=true
#                             keeps LibreChat from reaching for it.
#   KLAI_CHAT_API_KEY, KLAI_CHAT_BASE_URL
#                             librechat.yaml expands these for the "Klai AI"
#                             endpoint; fake values keep the expansion honest.
#   KLAI_ORG_SLUG, KLAI_ZITADEL_ORG_ID, KNOWLEDGE_INGEST_SECRET
#                             expanded in the knowledge MCP headers.
#   TWENTY_BASE_URL, TWENTY_API_KEY
#                             the catalog's required_env_vars for the twenty-crm
#                             server, which the rendered tenant config adds.
#                             Unset, `${TWENTY_BASE_URL}/mcp` is not a URL and
#                             the server exits with "Invalid URL" (measured);
#                             a tenant that enables the server has both in its
#                             .env, so the check does too.
# Deliberately absent: OpenID, Redis, LiteLLM and MCP credentials. They are
# read at login or request time, not at boot, and leaving them out keeps the
# container free of outbound calls.
#
# Needs Docker and anonymous pull access to the image. Touches nothing that is
# running: its own containers on its own network, named per run, removed on
# exit.
set -eu

[ $# -eq 2 ] || { echo "usage: $0 <image-ref> <config-file>" >&2; exit 2; }
IMAGE=$1
CONFIG=$2
[ -f "$CONFIG" ] || { echo "config file not found: $CONFIG" >&2; exit 2; }

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
ENTRYPOINT="$ROOT/deploy/librechat/klai-entrypoint.sh"
COMPOSE="$ROOT/deploy/docker-compose.yml"
CONFIG=$(cd "$(dirname "$CONFIG")" && pwd)/$(basename "$CONFIG")
MONGO_IMAGE=$(awk '/^  mongodb:/{f=1} f&&/^[[:space:]]*image:/{print $2; exit}' "$COMPOSE")
[ -n "$MONGO_IMAGE" ] || { echo "no mongodb image found in $COMPOSE" >&2; exit 2; }
TIMEOUT=${BOOT_TIMEOUT:-120}

P=lc-config-boot-$$
# shellcheck disable=SC2329  # invoked via trap
cleanup() {
    docker rm -f "${P}-app" "${P}-mongo" >/dev/null 2>&1 || true
    docker network rm "${P}-net" >/dev/null 2>&1 || true
}
trap cleanup EXIT HUP INT TERM

echo "booting $CONFIG on $IMAGE (mongo: $MONGO_IMAGE, timeout ${TIMEOUT}s)"
docker network create "${P}-net" >/dev/null
docker run -d --name "${P}-mongo" --network "${P}-net" "$MONGO_IMAGE" >/dev/null

i=0
until docker exec "${P}-mongo" mongosh --quiet --eval 'db.runCommand({ping:1}).ok' >/dev/null 2>&1; do
    i=$((i + 1))
    [ $i -lt 30 ] || { echo "throwaway MongoDB did not come up" >&2; docker logs "${P}-mongo" 2>&1 | tail -20 >&2; exit 2; }
    sleep 2
done

docker run -d --name "${P}-app" --network "${P}-net" \
    -v "$CONFIG:/app/librechat.yaml:ro" \
    -v "$ENTRYPOINT:/klai-entrypoint.sh:ro" \
    -e MONGO_URI="mongodb://${P}-mongo:27017/librechat-boot-test" \
    -e JWT_SECRET=0000000000000000000000000000000000000000000000000000000000000001 \
    -e JWT_REFRESH_SECRET=0000000000000000000000000000000000000000000000000000000000000002 \
    -e CREDS_KEY=0000000000000000000000000000000000000000000000000000000000000003 \
    -e CREDS_IV=00000000000000000000000000000004 \
    -e HOST=0.0.0.0 -e PORT=3080 \
    -e DOMAIN_CLIENT=https://chat.example.com -e DOMAIN_SERVER=https://chat.example.com \
    -e MEILI_MESSAGES_INDEX=boottest_messages -e MEILI_CONVOS_INDEX=boottest_convos \
    -e MEILI_HOST=http://meilisearch.example.com:7700 -e MEILI_MASTER_KEY=fake-meili-key -e MEILI_NO_SYNC=true \
    -e KLAI_CHAT_API_KEY=fake-chat-key -e KLAI_CHAT_BASE_URL=http://portal-api.example.com/partner/v1 \
    -e KLAI_ORG_SLUG=boottest -e KLAI_ZITADEL_ORG_ID=000000000000000000 \
    -e KNOWLEDGE_INGEST_SECRET=fake-ingest-secret \
    -e TWENTY_BASE_URL=https://crm.example.com -e TWENTY_API_KEY=fake-twenty-key \
    --entrypoint /bin/sh \
    "$IMAGE" /klai-entrypoint.sh npm run backend >/dev/null

CONFIG_ERRORS='Invalid custom config file|Exiting due to invalid configuration'

fail() {
    echo "FAIL $1" >&2
    echo "---- last 40 log lines ----" >&2
    docker logs "${P}-app" 2>&1 | tail -40 >&2
    exit 1
}

PROBE="fetch('http://127.0.0.1:3080/health').then(r=>process.exit(r.status===200?0:1)).catch(()=>process.exit(1))"
elapsed=0
while [ $elapsed -lt "$TIMEOUT" ]; do
    if docker logs "${P}-app" 2>&1 | grep -qE "$CONFIG_ERRORS"; then
        fail "config-validation: LibreChat rejected the config"
    fi
    if [ "$(docker inspect -f '{{.State.Running}}' "${P}-app")" != true ]; then
        fail "exited: the container stopped with code $(docker inspect -f '{{.State.ExitCode}}' "${P}-app")"
    fi
    if docker exec "${P}-app" node -e "$PROBE" >/dev/null 2>&1; then
        # A schema error can be logged just after the server starts listening.
        if docker logs "${P}-app" 2>&1 | grep -qE "$CONFIG_ERRORS"; then
            fail "config-validation: LibreChat rejected the config"
        fi
        echo "OK /health 200 after ${elapsed}s"
        exit 0
    fi
    sleep 2
    elapsed=$((elapsed + 2))
done
fail "timeout: /health was not 200 within ${TIMEOUT}s"
