#!/usr/bin/env bash
# Start a throwaway retrieval-api with the code of a checkout, beside the live one.
#
# For measuring a retrieval change before it merges (meetlat retrieval, docs/runbooks/meetlat.md):
# the container is a clone of klai-core-retrieval-api-1 (image, environment, networks) with the
# checkout's retrieval_api package mounted over the image's, started through
# retrieval-api-oneoff-launch.py, which opens its Postgres pool read-only and skips the two
# writes a /retrieve call makes. The live service is not touched and no port is published: a
# portal-api one-off reaches it by name on the shared network, e.g.
#   env KNOWLEDGE_RETRIEVE_URL=http://<name>:8040 python scripts/meetlat.py retrieval ...
#
# Usage: retrieval-api-oneoff.sh --name NAME --code DIR [--env KEY=VALUE]...
#   DIR holds the retrieval_api package (klai-retrieval-api/ of the checkout).
# The container runs with --rm, so stopping it also removes it: docker stop NAME
# NAME may not be the live container's name or one of its network aliases (retrieval-api):
# callers reach the live service by that name, and a clone answering to it would take their traffic.

set -euo pipefail

LIVE_CONTAINER="${KLAI_RETRIEVAL_CONTAINER:-klai-core-retrieval-api-1}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    echo "Usage: $0 --name NAME --code DIR [--env KEY=VALUE]..." >&2
    exit 1
}

name=""
code=""
overrides=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --name) [[ $# -ge 2 ]] || usage; name="$2"; shift 2 ;;
        --code) [[ $# -ge 2 ]] || usage; code="$2"; shift 2 ;;
        --env) [[ $# -ge 2 && "$2" == *=* ]] || usage; overrides+=("$2"); shift 2 ;;
        *) usage ;;
    esac
done
[[ -n "$name" && -n "$code" ]] || usage
[[ -d "$code/retrieval_api" ]] || { echo "ERROR: $code has no retrieval_api package." >&2; exit 1; }
code="$(cd "$code" && pwd)"

if [[ "$(docker inspect --format '{{.State.Running}}' "$LIVE_CONTAINER" 2>/dev/null || echo false)" != "true" ]]; then
    echo "ERROR: $LIVE_CONTAINER is not running; refusing to clone a stopped service." >&2
    exit 1
fi

live_names="$LIVE_CONTAINER
retrieval-api
$(docker inspect --format '{{range .NetworkSettings.Networks}}{{range .Aliases}}{{println .}}{{end}}{{end}}' "$LIVE_CONTAINER")"
if grep -qxF -- "$name" <<< "$live_names"; then
    echo "ERROR: --name $name is a name of the live service; pick another." >&2
    exit 1
fi

env_file="$(mktemp)"
chmod 600 "$env_file"
trap 'rm -f "$env_file"' EXIT

# Error-tracker variables out, so a failing one-off pages nobody; an override replaces the live value.
override_keys="$(printf '%s\n' "${overrides[@]:-}" | cut -d= -f1 | paste -sd'|' -)"
docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$LIVE_CONTAINER" \
    | grep -Ev '^(SENTRY_DSN|GLITCHTIP[A-Z_]*)=' \
    | { if [[ -n "$override_keys" ]]; then grep -Ev "^(${override_keys})="; else cat; fi; } \
    > "$env_file"
printf '%s\n' "${overrides[@]:-}" | grep . >> "$env_file" || true

image="$(docker inspect --format '{{.Image}}' "$LIVE_CONTAINER")"
workdir="$(docker inspect --format '{{.Config.WorkingDir}}' "$LIVE_CONTAINER")"
networks=()
while IFS= read -r net; do
    [[ -n "$net" ]] && networks+=("$net")
done < <(docker inspect --format '{{range $net, $cfg := .NetworkSettings.Networks}}{{println $net}}{{end}}' "$LIVE_CONTAINER")

docker create --rm \
    --name "$name" \
    --label klai.oneoff=true \
    --env-file "$env_file" \
    --network "${networks[0]}" \
    --mount "type=bind,source=${code}/retrieval_api,destination=${workdir}/retrieval_api,readonly" \
    --mount "type=bind,source=${here}/retrieval-api-oneoff-launch.py,destination=/launch.py,readonly" \
    -w "$workdir" \
    -e "PYTHONPATH=${workdir}" \
    --entrypoint "" \
    "$image" python /launch.py > /dev/null
for net in "${networks[@]:1}"; do
    docker network connect "$net" "$name"
done
docker start "$name" > /dev/null

tries="${KLAI_ONEOFF_HEALTH_TRIES:-60}"
for _ in $(seq 1 "$tries"); do
    if docker exec "$name" python -c "import urllib.request as u; u.urlopen('http://localhost:8040/health', timeout=2)" 2> /dev/null; then
        echo "retrieval-api one-off: http://${name}:8040 (stop and remove with: docker stop ${name})"
        exit 0
    fi
    sleep 1
done
docker logs --tail 20 "$name" >&2 || true
docker rm -f "$name" > /dev/null
echo "ERROR: $name did not answer /health within ${tries} s; it was removed (last log lines above)." >&2
exit 1
