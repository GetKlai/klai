#!/usr/bin/env bash
# PreToolUse hook: block destructive docker-actions on production-like targets
#
# Blocks: docker rm/rmi/volume rm/system prune, docker compose down --volumes,
#         and aggressive prune flags (image prune -af, volume prune).
# Run-time guard against the librechat-voys class of incidents:
# productie-tenant container removed because it lacked compose labels.
#
# Five checks for `docker rm/rmi/volume rm <target>`:
#   1. Hard-block dangerous global prunes (volume prune / image prune -af)
#   2. Tenant-naam pattern match (-voys, -getklai, -<klant>)
#   3. Compose history check (was target ever a declared service?)
#   4. Caddy upstream check (only when klai-infra checkout is reachable)
#   5. VictoriaLogs orphan-audit cross-check (only when MCP / curl reachable)
#
# Checks 4 + 5 are best-effort — fail-open if klai-infra checkout absent or
# core-01 unreachable (dev-machine without VPN). Checks 1 + 2 are always-on.
# Hook MUST stay <2s to keep PreToolUse latency acceptable.
#
# Exit 0 = allow, exit 2 = block (with JSON decision payload on stdout).
#
# SPEC-INFRA-CONTAINER-HYGIENE-001 REQ-1.

set -euo pipefail

INPUT=$(cat)

# JSON parsing: prefer python3 (Linux/Mac), fall back to python (Windows),
# fall back to sed (no Python at all). The sed fallback is approximate but
# good enough for docker-command extraction — escaped quotes in commands
# would be rare and the fail-closed checks (1) still cover them.
PY_BIN=""
if command -v python3 >/dev/null 2>&1 && python3 -c "import sys" >/dev/null 2>&1; then
    PY_BIN="python3"
elif command -v python >/dev/null 2>&1 && python -c "import sys" >/dev/null 2>&1; then
    PY_BIN="python"
fi

if [[ -n "$PY_BIN" ]]; then
    COMMAND=$(echo "$INPUT" | "$PY_BIN" -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('tool_input', {}).get('command', ''))
except Exception:
    pass
" 2>/dev/null || echo "")
else
    # sed fallback: extract value of "command":"..." (best-effort)
    COMMAND=$(echo "$INPUT" | sed -nE 's/.*"command"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/p' | head -1)
fi

# Fast-path: not a docker command
if ! echo "$COMMAND" | grep -qE '\bdocker\b'; then
    exit 0
fi

emit_block() {
    local reason="$1"
    if [[ -n "$PY_BIN" ]]; then
        "$PY_BIN" -c "
import json, sys
reason = sys.argv[1]
print(json.dumps({
  'decision': 'block',
  'reason': f'BLOCKED by container-hygiene-preflight: {reason}\n\nSPEC-INFRA-CONTAINER-HYGIENE-001 REQ-1. Override only with explicit user approval.'
}))
" "$reason"
    else
        # Manual JSON build — escape backslashes and double-quotes
        local esc="${reason//\\/\\\\}"
        esc="${esc//\"/\\\"}"
        printf '{"decision":"block","reason":"BLOCKED by container-hygiene-preflight: %s\\n\\nSPEC-INFRA-CONTAINER-HYGIENE-001 REQ-1. Override only with explicit user approval."}\n' "$esc"
    fi
    exit 2
}

# ─── Check 1: hard-block dangerous global prunes ──────────────────────────────
# These never have a legitimate one-shot use case — REQ-6 systemd timer is the
# canonical safe-cleanup path. If we ever need a one-off, the user runs it.

if echo "$COMMAND" | grep -qE 'docker\s+volume\s+prune'; then
    emit_block "docker volume prune — risk of klantdata loss. Use targeted 'docker volume rm <name>' after manual review."
fi
if echo "$COMMAND" | grep -qE 'docker\s+image\s+prune\s+-a\s*f|docker\s+image\s+prune\s+-af|docker\s+image\s+prune\s+--all\s+--force'; then
    emit_block "docker image prune -af — can delete rollback-bare ghcr.io/getklai/* :sha tags. Use 'docker image prune -f' for dangling-only."
fi
if echo "$COMMAND" | grep -qE 'docker\s+system\s+prune\s+-a|docker\s+system\s+prune\s+--all'; then
    emit_block "docker system prune -a — too broad. Use targeted prune commands (image -f, container -f)."
fi
if echo "$COMMAND" | grep -qE 'docker\s+compose\s+down\s+(-v|--volumes)'; then
    emit_block "docker compose down --volumes — destroys named volumes. Stop containers without -v, then targeted volume rm if needed."
fi

# ─── Targeted destructive operations: extract ALL targets ────────────────────
# `docker rm a b c` removes three containers. Extract every argument that
# follows the verb (skipping flags) so a tenant-named container in position
# 2/3/N is also blocked, not just the first.

TARGETS=()
OPERATION=""

extract_args_after() {
    # Strip leading whitespace + verb, then split by whitespace, dropping flags.
    local rest="$1"
    # Take everything after the verb; remove `--flag` and `-x` tokens
    awk '{
        for (i=1; i<=NF; i++) {
            if ($i ~ /^-/) continue
            print $i
        }
    }' <<< "$rest"
}

if [[ "$COMMAND" =~ docker[[:space:]]+rm[[:space:]]+(.*)$ ]]; then
    OPERATION="container removal"
    while IFS= read -r tgt; do
        [[ -n "$tgt" ]] && TARGETS+=("$tgt")
    done < <(extract_args_after "${BASH_REMATCH[1]}")
elif [[ "$COMMAND" =~ docker[[:space:]]+rmi[[:space:]]+(.*)$ ]]; then
    OPERATION="image removal"
    while IFS= read -r tgt; do
        [[ -n "$tgt" ]] && TARGETS+=("$tgt")
    done < <(extract_args_after "${BASH_REMATCH[1]}")
elif [[ "$COMMAND" =~ docker[[:space:]]+volume[[:space:]]+rm[[:space:]]+(.*)$ ]]; then
    OPERATION="volume removal"
    while IFS= read -r tgt; do
        [[ -n "$tgt" ]] && TARGETS+=("$tgt")
    done < <(extract_args_after "${BASH_REMATCH[1]}")
fi

# Nothing matched a destructive operation we know — allow
if [[ ${#TARGETS[@]} -eq 0 ]]; then
    exit 0
fi

# Locate klai-infra checkout once; reuse across targets
KLAI_INFRA=""
for candidate in \
    "$CLAUDE_PROJECT_DIR/../klai-infra" \
    "$CLAUDE_PROJECT_DIR/../../klai-infra" \
    "$CLAUDE_PROJECT_DIR/klai-infra" \
    "/opt/klai-infra"; do
    if [[ -d "$candidate/.git" ]]; then
        KLAI_INFRA="$candidate"
        break
    fi
done

# Iterate over every target; first hard-match wins (block immediately).
for TARGET in "${TARGETS[@]}"; do
    # Check 2: tenant-naam / klasse-B prefix pattern
    if [[ "$TARGET" =~ -voys$|-getklai$|-[a-z]+-tenant$ ]] || [[ "$TARGET" =~ ^librechat- ]]; then
        emit_block "$TARGET matches a tenant-managed naming pattern ($OPERATION). If this is a portal-api-provisioning-managed tenant container (klasse B, see SPEC REQ-2), use the deprovision flow instead: portal-api orchestrator.deprovision_tenant() — never delete directly via 'docker rm'. If this is a different match, verify customer impact and override with explicit user approval."
    fi

    # Check 3: compose git-history (best-effort)
    if [[ -n "$KLAI_INFRA" ]]; then
        if timeout 2 git -C "$KLAI_INFRA" log --all -p -- 'deploy/docker-compose*.yml' 2>/dev/null | grep -qE "container_name:[[:space:]]*$TARGET\b|^[[:space:]]+$TARGET:[[:space:]]*$" ; then
            emit_block "$TARGET previously appeared as a service in klai-infra/deploy/docker-compose*.yml history. Verify why it was removed before deleting the runtime artifact."
        fi
    fi
done

# ─── Check 4 + 5: Caddy upstream + VictoriaLogs traffic (best-effort) ─────────
# These require core-01 reachability. Fail-open on dev machines without VPN —
# Check 2 + 3 cover the common-case dangers. Server-side detection happens via
# REQ-5 audit-stream (independent of this hook).
#
# Implementation deferred to a follow-up — initial PR keeps the hook hermetic
# (no SSH, no curl) so it stays under 500ms and works for offline dev.
# When core-01 reachability detection is added, gate behind a fast TCP
# probe (timeout 1s) and skip silently on failure.

# All checks passed — allow
exit 0
