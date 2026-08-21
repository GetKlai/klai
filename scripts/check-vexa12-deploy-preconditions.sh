#!/bin/sh
# SPEC-VEXA-004 — host readiness for the Vexa 0.12 stack.
#
# Run on core-01. Both checks here correspond to a failure that is silent until
# the first real meeting, which is the worst moment to discover it. Neither is
# a one-shot cutover gate: each one re-arms on every bot-image bump, every
# restore, and every fresh host, so this is worth running whenever the Vexa
# stack has been touched — not only before a migration.
#
#   1. Bot image present on the host.
#      docker-socket-proxy has IMAGES disabled (SPEC-SEC-024), so vexa12-runtime
#      CANNOT pull it — POST /containers/create returns "No such image" and every
#      spawn fails. The image is an env value, not a compose service, so
#      `compose up` does not fetch it either. It has to be pulled by hand, and
#      again after every tag bump.
#
#   2. Schema converged. meeting-api ships no alembic and never calls create_all;
#      vexa12-admin-api owns ensure_schema(). An empty vexa_v012 means the tables
#      do not exist and meeting-api has nothing to query.
#
# RETIRED — the Redis-separation check (removed 2026-08-21).
#   It compared vexa12-meeting-api's REDIS_URL against the 0.10 stack's, to catch
#   two co-resident Vexa deployments sharing one Redis instance (pub/sub is
#   instance-wide; a DB index does not isolate `bm:meeting:{id}:status`). PR #914
#   (9dced9f37, 2026-08-14) deleted the 0.10 stack, so its container
#   klai-core-meeting-api-1 has not existed since — `docker inspect` failed, the
#   value came back empty, and "empty != vexa12-redis" landed on the SUCCESS
#   branch. The check reported OK unconditionally for a week.
#   Note the shape, because it is the reusable lesson: `set -eu` does NOT catch
#   this. The exit status of `VAR=$(docker inspect … | sed … | head -1)` is
#   head's, which is 0; the `2>/dev/null` only hides the message. Any empty
#   value that reaches the OK side of a comparison is a check that cannot fail.
#   Making it fail-closed instead would have been just as wrong — its premise,
#   two stacks on one host, is permanently false now, so it would only ever
#   have been red. A check whose question no longer exists gets retired, not
#   repaired.
#
# This script needs a live Docker host with the real containers on it, so it is
# an operator command and not a CI gate. Its logic IS covered pre-merge by
# deploy/scripts/tests/check-vexa12-preconditions.test.sh, which drives it with
# a docker stub.
#
# Usage:  ssh core-01 'sh -s' < scripts/check-vexa12-deploy-preconditions.sh
# Exit:   0 = host ready, 1 = at least one check failed.

set -eu

BOT_IMAGE="vexaai/vexa-bot:v0.12.22"
FAIL=0

say_fail() { echo "FAIL: $1"; echo "      fix: $2"; FAIL=1; }
say_ok()   { echo "OK:   $1"; }

echo "── SPEC-VEXA-004 host readiness ──"

# 1 — bot image on the host
if docker image inspect "$BOT_IMAGE" >/dev/null 2>&1; then
    say_ok "bot image $BOT_IMAGE present"
else
    say_fail "bot image $BOT_IMAGE is NOT on this host" \
             "docker pull $BOT_IMAGE   (the runtime cannot pull it: IMAGES disabled in docker-socket-proxy)"
fi

# 2 — schema converged in vexa_v012
#
# The `|| echo ERR` sentinel is load-bearing: an unreachable daemon, a missing
# postgres container and a missing database all land on ERR, and ERR is not 3,
# so every one of them is reported. Do not "simplify" this into a bare
# assignment — an empty value would compare false against "3" today, but the
# next person to touch the comparison inherits the trap that killed check 3.
TABLES=$(docker exec klai-core-postgres-1 sh -c \
    'psql -U $POSTGRES_USER -d vexa_v012 -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='"'"'public'"'"' AND table_name IN ('"'"'meetings'"'"','"'"'transcriptions'"'"','"'"'meeting_sessions'"'"')"' \
    2>/dev/null || echo "ERR")
if [ "$TABLES" = "3" ]; then
    say_ok "vexa_v012 schema converged (meetings, transcriptions, meeting_sessions)"
else
    say_fail "vexa_v012 has $TABLES/3 expected tables" \
             "start vexa12-admin-api and wait for it to become healthy — it owns ensure_schema()"
fi

echo "──────────────────────────────────"
[ "$FAIL" -eq 0 ] && echo "Host ready for the Vexa 0.12 stack." \
                  || echo "Host NOT ready — fix the above before spawning bots."
exit "$FAIL"
