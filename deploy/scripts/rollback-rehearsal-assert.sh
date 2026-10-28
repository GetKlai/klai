#!/usr/bin/env bash
# /opt/klai/scripts/rollback-rehearsal-assert.sh
#
# The weekly proof that automatic rollback works (SPEC-INFRA-AUTO-ROLLBACK-001
# section 8, slice S7). rollback-rehearsal.yml runs this over ssh on core-01.
#
# Usage:
#   rollback-rehearsal-assert.sh <good-image-ref> <bad-image-ref>
#
# It drives the real deploy-with-rollback.sh against the rollback-canary
# service, three steps, each of which must hold:
#   1. the good image deploys                      -> exit 0
#   2. the bad image deploys and is rolled back    -> exit 3 and a report line
#      with outcome rolled_back (exit 0 means the bad image went live, exit 4
#      means the rollback itself failed)
#   3. within KLAI_REHEARSAL_WAIT seconds (default 300) the canary container runs
#      the good image again and answers its HTTP probe
# Only then one rollback_rehearsal event line is appended to the auto-rollback
# log, which Alloy ships to VictoriaLogs; the Grafana rule
# rollback_rehearsal_missing fires when no such line arrived for 8 days. Any
# other result prints an ::error:: line and exits 1.
#
# The deliberate rollback in step 2 also writes an auto_rollback event; the
# Grafana rule on that event excludes deploy_service:rollback-canary.
#
# Tests: deploy/scripts/tests/rollback-rehearsal-assert.test.sh

set -uo pipefail

# Seams for the test-suite; production keeps every default.
KLAI_DIR="${KLAI_COMPOSE_DIR:-/opt/klai}"
DOCKER="${KLAI_DOCKER_BIN:-docker}"
DEPLOY="${KLAI_DEPLOY_WITH_ROLLBACK:-$KLAI_DIR/scripts/deploy-with-rollback.sh}"
EVENT_LOG="${KLAI_AUTO_ROLLBACK_LOG:-$KLAI_DIR/logs/auto-rollback.log}"
WAIT="${KLAI_REHEARSAL_WAIT:-300}"
INTERVAL="${KLAI_REHEARSAL_INTERVAL:-10}"
# Same pinned image deploy-with-rollback.sh probes with.
CURL_IMAGE="${KLAI_CURL_IMAGE:-curlimages/curl:8.11.1}"

# Must match the rollback-canary service in deploy/docker-compose.yml.
SERVICE="rollback-canary"
COMPOSE_TAG="klai/rollback-canary:1.0-local-261028-0000"
PROBE_URL="http://rollback-canary:8080/"
PROBE_NETWORK="klai-rehearsal"

(( $# == 2 )) || { echo "usage: ${0##*/} <good-image-ref> <bad-image-ref>" >&2; exit 1; }
GOOD="$1"
BAD="$2"

# The service sits in a compose profile so bulk deploys ignore it; the profile
# has to be on for every `docker compose` call below and in the deploy script.
export COMPOSE_PROFILES="rollback-rehearsal"

cd "$KLAI_DIR" || exit 1
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fail() { echo "::error::rollback rehearsal: $*" >&2; exit 1; }

# Runs the deploy script for one image; leaves its exit code in DEPLOY_RC and
# its output in $WORK/<name>.out (tee keeps it in the Actions log too).
deploy() {
    local image="$1" name="$2"
    "$DEPLOY" "$SERVICE" "$image" --compose-tag "$COMPOSE_TAG" --probe "$PROBE_URL" --probe-network "$PROBE_NETWORK" 2>&1 \
        | tee "$WORK/$name.out"
    DEPLOY_RC="${PIPESTATUS[0]}"
}

echo "==> 1/3 good image $GOOD must deploy"
deploy "$GOOD" good
(( DEPLOY_RC == 0 )) || fail "the good image $GOOD did not deploy (exit $DEPLOY_RC); the canary or the deploy script is broken, not the rollback"

echo "==> 2/3 bad image $BAD must be rolled back"
deploy "$BAD" bad
case "$DEPLOY_RC" in
    3) ;;
    0) fail "the rollback path did not roll back: the bad image $BAD deployed as healthy" ;;
    4) fail "the rollback failed (outcome rollback_failed): the canary may still run $BAD" ;;
    *) fail "the bad image deploy ended with unexpected exit $DEPLOY_RC (expected 3)" ;;
esac
grep '^ROLLBACK_REPORT ' "$WORK/bad.out" | grep -q '"outcome":"rolled_back"' \
    || fail "exit 3 but no ROLLBACK_REPORT line with outcome rolled_back"

echo "==> 3/3 the canary must run $GOOD again and answer within ${WAIT}s"
good_id="$("$DOCKER" image inspect --format '{{.Id}}' "$GOOD" 2>/dev/null)" \
    || fail "cannot inspect the good image $GOOD"
deadline=$(( SECONDS + WAIT ))
while :; do
    cid="$("$DOCKER" compose ps -q "$SERVICE" 2>/dev/null | head -n1)"
    running_id=""
    [[ -z "$cid" ]] || running_id="$("$DOCKER" inspect --format '{{.Image}}' "$cid" 2>/dev/null || true)"
    if [[ -n "$running_id" && "$running_id" == "$good_id" ]]; then
        code="$("$DOCKER" run --rm --network "$PROBE_NETWORK" "$CURL_IMAGE" \
            -s -o /dev/null -w '%{http_code}' --max-time 5 "$PROBE_URL" 2>/dev/null)" || code="000"
        echo "canary runs the good image; probe $PROBE_URL -> HTTP $code"
        [[ "$code" =~ ^2[0-9][0-9]$ ]] && break
    else
        echo "canary container ${cid:-none} runs ${running_id:-nothing}, wanted $good_id"
    fi
    (( SECONDS + INTERVAL < deadline )) \
        || fail "after ${WAIT}s the canary does not run the good image and answer its probe"
    sleep "$INTERVAL"
done

ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
event="$(printf '{"service":"auto-rollback","level":"info","event":"rollback_rehearsal","outcome":"ok","deploy_service":"%s","good":"%s","bad":"%s","_time":"%s"}' \
    "$SERVICE" "$GOOD" "$BAD" "$ts")"
mkdir -p "$(dirname "$EVENT_LOG")" && printf '%s\n' "$event" >> "$EVENT_LOG" \
    || fail "could not append the rollback_rehearsal event to $EVENT_LOG; the dead-man's switch would fire"
echo "$event"
echo "==> rehearsal ok"
