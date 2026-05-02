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

# ─── Targeted destructive operations: extract target ──────────────────────────

TARGET=""
OPERATION=""

# docker rm <name|id> [more]
if [[ "$COMMAND" =~ docker[[:space:]]+rm[[:space:]]+(-f[[:space:]]+)?([a-zA-Z0-9_.-]+) ]]; then
    TARGET="${BASH_REMATCH[2]}"
    OPERATION="container removal"
fi
# docker rmi <name|sha> [more]
if [[ -z "$TARGET" ]] && [[ "$COMMAND" =~ docker[[:space:]]+rmi[[:space:]]+(-f[[:space:]]+)?([a-zA-Z0-9_./:-]+) ]]; then
    TARGET="${BASH_REMATCH[2]}"
    OPERATION="image removal"
fi
# docker volume rm <name>
if [[ -z "$TARGET" ]] && [[ "$COMMAND" =~ docker[[:space:]]+volume[[:space:]]+rm[[:space:]]+([a-zA-Z0-9_.-]+) ]]; then
    TARGET="${BASH_REMATCH[1]}"
    OPERATION="volume removal"
fi

# Nothing matched a destructive operation we know — allow
if [[ -z "$TARGET" ]]; then
    exit 0
fi

# ─── Check 2: tenant-naam pattern (always-on, lokaal) ─────────────────────────
# A name ending in -voys, -getklai, or -<word>-tenant is a strong signal of a
# customer-/tenant-specific container. Removing without explicit confirmation
# is exactly the librechat-voys mistake.

if [[ "$TARGET" =~ -voys$|-getklai$|-[a-z]+-tenant$ ]]; then
    emit_block "$TARGET matches a tenant-specific naming pattern ($OPERATION). Verify customer impact before removal."
fi

# ─── Check 3: compose git-history (best-effort, lokaal) ───────────────────────
# If a klai-infra checkout exists alongside this repo, search its compose
# history for the target. Appearance in history = was a declared service at
# some point = needs human review before removal.

KLAI_INFRA_CANDIDATES=(
    "$CLAUDE_PROJECT_DIR/../klai-infra"
    "$CLAUDE_PROJECT_DIR/../../klai-infra"
    "/opt/klai-infra"
)
for candidate in "${KLAI_INFRA_CANDIDATES[@]}"; do
    if [[ -d "$candidate/.git" ]]; then
        # 2s timeout — git log on a small repo is fast; bail if anything weird
        if timeout 2 git -C "$candidate" log --all -p -- 'deploy/docker-compose*.yml' 2>/dev/null | grep -qE "container_name:[[:space:]]*$TARGET\b|^[[:space:]]+$TARGET:[[:space:]]*$" ; then
            emit_block "$TARGET previously appeared as a service in klai-infra/deploy/docker-compose*.yml history. Verify why it was removed before deleting the runtime artifact."
        fi
        break
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
