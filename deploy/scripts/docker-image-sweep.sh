#!/usr/bin/env bash
# /opt/klai/scripts/docker-image-sweep.sh
#
# Remove the images no container uses, except what a rollback or a local
# build still needs. docker-cleanup.service runs it daily after the dangling
# prune; it can be run by hand at any time.
#
# Why it exists: every deploy leaves a tagged image behind, and tagged images
# are never dangling, so the daily `docker image prune` never touches them.
# With every update automerged (AGENTS.md, 2026-10-29) that is dozens of
# multi-GB images a day: on 2026-10-29 16:33 UTC core-01 hit 100% disk with
# 221 unused images (235 GB reclaimable per `docker system df`) and the
# compose sync refused its data-store snapshots for lack of headroom.
#
# Kept, in this order of checks:
#   - any image a container uses (running or exited);
#   - every klai/* tag: `:previous` is what deploy-with-rollback.sh rolls
#     back to, the rest are local builds and the rollback canary;
#   - ghcr.io/getklai/crawl4ai: built on this host, not pullable;
#   - the tool images the helpers run on the fly (curlimages/curl for the
#     VictoriaLogs and store probes, alpine for tar and cp, busybox): no
#     container holds them between runs, and the first night (2026-10-30
#     03:00 UTC) swept curl 8.11.1 and alpine 3.22 away, so a rollback would
#     have started with a registry pull;
#   - every tag named in the current or the previous compose file
#     (/opt/klai/docker-compose.yml and .yml.prev): a compose rollback
#     restarts the previous tag of a data store;
#   - anything younger than KLAI_IMAGE_SWEEP_MIN_AGE_HOURS (default 24):
#     the E2E-triggered replay restores the ledger's previous image ids of
#     the day, and a pull is cheaper than a failed rollback.
# Everything else is removed one tag at a time; a layer shared with a kept
# image stays. Exit 0 even when a removal fails (printed), so the rest of the
# cleanup unit still runs.
#
# Tests: deploy/scripts/tests/docker-image-sweep.test.sh
set -uo pipefail

DOCKER="${KLAI_DOCKER_BIN:-docker}"
COMPOSE_DIR="${KLAI_COMPOSE_DIR:-/opt/klai}"
MIN_AGE_HOURS="${KLAI_IMAGE_SWEEP_MIN_AGE_HOURS:-24}"

# `docker images` prints "2026-10-29 18:00:31 +0200 EET"; GNU date refuses the
# trailing zone name next to the offset, so the last word goes.
to_epoch() {
    local c="${1% *}"
    date -u -d "$c" +%s 2>/dev/null || date -u -j -f '%Y-%m-%d %H:%M:%S %z' "$c" +%s 2>/dev/null
}

used="$("$DOCKER" ps -aq | xargs -r "$DOCKER" inspect -f '{{.Image}}' 2>/dev/null | sort -u)"
compose_tags="$(grep -hoE '^[[:space:]]*image:[[:space:]]*[^[:space:]#]+' "$COMPOSE_DIR/docker-compose.yml" "$COMPOSE_DIR/docker-compose.yml.prev" 2>/dev/null \
    | sed -E 's/^[[:space:]]*image:[[:space:]]*//' | sort -u)"
now="$(date -u +%s)"
removed=0 kept=0 failed=0

while IFS=$'\t' read -r id ref digest created; do
    [[ -n "$id" ]] || continue
    reason=""
    if grep -qxF "$id" <<< "$used"; then reason="in use"
    elif [[ "$ref" == klai/* ]]; then reason="klai tag"
    elif [[ "$ref" == ghcr.io/getklai/crawl4ai:* ]]; then reason="local build"
    elif [[ "$ref" == curlimages/curl:* || "$ref" == alpine:* || "$ref" == busybox:* ]]; then reason="tool image"
    elif grep -qxF "$ref" <<< "$compose_tags"; then reason="in a compose file"
    else
        epoch="$(to_epoch "$created")"
        if [[ "$epoch" =~ ^[0-9]+$ ]] && (( now - epoch < MIN_AGE_HOURS * 3600 )); then reason="younger than ${MIN_AGE_HOURS}h"; fi
    fi
    if [[ -n "$reason" ]]; then
        kept=$(( kept + 1 ))
        continue
    fi
    # An image pulled by digest (LibreChat, presidio) lists as repo:<none>,
    # which no docker command accepts; its repo@digest form is removable.
    [[ "$ref" != *:'<none>' ]] || ref="${ref%:<none>}@$digest"
    if "$DOCKER" image rm "$ref" > /dev/null 2>&1; then
        echo "removed $ref"
        removed=$(( removed + 1 ))
    else
        echo "could not remove $ref" >&2
        failed=$(( failed + 1 ))
    fi
done < <("$DOCKER" images --no-trunc --digests --format '{{.ID}}\t{{.Repository}}:{{.Tag}}\t{{.Digest}}\t{{.CreatedAt}}' \
    | awk -F'\t' '$2 != "<none>:<none>"')

echo "image sweep: removed $removed, kept $kept, failed $failed"
