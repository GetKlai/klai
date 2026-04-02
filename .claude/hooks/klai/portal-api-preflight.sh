#!/usr/bin/env bash
# PreToolUse hook: portal-api pre-flight check
#
# Intercepts Bash commands that match "docker compose up -d ... portal-api" and
# verifies that all required env vars are non-empty BEFORE allowing the restart.
#
# Required vars (names as they appear in `docker compose config portal-api`):
#   ZITADEL_PAT        — PAT for Zitadel sessions API
#   PORTAL_SECRETS_KEY — AES-256 hex key
#   SSO_COOKIE_KEY     — Fernet key
#   DATABASE_URL       — asyncpg DSN (proves PORTAL_API_DB_PASSWORD is set)
#   DOMAIN             — getklai.com
#
# See: .claude/rules/klai/pitfalls/platform.md#platform-portal-api-deploy-env-preflight

set -euo pipefail

INPUT=$(cat)

# Only act on Bash tool calls
TOOL_NAME=$(echo "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('tool_name', ''))
" 2>/dev/null || echo "")

if [ "$TOOL_NAME" != "Bash" ]; then
    exit 0
fi

# Only act when the command targets portal-api via docker compose up
COMMAND=$(echo "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('tool_input', {}).get('command', ''))
" 2>/dev/null || echo "")

if ! echo "$COMMAND" | grep -qE 'docker[[:space:]-]?compose up.*portal-api'; then
    exit 0
fi

# --- Pre-flight: check required vars on core-01 ---
REQUIRED_VARS="ZITADEL_PAT PORTAL_SECRETS_KEY SSO_COOKIE_KEY DATABASE_URL DOMAIN"
EMPTY_VARS=""

# Fetch the resolved compose config (env vars expanded) for portal-api
CONFIG=$(ssh core-01 "cd /opt/klai && docker compose config portal-api 2>/dev/null" 2>/dev/null) || {
    # Cannot reach core-01 — don't block, but warn
    python3 -c "
import json
print(json.dumps({'decision': 'block', 'reason': 'Pre-flight FAILED: cannot reach core-01 via SSH to verify env vars.\nFix SSH access before attempting docker compose up -d portal-api.'}))
"
    exit 2
}

for VAR in $REQUIRED_VARS; do
    # Match "  VAR: value" — empty value appears as `""` or blank after colon
    LINE=$(echo "$CONFIG" | grep -E "^[[:space:]]+${VAR}:" || echo "")
    if [ -z "$LINE" ]; then
        EMPTY_VARS="$EMPTY_VARS $VAR (missing)"
        continue
    fi
    VALUE=$(echo "$LINE" | sed 's/^[^:]*: *//' | tr -d '"' | xargs)
    if [ -z "$VALUE" ]; then
        EMPTY_VARS="$EMPTY_VARS $VAR (empty)"
    fi
done

if [ -n "$EMPTY_VARS" ]; then
    python3 -c "
import json
empty = '''$EMPTY_VARS'''
reason = (
    'Pre-flight BLOCKED — portal-api has empty required env vars:\n'
    + empty.strip() + '\n\n'
    'Fix before restarting:\n'
    '  1. Check /opt/klai/.env on core-01 has the missing variables\n'
    '  2. Run: ssh core-01 \"docker compose config portal-api\" | grep -A 60 environment:\n'
    '  3. See: .claude/rules/klai/pitfalls/platform.md#platform-portal-api-deploy-env-preflight\n'
)
print(json.dumps({'decision': 'block', 'reason': reason}))
"
    exit 2
fi

# All vars present — allow the command
exit 0
