#!/usr/bin/env bash
# deploy/scripts/deploy-locks.sh: the two host locks every container deploy on
# core-01 takes, sourced (not run) by compose-up.sh, tenant-config-regenerate.sh,
# deploy-portal-api.sh, portal-api-oneoff.sh and the deploy-compose.yml sync step.
#
# The measurement lock (/opt/klai/.measurement.lock). Chat quality rounds
# (portal-api-oneoff.sh --measurement) hold it shared for as long as their
# container runs; on 22 October 2026 three deploys recreated a service in the
# middle of a 25 to 45 minute round and cost it its turns. A deploy asks for it
# exclusively, waits at most KLAI_MEASUREMENT_WAIT_SECONDS (20 minutes) because
# a hotfix must not sit behind a measurement for an hour, and then goes ahead
# with a warning.
#
# The container lock (/opt/klai/.container-deploy.lock). One container deploy
# at a time: two workflows recreated klai-docker-authz in the same second on
# 21 October 2026 and compose exited on "removal of container is already in
# progress". Waits at most 15 minutes, then fails.
#
# Always the measurement lock first, then the container lock, so two deploys
# never each hold the lock the other waits for. Each function exports
# KLAI_MEASUREMENT_LOCK_HELD=1 or KLAI_CONTAINER_LOCK_HELD=1, and skips when it
# is already set: a step or script that took a lock once hands it to every
# script it runs, so a deploy-compose sync that calls compose-up.sh twice waits
# for a round once, and deploy-portal-api.sh migrates and recreates inside one
# hold. The descriptors (8 and 9) are inherited by those children, so the
# locks last until the outermost holder exits.
#
# Without flock a deploy would run unlocked, so a missing binary is an error,
# not a skip. The test suites put a stub flock on PATH.

KLAI_LOCK_DIR="${KLAI_COMPOSE_DIR:-/opt/klai}"
MEASUREMENT_LOCK="${KLAI_MEASUREMENT_LOCK:-$KLAI_LOCK_DIR/.measurement.lock}"
CONTAINER_LOCK="${KLAI_CONTAINER_LOCK:-$KLAI_LOCK_DIR/.container-deploy.lock}"

require_flock() {
    if ! command -v flock >/dev/null 2>&1; then
        echo "ERROR: flock not found; refusing to run $(basename "$0") without the host locks" >&2
        exit 1
    fi
}

# A round usually runs as root and a deploy as klai, so the file is created
# world-readable and opened read-only, which is enough for flock(2). noclobber
# makes the create an O_EXCL open: two starters that both find the file missing
# cannot end up locking two different inodes, and an existing file (possibly
# another user's) is never truncated or replaced.
open_measurement_lock() {
    if [[ ! -e "$MEASUREMENT_LOCK" ]] && (set -o noclobber; : >"$MEASUREMENT_LOCK") 2>/dev/null; then
        chmod 644 "$MEASUREMENT_LOCK"
    fi
    exec 8<"$MEASUREMENT_LOCK"
}

# $1: what goes ahead when the wait runs out ("deploying", "regenerating").
take_measurement_lock() {
    [[ "${KLAI_MEASUREMENT_LOCK_HELD:-}" == 1 ]] && return 0
    local wait="${KLAI_MEASUREMENT_WAIT_SECONDS:-1200}" waited=0 step
    open_measurement_lock
    if ! flock -x -n 8; then
        while (( waited < wait )); do
            echo "Waiting for a measurement round (or another deploy) to finish: $MEASUREMENT_LOCK is held (${waited} of ${wait} s)"
            step=$(( wait - waited < 60 ? wait - waited : 60 ))
            if flock -x -w "$step" 8; then break; fi
            waited=$(( waited + step ))
        done
        if (( waited >= wait )); then
            echo "::warning::$MEASUREMENT_LOCK still held after ${wait} s; $1 anyway, a running measurement round will see this restart"
        fi
    fi
    # Also after a wait that ran out: the children must not wait a second time.
    export KLAI_MEASUREMENT_LOCK_HELD=1
}

# $1: what is refused when another deploy holds the lock too long ("starting").
take_container_lock() {
    [[ "${KLAI_CONTAINER_LOCK_HELD:-}" == 1 ]] && return 0
    exec 9>"$CONTAINER_LOCK"
    if ! flock -w "${KLAI_LOCK_WAIT_SECONDS:-900}" 9; then
        echo "ERROR: another container deploy has held $CONTAINER_LOCK for 15 minutes; not $1" >&2
        exit 1
    fi
    export KLAI_CONTAINER_LOCK_HELD=1
}
