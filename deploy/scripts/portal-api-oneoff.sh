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
# Usage: portal-api-oneoff.sh [--out DIR] -- COMMAND [ARGS...]

set -euo pipefail

PORTAL_CONTAINER="${KLAI_PORTAL_CONTAINER:-klai-core-portal-api-1}"

usage() {
    echo "Usage: $0 [--out DIR] -- COMMAND [ARGS...]" >&2
    exit 1
}

out_dir=""
while [[ $# -gt 0 ]]; do
    case "$1" in
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
# container. docker top reads the uid the live process actually runs as,
# which is the clone's uid too since it comes from the same image.
container_uid="$(docker top "$PORTAL_CONTAINER" -o uid | tail -n +2 | head -n1 | tr -d '[:space:]')"
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
