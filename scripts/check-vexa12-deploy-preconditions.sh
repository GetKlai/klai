#!/bin/sh
# SPEC-VEXA-004 — host preconditions for the parallel Vexa 0.12 stack.
#
# Run on core-01 BEFORE cutting portal-api over to vexa12-meeting-api. Every
# check here corresponds to a failure that is silent until the first real
# meeting, which is the worst moment to discover it.
#
#   1. Bot image present on the host.
#      docker-socket-proxy has IMAGES disabled (SPEC-SEC-024), so vexa12-runtime
#      CANNOT pull it — POST /containers/create returns "No such image" and every
#      spawn fails. The image is an env value, not a compose service, so
#      `compose up` does not fetch it either. It has to be pulled by hand.
#
#   2. Schema converged. meeting-api ships no alembic and never calls create_all;
#      vexa12-admin-api owns ensure_schema(). An empty vexa_v012 means the tables
#      do not exist and meeting-api has nothing to query.
#
#   3. Redis instance separation. Pub/sub is instance-wide in Redis — a DB index
#      does not isolate channels, and both versions publish the identical
#      bm:meeting:{id}:status / bot_commands:meeting:{id} names over independent
#      id sequences.
#
# Usage:  ssh core-01 'sh -s' < scripts/check-vexa12-deploy-preconditions.sh
# Exit:   0 = safe to cut over, 1 = at least one precondition unmet.

set -eu

BOT_IMAGE="vexaai/vexa-bot:v0.12.22"
FAIL=0

say_fail() { echo "FAIL: $1"; echo "      fix: $2"; FAIL=1; }
say_ok()   { echo "OK:   $1"; }

echo "── SPEC-VEXA-004 deploy preconditions ──"

# 1 — bot image on the host
if docker image inspect "$BOT_IMAGE" >/dev/null 2>&1; then
    say_ok "bot image $BOT_IMAGE present"
else
    say_fail "bot image $BOT_IMAGE is NOT on this host" \
             "docker pull $BOT_IMAGE   (the runtime cannot pull it: IMAGES disabled in docker-socket-proxy)"
fi

# 2 — schema converged in vexa_v012
TABLES=$(docker exec klai-core-postgres-1 sh -c \
    'psql -U $POSTGRES_USER -d vexa_v012 -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='"'"'public'"'"' AND table_name IN ('"'"'meetings'"'"','"'"'transcriptions'"'"','"'"'meeting_sessions'"'"')"' \
    2>/dev/null || echo "ERR")
if [ "$TABLES" = "3" ]; then
    say_ok "vexa_v012 schema converged (meetings, transcriptions, meeting_sessions)"
else
    say_fail "vexa_v012 has $TABLES/3 expected tables" \
             "start vexa12-admin-api and wait for it to become healthy — it owns ensure_schema()"
fi

# 3 — the two stacks must not share a Redis instance
OLD_REDIS=$(docker inspect klai-core-meeting-api-1 \
    --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null | sed -n 's/^REDIS_URL=.*@\([^:]*\):.*/\1/p' | head -1)
NEW_REDIS=$(docker inspect klai-core-vexa12-meeting-api-1 \
    --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null | sed -n 's/^REDIS_URL=.*@\([^:]*\):.*/\1/p' | head -1)
if [ -z "$NEW_REDIS" ]; then
    say_fail "vexa12-meeting-api is not running (cannot read its REDIS_URL)" \
             "deploy the vexa12 stack first"
elif [ "$OLD_REDIS" = "$NEW_REDIS" ]; then
    say_fail "both stacks point at Redis host '$NEW_REDIS'" \
             "give 0.12 its own instance — pub/sub is instance-wide, a DB index does not isolate channels"
else
    say_ok "Redis instances separated (0.10=$OLD_REDIS, 0.12=$NEW_REDIS)"
fi

echo "────────────────────────────────────────"
[ "$FAIL" -eq 0 ] && echo "All preconditions met — safe to repoint VEXA_MEETING_API_URL." \
                  || echo "Preconditions NOT met — do not cut over."
exit "$FAIL"
