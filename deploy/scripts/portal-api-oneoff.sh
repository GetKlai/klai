#!/usr/bin/env bash
# Run one command in a throwaway clone of klai-core-portal-api-1, not inside
# the compose service itself.
#
# Long operator scripts (scripts/replay_internal_chat.py and friends) used to
# run via `docker exec` in the live portal-api container. Every merge to main
# redeploys that container (deploy-portal-api.sh recreates it), which kills
# the running script and wipes its output under the container's /tmp. On
# 2026-09-2x this killed a 45-minute, 24-conversation replay twice in one day
# — once at 7/24, once at 12/24 — the second time to an unrelated LiteLLM hook
# deploy restarting klai-core-litellm-1.
#
# This clones the running service's image, environment, networks and mounts
# into a separate, unmanaged container, so a portal-api deploy can no longer
# touch it, and writes output to a host directory that survives the container
# too.
#
# --measurement marks the run as a chat quality round (scripts/meetlat.py run,
# docs/runbooks/meetlat.md). Moving the round out of the service container did
# not move it away from the services it calls: on 22 October 2026 three deploys
# recreated the LiteLLM gateway or portal-api in the middle of a round. A round
# therefore holds a shared lock on /opt/klai/.measurement.lock for as long as
# its container runs; compose-up.sh and tenant-config-regenerate.sh ask for the
# same file exclusively and wait up to 20 minutes for running rounds before
# they recreate anything. Shared, so several rounds can run at once.
#
# Usage: portal-api-oneoff.sh [--measurement] [--out DIR] -- COMMAND [ARGS...]

set -euo pipefail

PORTAL_CONTAINER="${KLAI_PORTAL_CONTAINER:-klai-core-portal-api-1}"

usage() {
    echo "Usage: $0 [--measurement] [--out DIR] -- COMMAND [ARGS...]" >&2
    exit 1
}

out_dir=""
measurement=no
while [[ $# -gt 0 ]]; do
    case "$1" in
        --measurement)
            measurement=yes
            shift
            ;;
        --out)
            [[ $# -ge 2 ]] || usage
            out_dir="$2"
            shift 2
            ;;
        --)
            shift
            break
            ;;
        *)
            usage
            ;;
    esac
done
[[ $# -gt 0 ]] || usage

# Taken before the service is inspected: a round that waited for a deploy has to
# clone the container that deploy left behind, not the one it replaced. The
# descriptor stays open in this shell and in `docker run` below, so the lock is
# held until the container has exited. Without flock a round would run
# unannounced, so a missing binary is an error, as in the deploy scripts. The
# file is created and opened the way the deploys do it (deploy-locks.sh).
if [[ "$measurement" == yes ]]; then
    # shellcheck source-path=SCRIPTDIR source=deploy-locks.sh
    source "$(dirname "${BASH_SOURCE[0]}")/deploy-locks.sh"
    require_flock
    open_measurement_lock
    if ! flock -s -n 8; then
        echo "A deploy holds $MEASUREMENT_LOCK; the round starts once it is done."
        flock -s 8
    fi
fi

if [[ "$(docker inspect --format '{{.State.Running}}' "$PORTAL_CONTAINER" 2>/dev/null || echo false)" != "true" ]]; then
    echo "ERROR: $PORTAL_CONTAINER is not running; refusing to clone a stopped service." >&2
    exit 1
fi

timestamp="$(date +%Y%m%d%H%M%S)"
name="klai-oneoff-${timestamp}"
out_dir="${out_dir:-${KLAI_ONEOFF_OUT:-/opt/klai/oneoff/${timestamp}}}"
# --mount source= requires an absolute path; a relative --out otherwise fails
# inside `docker run`, not here, which is a worse place to find out.
[[ "$out_dir" == /* ]] || out_dir="$PWD/$out_dir"
mkdir -p -m 700 "$out_dir"

image="$(docker inspect --format '{{.Image}}' "$PORTAL_CONTAINER")"

# The image runs as a non-root user (klai), so a 0700 dir created by whoever
# invoked this script (typically root) is unwritable from inside the
# container. Ask the live container for its uid, which is the clone's uid too
# since it comes from the same image. (`docker top -o uid` was tried first and
# fails on the host with "Couldn't find PID field in ps output".)
container_uid="$(docker exec "$PORTAL_CONTAINER" id -u)"
chown "$container_uid" "$out_dir"

env_file="$(mktemp)"
chmod 600 "$env_file"
cleanup() {
    rm -f "$env_file"
}
trap cleanup EXIT

# A one-off's crash is not the live service's crash: dropping any Sentry- or
# GlitchTip-named variable keeps a failing one-off from paging anyone.
docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$PORTAL_CONTAINER" \
    | grep -Ev '^(SENTRY_DSN|GLITCHTIP[A-Z_]*)=' \
    > "$env_file"

network_args=()
while IFS= read -r net; do
    [[ -n "$net" ]] || continue
    network_args+=(--network "$net")
done < <(docker inspect --format '{{range $net, $cfg := .NetworkSettings.Networks}}{{println $net}}{{end}}' "$PORTAL_CONTAINER")

mount_args=()
while IFS='|' read -r mount_type source destination rw; do
    [[ "$mount_type" == "bind" ]] || continue
    mount_opt="type=bind,source=${source},destination=${destination}"
    [[ "$rw" == "false" ]] && mount_opt="${mount_opt},readonly"
    mount_args+=(--mount "$mount_opt")
done < <(docker inspect --format '{{range .Mounts}}{{.Type}}|{{.Source}}|{{.Destination}}|{{.RW}}{{println}}{{end}}' "$PORTAL_CONTAINER")

echo "container: $name"
echo "output dir: $out_dir"

docker run --rm \
    --name "$name" \
    --label klai.oneoff=true \
    --env-file "$env_file" \
    "${network_args[@]}" \
    "${mount_args[@]}" \
    --mount "type=bind,source=${out_dir},destination=/out" \
    -w /repo/klai-portal/backend \
    --entrypoint "" \
    "$image" "$@"
