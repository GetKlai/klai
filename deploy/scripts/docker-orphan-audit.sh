#!/usr/bin/env bash
# /opt/klai/scripts/docker-orphan-audit.sh
#
# Weekly orphan audit emitting structlog-events to stdout (Alloy →
# VictoriaLogs). Detects six categories of "wees" state:
#
#   1. orphan_no_managed_label
#      Running container without klasse-A (compose-project=klai-core)
#      OR klasse-B (klai.managed_by=portal-api-provisioning) OR
#      klasse-C (runtime.managed=true on a vexaai/* image) OR
#      klai.adhoc=* opt-in label.
#
#   2. orphan_service_removed
#      Compose-managed (klasse A) container whose service name is no
#      longer in /opt/klai/docker-compose.yml — stale runner.
#
#   3. image_untagged_old
#      Untagged image older than 30 days, not referenced by any
#      running container — safe to prune by daily timer (REQ-6) but
#      logged here for visibility.
#
#   4. volume_unmounted
#      Named volume with no current container mount, with last-write
#      timestamp > 7 days. May contain klantdata; never auto-prune.
#
#   5. caddy_upstream_missing
#      Caddy upstream in /opt/klai/Caddyfile that does NOT match any
#      running container — broken routing-rule.
#
#   6. tenant_container_no_route
#      Container with klasse-B label OR tenant-pattern-name (`librechat-*`)
#      that has no Caddy upstream pointing at it — exact librechat-voys
#      detection signal.
#
# REPORT-ONLY. Never deletes anything. Output to stdout only —
# /var/log/klai-orphan-audit/ is NOT used per ops-events-go-to-VictoriaLogs
# convention (.claude/rules/klai/infra/observability.md).
#
# SPEC-INFRA-CONTAINER-HYGIENE-001 REQ-5.

set -euo pipefail

TIMESTAMP="$(date -Iseconds)"
COMPOSE_FILE="${COMPOSE_FILE:-/opt/klai/docker-compose.yml}"
OVERRIDE_FILE="${OVERRIDE_FILE:-/opt/klai/docker-compose.override.yml}"
DEV_FILE="${DEV_FILE:-/opt/klai/docker-compose.dev.yml}"
CADDYFILE="${CADDYFILE:-/opt/klai/Caddyfile}"

# Use a tempfile to count events across subshells (while|read pipelines)
EVENT_TMP="$(mktemp)"
trap 'rm -f "$EVENT_TMP"' EXIT
echo 0 > "$EVENT_TMP"

emit_event() {
    local event_type="$1"
    local severity="$2"
    local container_name="$3"
    local extra_json="${4:-{\}}"
    printf '{"service":"klai-orphan-audit","level":"%s","event":"%s","container_name":"%s","extra":%s,"_time":"%s"}\n' \
        "$severity" "$event_type" "$container_name" "$extra_json" "$TIMESTAMP"
    # Count via tempfile so subshells contribute
    local cur
    cur=$(cat "$EVENT_TMP")
    echo $((cur + 1)) > "$EVENT_TMP"
}

# ─── Build compose-services inventory ────────────────────────────────────────
# Includes main + override + dev compose-files. Service names live under
# `services:` section, indented 2 spaces. Naive grep is sufficient because
# this is yaml without nested service blocks.
extract_services() {
    local f="$1"
    [[ -f "$f" ]] || return 0
    awk '
        /^services:[[:space:]]*$/ { in_svc=1; next }
        /^[a-zA-Z]/ && in_svc { in_svc=0 }
        in_svc && /^  [a-zA-Z][a-zA-Z0-9_-]*:[[:space:]]*$/ {
            gsub(/^  |:.*$/, "")
            print
        }
    ' "$f"
}

COMPOSE_SERVICES="$(
    {
        extract_services "$COMPOSE_FILE"
        extract_services "$OVERRIDE_FILE"
        extract_services "$DEV_FILE"
    } | sort -u
)"

is_in_compose() {
    local svc="$1"
    grep -qxF "$svc" <<< "$COMPOSE_SERVICES"
}

# ─── Detection 1+2+6: per running container ──────────────────────────────────
# Caddy upstream-name normalisation: Caddyfile entries use service-names
# (e.g. `reverse_proxy portal-api:8000`), NOT compose container-names
# (which are prefixed `klai-core-portal-api-1`). Build a service→container
# map for detection 6's reverse-lookup.
while read -r name; do
    [[ -z "$name" ]] && continue

    proj=$(docker inspect "$name" --format '{{index .Config.Labels "com.docker.compose.project"}}' 2>/dev/null || echo "")
    svc=$(docker inspect "$name" --format '{{index .Config.Labels "com.docker.compose.service"}}' 2>/dev/null || echo "")
    managed_by=$(docker inspect "$name" --format '{{index .Config.Labels "klai.managed_by"}}' 2>/dev/null || echo "")
    tenant=$(docker inspect "$name" --format '{{index .Config.Labels "klai.tenant_slug"}}' 2>/dev/null || echo "")
    adhoc=$(docker inspect "$name" --format '{{index .Config.Labels "klai.adhoc"}}' 2>/dev/null || echo "")
    image=$(docker inspect "$name" --format '{{.Config.Image}}' 2>/dev/null || echo "unknown")

    # Klasse C — per-meeting Vexa bot workloads.
    #
    # vexa12-runtime creates one container per meeting (vexa-mtg-<id>) through
    # klai-docker-authz. They carry upstream's labels, not ours, so before this
    # they read as klasse-A-and-B-less: exactly the shape that got librechat-voys
    # deleted. They are legitimate and short-lived — AutoRemove is false, and the
    # daily docker-cleanup.timer reaps them once exited (REQ-6, until=24h).
    #
    # Both the label and the image namespace must match. `runtime.managed` is
    # upstream's generic name and far more collidable than our own klai.* labels,
    # so a container claiming the class from a non-vexaai image falls through to
    # orphan_no_managed_label rather than being waved past.
    runtime_managed=$(docker inspect "$name" --format '{{index .Config.Labels "runtime.managed"}}' 2>/dev/null || echo "")
    klasse_c=""
    if [[ "$runtime_managed" == "true" ]] && [[ "$image" == vexaai/* ]]; then
        klasse_c="yes"
    fi

    # Detection 1: no managed label
    if [[ "$proj" != "klai-core" ]] && [[ "$managed_by" != "portal-api-provisioning" ]] && [[ -z "$klasse_c" ]] && [[ -z "$adhoc" ]]; then
        emit_event "orphan_no_managed_label" "warning" "$name" \
            "{\"image\":\"$image\"}"
        continue
    fi

    # Detection 2: klasse-A but service-name not in compose
    if [[ "$proj" == "klai-core" ]] && [[ -n "$svc" ]] && ! is_in_compose "$svc"; then
        emit_event "orphan_service_removed" "warning" "$name" \
            "{\"image\":\"$image\",\"compose_service\":\"$svc\"}"
    fi

    # Detection 6: klasse-B or librechat-* container without Caddy upstream.
    # Caddy refers to klasse-B containers by container-name (e.g.
    # `reverse_proxy librechat-voys:3080`), so direct grep works for them.
    if [[ "$managed_by" == "portal-api-provisioning" ]] || [[ "$name" =~ ^librechat- ]]; then
        if [[ -f "$CADDYFILE" ]] && ! grep -qE "(^|[[:space:]])${name}([[:space:]]|:|$)" "$CADDYFILE"; then
            emit_event "tenant_container_no_route" "warning" "$name" \
                "{\"image\":\"$image\",\"tenant_slug\":\"$tenant\"}"
        fi
    fi
done < <(docker ps --format '{{.Names}}')

# ─── Detection 3: untagged images >30d, not in use ───────────────────────────
THIRTY_DAYS_AGO=$(date -d '30 days ago' +%s 2>/dev/null || date -v-30d +%s)
docker images --filter dangling=true --format '{{.ID}} {{.CreatedAt}}' | while read -r img_id created_str; do
    [[ -z "$img_id" ]] && continue
    # Created at format is "YYYY-MM-DD HH:MM:SS +ZZZZ ZONE" — convert
    img_ts=$(date -d "$(echo "$created_str" | awk '{print $1, $2}')" +%s 2>/dev/null || echo 0)
    [[ "$img_ts" -lt "$THIRTY_DAYS_AGO" ]] || continue
    # Skip if any container references it
    if docker ps -a --filter ancestor="$img_id" --format '{{.ID}}' | grep -q .; then
        continue
    fi
    emit_event "image_untagged_old" "info" "$img_id" \
        "{\"created\":\"$created_str\",\"age_days\":\"30+\"}"
done

# ─── Detection 4: dangling volumes >7d last-write ────────────────────────────
SEVEN_DAYS_AGO=$(date -d '7 days ago' +%s 2>/dev/null || date -v-7d +%s)
docker volume ls -f dangling=true -q | while read -r vol; do
    [[ -z "$vol" ]] && continue
    mountpoint=$(docker volume inspect "$vol" --format '{{.Mountpoint}}' 2>/dev/null || echo "")
    [[ -z "$mountpoint" ]] && continue
    # Find newest mtime in volume; if none / older than 7d, flag.
    # `find -printf %T@` gives epoch seconds for the most-recent file.
    newest_mtime=$(sudo find "$mountpoint" -type f -printf '%T@\n' 2>/dev/null | sort -n | tail -1 || echo 0)
    newest_int=${newest_mtime%.*}
    [[ -z "$newest_int" ]] && newest_int=0
    if [[ "$newest_int" -lt "$SEVEN_DAYS_AGO" ]]; then
        emit_event "volume_unmounted" "warning" "$vol" \
            "{\"mountpoint\":\"$mountpoint\",\"newest_mtime\":\"$newest_int\"}"
    fi
done

# ─── Detection 5: Caddy upstreams without matching container ─────────────────
# Caddy upstreams reach docker services by their compose-service-name, not
# the prefixed container-name. Build a service-name → running set and a
# normalised running-name set to allow either form.
if [[ -f "$CADDYFILE" ]]; then
    # Set 1: full container-names (klai-core-portal-api-1, librechat-voys, etc.)
    RUNNING_NAMES=$(docker ps --format '{{.Names}}')
    # Set 2: compose-service-names (portal-api, redis, etc.)
    RUNNING_SERVICES=$(docker ps --format '{{.Label "com.docker.compose.service"}}' | grep -v '^$' | sort -u)

    # Whitelist: hostnames that are valid by convention (Vexa internal,
    # localhost, named matchers, snippet-references, special caddy tokens).
    is_caddy_whitelist() {
        local h="$1"
        case "$h" in
            localhost|127.0.0.1) return 0 ;;
            api-gateway|admin-api|meeting-api|runtime-api|transcription-api) return 0 ;;
            h2c|http|https|h2|h3) return 0 ;;
        esac
        # Caddy named matchers start with @
        [[ "$h" == @* ]] && return 0
        # Caddy placeholders / variables
        [[ "$h" == \{* ]] && return 0
        return 1
    }

    grep -hE '^\s*reverse_proxy\s+' "$CADDYFILE" \
        | awk '{
            # reverse_proxy may have named matcher as first arg (@matcher)
            # then upstream(s). Iterate from $2 onward, filter out matchers
            # and flags.
            for (i=2;i<=NF;i++) {
                t = $i
                # skip flags and matchers
                if (t ~ /^@/) continue
                if (t ~ /^-/) continue
                # strip path and port
                sub(/\/.*$/, "", t)
                sub(/:.*$/, "", t)
                if (length(t) > 0) print t
            }
        }' \
        | sort -u \
        | while read -r host; do
            [[ -z "$host" ]] && continue
            is_caddy_whitelist "$host" && continue
            # Match against either container-name OR compose-service-name
            if grep -qxF "$host" <<< "$RUNNING_NAMES"; then continue; fi
            if grep -qxF "$host" <<< "$RUNNING_SERVICES"; then continue; fi
            emit_event "caddy_upstream_missing" "critical" "$host" \
                "{\"upstream_in_caddyfile\":\"$host\"}"
        done
fi

# ─── Always emit run-completed marker ────────────────────────────────────────
EVENT_COUNT=$(cat "$EVENT_TMP")
printf '{"service":"klai-orphan-audit","level":"info","event":"audit_run_completed","total_events":%d,"_time":"%s"}\n' \
    "$EVENT_COUNT" "$TIMESTAMP"
