#!/usr/bin/env bash
# audit-compose-orphans.sh — CI-guard tegen orphan-by-construction
# in deploy/docker-compose.yml en deploy/caddy/Caddyfile.
#
# Three checks, each a hard fail:
#
#   1. Every Caddy upstream (`reverse_proxy <host>:<port>`) in
#      deploy/caddy/Caddyfile MUST resolve to either:
#        - a service name declared in deploy/docker-compose.yml
#        - a klasse-B provisioning-managed pattern (`librechat-*`,
#          set by portal-api `_start_librechat_container`)
#        - a whitelisted external endpoint (Vexa internal, etc.)
#      Otherwise the upstream points at a container that doesn't
#      exist and will silently break tenant routing.
#
#   2. Every `container_name:` in deploy/docker-compose.yml MUST match
#      its enclosing service block (no drift). Mismatch = silent
#      orphan-on-deploy because compose creates the named container
#      but `--remove-orphans` won't recognise it.
#
#   3. Every top-level volume declared in deploy/docker-compose.yml
#      `volumes:` section MUST be referenced by at least one service.
#      Unreferenced top-level volumes accumulate as "verlaten" volumes
#      that detection scripts later flag as orphans.
#
# Exits non-zero on any violation. Intended to run in CI on PRs that
# touch deploy/docker-compose.yml or deploy/caddy/Caddyfile.
#
# SPEC-INFRA-CONTAINER-HYGIENE-001 REQ-2c.
#
# Exit codes:
#   0 = all checks passed
#   1 = one or more violations (details on stderr)
#   2 = configuration error (missing files, missing tools)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/deploy/docker-compose.yml"
CADDYFILE="${REPO_ROOT}/deploy/caddy/Caddyfile"

# Allow override for unit-test fixtures
COMPOSE_FILE="${AUDIT_COMPOSE_FILE:-$COMPOSE_FILE}"
CADDYFILE="${AUDIT_CADDYFILE:-$CADDYFILE}"

if ! command -v yq >/dev/null 2>&1; then
    echo "ERROR: yq is required (mikefarah/yq v4)" >&2
    exit 2
fi

if [[ ! -f "$COMPOSE_FILE" ]]; then
    echo "ERROR: compose file not found: $COMPOSE_FILE" >&2
    exit 2
fi

VIOLATIONS=0

# ─── Klasse-B name patterns ───────────────────────────────────────────────────
# Provisioning-managed by portal-api. SPEC-INFRA-CONTAINER-HYGIENE-001 REQ-2.
# Maintain in sync with .claude/rules/klai/infra/container-hygiene.md.
KLASSE_B_PATTERNS=(
    '^librechat-[a-z0-9-]+$'
)

# ─── Whitelist for Caddy upstreams that do NOT need a compose service ────────
# These are intentionally external or runtime-resolved.
CADDY_WHITELIST=(
    '^localhost$'
    '^127\.0\.0\.1$'
    '^api-gateway$'              # Vexa internal — managed by Vexa stack
    '^admin-api$'                # Vexa internal
    '^meeting-api$'              # Vexa internal
    '^runtime-api$'              # Vexa internal
    '^portal-api-dev$'           # protected dev environment outside prod compose
)

is_whitelisted() {
    local host="$1"
    for pat in "${CADDY_WHITELIST[@]}"; do
        [[ "$host" =~ $pat ]] && return 0
    done
    return 1
}

is_klasse_b_pattern() {
    local host="$1"
    for pat in "${KLASSE_B_PATTERNS[@]}"; do
        [[ "$host" =~ $pat ]] && return 0
    done
    return 1
}

# ─── Compose service inventory ────────────────────────────────────────────────

mapfile -t COMPOSE_SERVICES < <(yq eval '.services | keys | .[]' "$COMPOSE_FILE")

if [[ ${#COMPOSE_SERVICES[@]} -eq 0 ]]; then
    echo "ERROR: no services found in $COMPOSE_FILE — is this a valid compose file?" >&2
    exit 2
fi

is_compose_service() {
    local host="$1"
    for svc in "${COMPOSE_SERVICES[@]}"; do
        [[ "$host" == "$svc" ]] && return 0
    done
    return 1
}

# ─── Check 1: Caddy upstreams resolve to compose-services or klasse-B ────────

if [[ -f "$CADDYFILE" ]]; then
    # Caddy `reverse_proxy [@matcher] <upstream> [<upstream>...]` accepts
    # an optional named matcher as first arg. Skip @-prefixed tokens and
    # flags so we only collect actual upstream hosts.
    mapfile -t UPSTREAMS < <(
        grep -hE '^\s*(reverse_proxy|proxy)\s+' "$CADDYFILE" \
        | awk '{
            for (i=2; i<=NF; i++) {
                t = $i
                if (t ~ /^@/) continue       # named matcher
                if (t ~ /^-/) continue       # flag
                if (t ~ /^\{/) continue      # caddy placeholder
                sub(/^[A-Za-z][A-Za-z0-9+.-]*:\/\//, "", t)   # strip scheme
                sub(/\/.*$/, "", t)          # strip path
                sub(/:.*$/, "", t)           # strip port
                if (length(t) > 0) print t
            }
        }' \
        | sort -u
    )

    for host in "${UPSTREAMS[@]}"; do
        [[ -z "$host" ]] && continue
        if is_compose_service "$host"; then continue; fi
        if is_klasse_b_pattern "$host"; then continue; fi
        if is_whitelisted "$host"; then continue; fi
        echo "VIOLATION (Check 1): Caddy upstream '$host' is neither a compose service, a klasse-B provisioning pattern, nor whitelisted." >&2
        echo "  Source: $CADDYFILE" >&2
        echo "  Either declare '$host' as a service in deploy/docker-compose.yml, add a klasse-B pattern, or add to CADDY_WHITELIST." >&2
        VIOLATIONS=$((VIOLATIONS + 1))
    done
else
    echo "INFO: $CADDYFILE not found — skipping Caddy upstream check" >&2
fi

# ─── Check 2: container_name matches its service block ───────────────────────

while IFS=$'\t' read -r svc cn; do
    [[ -z "$cn" ]] && continue
    [[ "$cn" == "null" ]] && continue
    # Allow the convention where container_name explicitly differs (e.g.
    # librechat-getklai service has container_name: librechat-getklai). The
    # check only flags when service uses a generic name but container_name
    # uses a tenant-specific suffix without explicit declaration in the
    # service block — i.e. just compare for non-equal pairs.
    if [[ "$cn" != "$svc" ]]; then
        # Allowed: explicit tenant/named containers where the service was
        # declared with that intent. Currently the only legitimate divergence
        # in compose is when service-name == container_name. If you need a
        # divergent name, add an exception here with rationale.
        # (Currently no exceptions needed; all known services match.)
        echo "VIOLATION (Check 2): service '$svc' has container_name '$cn' that does not match. Drift will silently break --remove-orphans cleanup." >&2
        VIOLATIONS=$((VIOLATIONS + 1))
    fi
done < <(yq eval '.services | to_entries | .[] | [.key, .value.container_name] | @tsv' "$COMPOSE_FILE")

# ─── Check 3: top-level volumes are referenced by at least one service ───────

mapfile -t TOP_VOLUMES < <(yq eval '.volumes | keys | .[]' "$COMPOSE_FILE" 2>/dev/null || true)

if [[ ${#TOP_VOLUMES[@]} -gt 0 ]]; then
    # Build a set of all volume references across all services. Volumes can
    # be referenced as `<volume>:<path>` in `volumes:` arrays.
    REFERENCED=$(yq eval '.services[] | .volumes[]?' "$COMPOSE_FILE" 2>/dev/null \
                 | sed -E 's/:.*$//' \
                 | sort -u || true)

    for vol in "${TOP_VOLUMES[@]}"; do
        [[ -z "$vol" ]] && continue
        if ! grep -qxF "$vol" <<< "$REFERENCED"; then
            echo "VIOLATION (Check 3): top-level volume '$vol' is declared but not used by any service. Wees-by-construction." >&2
            echo "  Source: $COMPOSE_FILE volumes: section" >&2
            VIOLATIONS=$((VIOLATIONS + 1))
        fi
    done
fi

# ─── Verdict ─────────────────────────────────────────────────────────────────

if [[ $VIOLATIONS -gt 0 ]]; then
    echo "" >&2
    echo "audit-compose-orphans: $VIOLATIONS violation(s) found." >&2
    exit 1
fi

echo "audit-compose-orphans: OK (${#COMPOSE_SERVICES[@]} services, ${#TOP_VOLUMES[@]} top-level volumes verified)"
exit 0
