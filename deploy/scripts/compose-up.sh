#!/usr/bin/env bash
# /opt/klai/scripts/compose-up.sh
#
# Canonical deploy-wrapper for klai-core compose stack.
# Replaces ad-hoc `docker compose up -d <svc>` calls in service-deploy
# workflows with a single shared mechanism that:
#
#   1. Pulls the targeted service image (or all images if no service given)
#   2. Recreates with --remove-orphans so containers no longer in
#      docker-compose.yml are cleaned up automatically (klasse-A only —
#      provisioning-managed klasse-B containers carry their own labels
#      and are NOT touched by --remove-orphans)
#   3. Verifies the container actually came back (targeted deploys only) and
#      makes THAT the verdict rather than compose's exit code
#   4. Emits a post-deploy orphan-snapshot event to VictoriaLogs via
#      audit-orphan-snapshot.sh so detection runs on every deploy
#
# SPEC-INFRA-CONTAINER-HYGIENE-001 REQ-3.
#
# Usage:
#   compose-up.sh                          — pull + up all services
#   compose-up.sh <service-name>           — pull + up single service
#   compose-up.sh --no-deps <svc>          — pull + up without service deps
#   compose-up.sh --force-recreate <svc>   — pull + up with --force-recreate
#                                            (drops Python module cache for
#                                            services whose code lives in
#                                            bind-mounted .py files)
#
# When to use --force-recreate:
#   `docker compose up -d` only recreates a container when the compose
#   DEFINITION changed (volume list, env-vars, image tag). Bind-mount
#   FILE CONTENT changes are invisible to compose. For services that
#   import bind-mounted Python files at module load (e.g. litellm with
#   klai_knowledge.py / klai_context.py / klai_chat_prompts.py
#   / klai_retrieval_telemetry.py / custom_router.py vendored on /app/), a
#   bind-mount-content rsync followed by `up -d` is a no-op: Python keeps
#   the cached module from the previous boot and the new code never runs.
#   --force-recreate forces a fresh container, which drops the cache and
#   reimports from disk. Tracked under
#   `bind-mount-content-vs-python-module-cache` in the process pitfalls.
#
# Exit code, for a targeted deploy, reflects whether the container is running
# after the recreate — not `docker compose`'s own exit code, which disagrees in
# both directions (see the verdict block near the bottom). Without a service
# argument the compose exit code still decides, since there is no single
# container to verify. A non-zero exit from audit-orphan-snapshot.sh is logged
# but never fails the deploy (snapshot is detective, not preventive — REQ-2d).

set -euo pipefail

# Seams for the test-suite (deploy/scripts/tests/compose-up-verify.test.sh).
# In production every one of these keeps its default, so the runtime behaviour
# is unchanged; the point is that the post-recreate verification below can be
# driven without a Docker daemon.
KLAI_DIR="${KLAI_COMPOSE_DIR:-/opt/klai}"
DOCKER="${KLAI_DOCKER_BIN:-docker}"
VERIFY_POLLS="${KLAI_VERIFY_POLLS:-5}"
VERIFY_INTERVAL="${KLAI_VERIFY_INTERVAL:-2}"
# A zero here would skip the status check entirely and make every deploy green.
if (( VERIFY_POLLS < 1 )); then VERIFY_POLLS=1; fi

# Pre-flight: refuse to run if /opt/klai is missing or compose-file absent.
# Better a fail-fast with a clear error than a silent partial deploy.
if [[ ! -f "$KLAI_DIR/docker-compose.yml" ]]; then
    echo "ERROR: $KLAI_DIR/docker-compose.yml not found — was deploy-compose.yml run?" >&2
    exit 2
fi

cd "$KLAI_DIR"

POSTGRES_CONTAINER="${KLAI_POSTGRES_CONTAINER:-klai-core-postgres-1}"
NO_DEPS_FLAG=""
FORCE_RECREATE_FLAG=""
SERVICE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-deps)
            NO_DEPS_FLAG="--no-deps"
            shift
            ;;
        --force-recreate)
            FORCE_RECREATE_FLAG="--force-recreate"
            shift
            ;;
        *)
            if [[ -z "$SERVICE" ]]; then
                SERVICE="$1"
            else
                echo "ERROR: unexpected argument '$1' — usage: compose-up.sh [--no-deps] [--force-recreate] [service]" >&2
                exit 2
            fi
            shift
            ;;
    esac
done

litellm_prisma_migrate_enabled() {
    grep -Eq 'USE_PRISMA_MIGRATE:[[:space:]]*"?True"?' "$KLAI_DIR/docker-compose.yml"
}

check_litellm_prisma_migration_baseline() {
    if ! litellm_prisma_migrate_enabled; then
        return 0
    fi

    echo "Checking LiteLLM Prisma migration baseline before recreate..."
    if ! "$DOCKER" ps --format '{{.Names}}' | grep -qx "$POSTGRES_CONTAINER"; then
        echo "ERROR: $POSTGRES_CONTAINER is not running; refusing to recreate litellm with USE_PRISMA_MIGRATE=True" >&2
        exit 1
    fi

    local migration_table
    if ! migration_table="$(
        "$DOCKER" exec "$POSTGRES_CONTAINER" \
            psql -U litellm -d litellm -v ON_ERROR_STOP=1 -tAc \
            "SELECT to_regclass('public._prisma_migrations')"
    )"; then
        echo "ERROR: cannot inspect LiteLLM _prisma_migrations table; refusing to recreate litellm" >&2
        exit 1
    fi
    migration_table="$(echo "$migration_table" | tr -d '[:space:]')"
    if [[ "$migration_table" != "public._prisma_migrations" && "$migration_table" != "_prisma_migrations" ]]; then
        echo "ERROR: LiteLLM USE_PRISMA_MIGRATE=True but public._prisma_migrations is missing." >&2
        echo "Refusing to recreate litellm; baseline the DB first or remove USE_PRISMA_MIGRATE to rollback." >&2
        exit 1
    fi

    local migration_count
    if ! migration_count="$(
        "$DOCKER" exec "$POSTGRES_CONTAINER" \
            psql -U litellm -d litellm -v ON_ERROR_STOP=1 -tAc \
            "SELECT count(*) FROM public._prisma_migrations"
    )"; then
        echo "ERROR: cannot count LiteLLM Prisma migrations; refusing to recreate litellm" >&2
        exit 1
    fi
    migration_count="$(echo "$migration_count" | tr -d '[:space:]')"
    if ! [[ "$migration_count" =~ ^[0-9]+$ ]] || (( migration_count < 1 )); then
        echo "ERROR: LiteLLM public._prisma_migrations is empty; prisma migrate deploy may fail on a non-empty schema." >&2
        echo "Refusing to recreate litellm; baseline the DB first or remove USE_PRISMA_MIGRATE to rollback." >&2
        exit 1
    fi
    echo "LiteLLM Prisma migration baseline OK ($migration_count applied migrations)"
}

pull_vexa_runtime_images() {
    # Vexa bot containers are spawned by vexa12-runtime from profile/env image refs
    # such as BOT_IMAGE_NAME / BROWSER_IMAGE. They are not compose services, so
    # `docker compose pull` will not fetch them. Missing bot images surface only
    # later as Docker /containers/create 404s when a user starts Scribe.
    local refs
    refs="$(
        grep -oE 'vexaai/[a-z0-9-]+:[A-Za-z0-9._-]+' "$KLAI_DIR/docker-compose.yml" | sort -u
    )"
    if [[ -z "$refs" ]]; then
        return 0
    fi

    echo "Pulling Vexa runtime image refs from docker-compose.yml..."
    while IFS= read -r ref; do
        [[ -n "$ref" ]] || continue
        "$DOCKER" pull "$ref"
    done <<< "$refs"
}

# ---------------------------------------------------------------------------
# Post-recreate verification.
#
# Until 2026-08-14 this script pulled, recreated, and exited. Nothing ever
# checked that the container came back. That asymmetry was visible in the
# codebase: a Caddy*file* change goes through sync_and_recreate in
# deploy-compose.yml, which force-recreates and then polls for `running` and
# fails the workflow if it never gets there. A Caddy *image* change came
# through here and got no check at all — so an image that cannot boot took the
# TLS-terminating edge proxy down while CI stayed green.
#
# What is deliberately NOT done: waiting for a healthcheck to report `healthy`.
# Only 19 of the compose services define one at all, none of the eight
# ghcr.io/getklai/* application services among them, and start_period runs up
# to 120s (cal-com). Blocking every deploy on the slowest starter would trade a
# silent failure for a guaranteed-slow one. `unhealthy` is treated as fatal,
# `starting` is reported and accepted.
# ---------------------------------------------------------------------------

inspect_field() {
    # $1 = container id, $2 = --format template. Empty string when the field or
    # the container is absent, so callers can test with [[ -z ]].
    "$DOCKER" inspect "$1" --format "$2" 2>/dev/null || true
}

ts_key() {
    # Docker emits RFC3339Nano and Go strips trailing zeros from the fraction,
    # so ".5Z" and ".5000001Z" both occur and a plain string compare gets them
    # backwards ('Z' sorts above digits). Pad the fraction to a fixed nine
    # digits and the compare is a time compare for real.
    local ts="$1" base frac
    base="${ts%%.*}"; base="${base%Z}"
    frac=""
    [[ "$ts" == *.* ]] && { frac="${ts#*.}"; frac="${frac%Z}"; }
    printf '%s.%s' "$base" "$(printf '%-9s' "$frac" | tr ' ' '0')"
}

service_container_id() {
    # Newest container carrying this service's compose labels, or empty.
    #
    # -aq, not -q: `compose ps -q` lists RUNNING containers only. Every state
    # this function exists to detect — created-but-never-started, exited on
    # boot, renamed by a half-finished recreate — is invisible to -q, which
    # would make the checks below unreachable in exactly the situations they
    # were written for.
    local service="$1" ids id created best="" best_key=""
    ids="$("$DOCKER" compose ps -aq "$service" 2>/dev/null || true)"
    while IFS= read -r id; do
        [[ -n "$id" ]] || continue
        created="$(ts_key "$(inspect_field "$id" '{{.Created}}')")"
        if [[ -z "$best" || "$created" > "$best_key" ]]; then
            best="$id"; best_key="$created"
        fi
    done <<< "$ids"
    printf '%s' "$best"
}

restore_canonical_name() {
    # When a recreate half-finishes, Docker leaves the container under
    # <12-hex>_<original-name>. That is not cosmetic: VictoriaLogs keys
    # container queries on the name, so the logs land under a name nobody would
    # ever search for. It happened twice to knowledge-ingest on 2026-08-14 and
    # both times had to be renamed by hand.
    local cid="$1" current canonical
    current="$(inspect_field "$cid" '{{.Name}}')"
    current="${current#/}"
    [[ "$current" =~ ^[0-9a-f]{12}_(.+)$ ]] || return 0
    canonical="${BASH_REMATCH[1]}"

    if [[ -n "$("$DOCKER" ps -aq --filter "name=^${canonical}$" 2>/dev/null || true)" ]]; then
        echo "WARN: $current looks like a half-finished recreate, but $canonical is taken — leaving the name alone" >&2
        return 0
    fi
    if "$DOCKER" rename "$current" "$canonical" 2>/dev/null; then
        echo "Renamed $current back to $canonical (half-finished recreate)"
    else
        echo "WARN: could not rename $current back to $canonical" >&2
    fi
}

VERIFIED_CID=""

verify_service_running() {
    # 0 = the service is up and stayed up; 1 = it is not.
    # Sets VERIFIED_CID to the container it judged, so the caller can tell a
    # replacement apart from the survivor of a failed recreate.
    local service="$1" cid status health restarts prev_restarts="" i
    cid="$(service_container_id "$service")"
    VERIFIED_CID="$cid"
    if [[ -z "$cid" ]]; then
        echo "ERROR: no container for $service after recreate" >&2
        return 1
    fi

    restore_canonical_name "$cid"

    # Poll rather than sample once: a crash-looping container reports `running`
    # in the window between restarts, so a single check passes on exactly the
    # failure this exists to catch. A rising RestartCount is the tell.
    for (( i = 1; i <= VERIFY_POLLS; i++ )); do
        status="$(inspect_field "$cid" '{{.State.Status}}')"
        restarts="$(inspect_field "$cid" '{{.RestartCount}}')"

        if [[ -z "$status" ]]; then
            echo "ERROR: cannot inspect $service's container ($cid) — daemon unreachable or container gone" >&2
            return 1
        fi
        if [[ "$status" != "running" ]]; then
            echo "ERROR: $service is '$status' after recreate (expected running)" >&2
            "$DOCKER" logs --tail 30 "$cid" 2>&1 | sed 's/^/    /' >&2 || true
            return 1
        fi
        if [[ -n "$prev_restarts" && -n "$restarts" && "$restarts" != "$prev_restarts" ]]; then
            echo "ERROR: $service restarted during verification ($prev_restarts -> $restarts) — crash loop" >&2
            "$DOCKER" logs --tail 30 "$cid" 2>&1 | sed 's/^/    /' >&2 || true
            return 1
        fi
        prev_restarts="$restarts"
        if (( i < VERIFY_POLLS )); then sleep "$VERIFY_INTERVAL"; fi
    done

    health="$(inspect_field "$cid" '{{if .State.Health}}{{.State.Health.Status}}{{end}}')"
    case "$health" in
        unhealthy)
            echo "ERROR: $service is running but its healthcheck reports unhealthy" >&2
            "$DOCKER" logs --tail 30 "$cid" 2>&1 | sed 's/^/    /' >&2 || true
            return 1
            ;;
        starting)
            echo "$service is running (healthcheck still starting — accepted, not waited on)"
            ;;
        healthy)
            echo "$service is running and healthy"
            ;;
        *)
            echo "$service is running (no healthcheck defined)"
            ;;
    esac
    return 0
}

if [[ "$SERVICE" == "litellm" ]]; then
    check_litellm_prisma_migration_baseline
fi

# SPEC-VEXA-004 renamed the services; a targeted deploy of the new names used to
# skip the pre-pull entirely, so a bot image absent from the host surfaced only as
# a /containers/create 404 on the next real meeting. docker-socket-proxy has IMAGES
# disabled, so the runtime cannot recover by pulling it itself.
if [[ -z "$SERVICE" || "$SERVICE" == "vexa12-runtime" || "$SERVICE" == "vexa12-meeting-api" ]]; then
    pull_vexa_runtime_images
fi

if [[ -n "$SERVICE" ]]; then
    echo "Pulling $SERVICE..."
    # Pull is best-effort. Some services intentionally have no
    # registry image and `docker compose pull` exits non-zero:
    #   - retrieval-api: image klai/retrieval-api:local — tag-aliased
    #     locally from ghcr.io/getklai/retrieval-api:latest before this
    #     script runs (see retrieval-api.yml workflow `docker tag`).
    #   - bge-m3-sparse on gpu-01: built from local context.
    # For these the existing image is already up-to-date in the local
    # daemon; we proceed to `up -d` which uses what's there.
    if ! "$DOCKER" compose pull "$SERVICE" 2>&1; then
        echo "WARN: pull failed for $SERVICE (likely a locally-tagged image like klai/<svc>:local) — proceeding with existing local image"
    fi
    if [[ -n "$FORCE_RECREATE_FLAG" ]]; then
        echo "Recreating $SERVICE with --remove-orphans --force-recreate..."
    else
        echo "Recreating $SERVICE with --remove-orphans..."
    fi
    # Which container is serving right now, before anything is replaced. The
    # verdict block needs it to tell "the new one is up" apart from "the old
    # one never left".
    PRE_CID="$(service_container_id "$SERVICE")"
    # rc is captured rather than allowed to abort under `set -e`, because a
    # non-zero compose exit does NOT reliably mean the service is down — see
    # the verdict block near the bottom of this file.
    COMPOSE_RC=0
    # shellcheck disable=SC2086
    "$DOCKER" compose up -d --remove-orphans $NO_DEPS_FLAG $FORCE_RECREATE_FLAG "$SERVICE" \
        || COMPOSE_RC=$?
else
    echo "Pulling all services..."
    if ! "$DOCKER" compose pull 2>&1; then
        echo "WARN: bulk pull had failures (likely klai/<svc>:local-tagged services) — proceeding with existing local images"
    fi
    if [[ -n "$FORCE_RECREATE_FLAG" ]]; then
        echo "Recreating all services with --remove-orphans --force-recreate..."
    else
        echo "Recreating all services with --remove-orphans..."
    fi
    # shellcheck disable=SC2086
    "$DOCKER" compose up -d --remove-orphans $FORCE_RECREATE_FLAG
fi

# The verdict on a targeted deploy.
#
# `docker compose up -d`'s exit code is not a reliable answer to "is the new
# image serving", in either direction:
#
#   compose succeeds, container dead — `up -d` returns 0 once the container is
#     created. An image that panics on boot exits 0 here. Verification catches
#     it; this is the clear win and it is unconditional.
#
#   compose fails, container up — knowledge-ingest carries a deliberate
#     stop_grace_period of 90s (SPEC-PROCRASTINATE-ZOMBIE-001, so in-flight
#     LiteLLM calls are not cut off mid-enrichment). With a busy queue the stop
#     burns all 90s and compose aborts on "removal of container ... is already
#     in progress" (runs 31803259348, 31809767884 on 2026-08-14), leaving the
#     replacement behind under a hash-prefixed name.
#
# The second case is where a naive "container is running, call it green" turns
# dangerous, because the far more common compose failure looks identical from
# the outside: an unresolvable image tag, a registry hiccup, a dependency that
# would not start. In all of those compose aborts BEFORE replacing anything, so
# the OLD container is still running under the canonical name and a container
# check passes while production keeps serving the previous release — a green
# deploy that shipped nothing. A false red gets investigated; a false green does
# not. Trading the first for the second would be a net loss.
#
# So the override is narrow: compose may be overruled only when the container
# that passed verification is not the one that was already there. Same ID means
# nothing was replaced, and a non-zero compose exit stands.
if [[ -n "$SERVICE" ]]; then
    if verify_service_running "$SERVICE"; then
        if (( COMPOSE_RC != 0 )); then
            if [[ -n "$PRE_CID" && "$VERIFIED_CID" == "$PRE_CID" ]]; then
                echo "ERROR: docker compose exited $COMPOSE_RC and $SERVICE was never replaced —" >&2
                echo "       container ${PRE_CID:0:12} is the one that was already running, so this deploy shipped nothing." >&2
                exit 1
            fi
            echo "WARN: docker compose exited $COMPOSE_RC, but $SERVICE was replaced (${PRE_CID:0:12} -> ${VERIFIED_CID:0:12}) and verified running — treating the deploy as successful" >&2
        fi
    else
        if (( COMPOSE_RC != 0 )); then
            echo "ERROR: docker compose exited $COMPOSE_RC and $SERVICE did not come up" >&2
        fi
        exit 1
    fi
fi

# REQ-2d post-deploy orphan snapshot. Best-effort — snapshot failure
# does NOT fail the deploy. The snapshot script emits structlog-events
# to stdout; Alloy picks them up into VictoriaLogs.
if [[ -x "$KLAI_DIR/scripts/audit-orphan-snapshot.sh" ]]; then
    "$KLAI_DIR/scripts/audit-orphan-snapshot.sh" "${SERVICE:-all}" || \
        echo "WARN: post-deploy orphan-snapshot failed (deploy itself succeeded)" >&2
else
    echo "WARN: $KLAI_DIR/scripts/audit-orphan-snapshot.sh not installed yet — skipping post-deploy snapshot" >&2
fi
