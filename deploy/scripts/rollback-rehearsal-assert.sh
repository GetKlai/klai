#!/usr/bin/env bash
# /opt/klai/scripts/rollback-rehearsal-assert.sh
#
# The weekly proof that automatic rollback works (SPEC-INFRA-AUTO-ROLLBACK-001
# section 8, slice S7). rollback-rehearsal.yml runs this over ssh on core-01.
#
# Usage:
#   rollback-rehearsal-assert.sh <good-image-ref> <bad-image-ref>
#
# It drives the real deploy-with-rollback.sh and rollback-since.sh against the
# rollback-canary service, five steps, each of which must hold:
#   1. the good image deploys                      -> exit 0
#   2. the bad image deploys and is rolled back    -> exit 3 and a report line
#      with outcome rolled_back (exit 0 means the bad image went live, exit 4
#      means the rollback itself failed)
#   3. within KLAI_REHEARSAL_WAIT seconds (default 300) the canary container runs
#      the good image again and answers its HTTP probe
# Scenario 2, the already-red block of auto-rollback.yml: step 2 was a clean
# rollback, and the moment after step 3 stands in for the E2E run that round
# would start, completing red (the evidence).
#   4. the good image deploys again                -> exit 0; its ledger line
#      carries a serving time after the evidence
#   5. rollback-since.sh --rehearse with that evidence as --red-after must end
#      e2e_already_red (exit 5) and leave the canary container as it was.
#      Exit 3 means the round did not block and rolled the canary back.
# Step 5 weighs and replays the canary only (--rehearse) and writes its report
# and log to a scratch directory, so it never touches a real service, never
# reads or writes .e2e-rollback-at, and never overwrites the e2e-rollback.json
# a real round of auto-rollback.yml reads back. The evidence is a timestamp
# this script makes; the GitHub lookup that finds real evidence
# (e2e-rollback-evidence.sh) is not involved, and rehearsal rounds of
# auto-rollback.yml still pass no --red-after.
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
ROLLBACK_SINCE="${KLAI_ROLLBACK_SINCE:-$KLAI_DIR/scripts/rollback-since.sh}"
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
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# Runs the deploy script for one image; leaves its exit code in DEPLOY_RC and
# its output in $WORK/<name>.out (tee keeps it in the Actions log too).
deploy() {
    local image="$1" name="$2"
    "$DEPLOY" "$SERVICE" "$image" --compose-tag "$COMPOSE_TAG" --probe "$PROBE_URL" --probe-network "$PROBE_NETWORK" 2>&1 \
        | tee "$WORK/$name.out"
    DEPLOY_RC="${PIPESTATUS[0]}"
}

echo "==> 1/5 good image $GOOD must deploy"
deploy "$GOOD" good
(( DEPLOY_RC == 0 )) || fail "the good image $GOOD did not deploy (exit $DEPLOY_RC); the canary or the deploy script is broken, not the rollback"

echo "==> 2/5 bad image $BAD must be rolled back"
deploy "$BAD" bad
case "$DEPLOY_RC" in
    3) ;;
    0) fail "the rollback path did not roll back: the bad image $BAD deployed as healthy" ;;
    4) fail "the rollback failed (outcome rollback_failed): the canary may still run $BAD" ;;
    *) fail "the bad image deploy ended with unexpected exit $DEPLOY_RC (expected 3)" ;;
esac
grep '^ROLLBACK_REPORT ' "$WORK/bad.out" | grep -q '"outcome":"rolled_back"' \
    || fail "exit 3 but no ROLLBACK_REPORT line with outcome rolled_back"

echo "==> 3/5 the canary must run $GOOD again and answer within ${WAIT}s"
# The canary has to run the good image's filesystem, not the same image ID.
# A registry tag can be re-pushed with a new index digest (a new attestation
# entry, say) over the same platform manifest and layers: docker then hands the
# tag a new .Id, while compose keys on the platform manifest and rightly leaves
# a container on the old .Id alone. Comparing .Id turned that into a false red
# (runs of 2 and 9 November 2026: busybox:1.37.0 changed from bdf57e528e45 to
# a7dbc319b143, same layers). The rootfs layer list tells good from bad (debian).
layers_of() { "$DOCKER" image inspect --format '{{json .RootFS.Layers}}' "$1" 2>/dev/null; }
good_layers="$(layers_of "$GOOD")" && [[ -n "$good_layers" ]] \
    || fail "cannot inspect the good image $GOOD"
deadline=$(( SECONDS + WAIT ))
while :; do
    cid="$("$DOCKER" compose ps -q "$SERVICE" 2>/dev/null | head -n1)"
    running_id=""
    [[ -z "$cid" ]] || running_id="$("$DOCKER" inspect --format '{{.Image}}' "$cid" 2>/dev/null || true)"
    running_layers=""
    [[ -z "$running_id" ]] || running_layers="$(layers_of "$running_id" || true)"
    if [[ -n "$running_layers" && "$running_layers" == "$good_layers" ]]; then
        code="$("$DOCKER" run --rm --network "$PROBE_NETWORK" "$CURL_IMAGE" \
            -s -o /dev/null -w '%{http_code}' --max-time 5 "$PROBE_URL" 2>/dev/null)" || code="000"
        echo "canary runs the good image; probe $PROBE_URL -> HTTP $code"
        [[ "$code" =~ ^2[0-9][0-9]$ ]] && break
    else
        echo "canary container ${cid:-none} runs ${running_id:-nothing} (layers ${running_layers:-unknown}), wanted layers $good_layers of $GOOD"
    fi
    (( SECONDS + INTERVAL < deadline )) \
        || fail "after ${WAIT}s the canary does not run the good image and answer its probe"
    sleep "$INTERVAL"
done

# Timestamps have whole seconds and the block compares them strictly, so a
# second between the window start, the evidence and the next recreate.
since="$(now)"
sleep 1
evidence="$(now)"
sleep 1
echo "==> 4/5 already red: the check after that clean rollback ended red at $evidence; $GOOD deploys again"
deploy "$GOOD" again
(( DEPLOY_RC == 0 )) || fail "the good image $GOOD did not deploy again (exit $DEPLOY_RC)"
canary_state() {
    local cid
    cid="$("$DOCKER" compose ps -q "$SERVICE" 2>/dev/null | head -n1)"
    printf '%s %s' "$cid" "$([[ -z "$cid" ]] || "$DOCKER" inspect --format '{{.Image}}' "$cid" 2>/dev/null)"
}
before="$(canary_state)"

echo "==> 5/5 a rehearsal round with that evidence must end e2e_already_red and leave the canary alone"
KLAI_DEPLOY_REPORTS="$WORK/reports" "$ROLLBACK_SINCE" "$since" --until "$(now)" --red-after "$evidence" --rehearse 2>&1 \
    | tee "$WORK/blocked.out"
blocked_rc="${PIPESTATUS[0]}"
case "$blocked_rc" in
    5) ;;
    3) fail "the round did not block: with E2E red after a clean rollback and the canary deployed later, it rolled the canary back" ;;
    0) fail "the round found no canary deploy after $since to weigh (exit 0); the serving time or the --rehearse split is broken" ;;
    *) fail "the already-red round ended with unexpected exit $blocked_rc (expected 5)" ;;
esac
grep '^ROLLBACK_REPORT ' "$WORK/blocked.out" | grep -q '"outcome":"e2e_already_red"' \
    || fail "exit 5 but no ROLLBACK_REPORT line with outcome e2e_already_red"
after="$(canary_state)"
[[ "$after" == "$before" ]] || fail "the blocked round changed the canary (container and image '$before' before, '$after' after)"

ts="$(now)"
event="$(printf '{"service":"auto-rollback","level":"info","event":"rollback_rehearsal","outcome":"ok","deploy_service":"%s","good":"%s","bad":"%s","_time":"%s"}' \
    "$SERVICE" "$GOOD" "$BAD" "$ts")"
mkdir -p "$(dirname "$EVENT_LOG")" && printf '%s\n' "$event" >> "$EVENT_LOG" \
    || fail "could not append the rollback_rehearsal event to $EVENT_LOG; the dead-man's switch would fire"
echo "$event"
echo "==> rehearsal ok"
