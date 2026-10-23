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

# Every LiteLLM credential in the clone is the measurement key, a virtual key that can call klai-judge and klai-ingest
# only, so nothing run inside it can reach a chat alias: on 23 Oct 2026 measurement rounds on the master key spent the
# chat workspaces' monthly Mistral limit and took chat down for every tenant. There is no flag to skip this.
MEASUREMENT_KEY_FILE="${KLAI_MEASUREMENT_KEY_FILE:-/opt/klai/state/litellm/measurement-key}"
measurement_key="$(cat "$MEASUREMENT_KEY_FILE" 2>/dev/null || true)"
if [[ -z "$measurement_key" ]]; then
    echo "ERROR: no LiteLLM measurement key in $MEASUREMENT_KEY_FILE; create it with deploy/scripts/litellm-measurement-key.sh." >&2
    exit 1
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

# A measurement key equal to a key the live service holds is that key under another name, so it is refused, and so is
# an environment with no LITELLM_*KEY at all: the variable was renamed and this swap would no longer cover it.
rc=0
swapped="$(MEASUREMENT_KEY="$measurement_key" awk '
    /^LITELLM_[A-Z0-9_]*KEY=/ {
        eq = index($0, "=")
        if (substr($0, eq + 1) == ENVIRON["MEASUREMENT_KEY"]) live = 1
        print substr($0, 1, eq) ENVIRON["MEASUREMENT_KEY"]
        n++
        next
    }
    { print }
    END { exit live ? 2 : (n ? 0 : 3) }' "$env_file")" || rc=$?
case "$rc" in
    0) printf '%s\n' "$swapped" > "$env_file" ;;
    2) echo "ERROR: $MEASUREMENT_KEY_FILE holds a key $PORTAL_CONTAINER uses; recreate it with litellm-measurement-key.sh." >&2; exit 1 ;;
    *) echo "ERROR: $PORTAL_CONTAINER has no LITELLM_*KEY variable to replace with the measurement key." >&2; exit 1 ;;
esac

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
