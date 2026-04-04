#!/usr/bin/env bash
# PreToolUse hook: inject domain-specific context when operational commands are detected
#
# This hook does NOT block — it reminds Claude to read relevant pitfalls/patterns
# before executing infrastructure, deploy, or platform commands.
#
# Exit 0 = always allow the command, but print context reminder to stdout

set -euo pipefail

INPUT=$(cat)

COMMAND=$(echo "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('tool_input', {}).get('command', ''))
" 2>/dev/null || echo "")

# Skip empty or non-matching commands quickly
[ -z "$COMMAND" ] && exit 0

RULES_DIR=".claude/rules/klai"
CONTEXT=""

# --- SSH commands → infrastructure knowledge ---
if echo "$COMMAND" | grep -qE '^\s*ssh\s'; then
    CONTEXT="You're about to SSH into a server. Before making changes, read:
- ${RULES_DIR}/pitfalls/infrastructure.md (env wipes, dollar sign truncation, fail2ban)
- ${RULES_DIR}/patterns/infrastructure.md (SOPS commands, atomic env deploy)
- ${RULES_DIR}/patterns/devops.md#core-01-ssh (ALWAYS use 'ssh core-01' alias, never direct IP)"
fi

# --- docker compose → devops + platform knowledge ---
if echo "$COMMAND" | grep -qE 'docker[[:space:]-]?compose'; then
    CONTEXT="You're running docker compose. Before proceeding, read:
- ${RULES_DIR}/pitfalls/devops.md (restart vs up -d, env inheritance, image staleness)
- ${RULES_DIR}/patterns/devops.md (compose sync, rebuild patterns)
Key reminder: 'docker compose restart' does NOT reload .env — use 'up -d' instead."
fi

# --- docker (non-compose) → devops knowledge ---
if [ -z "$CONTEXT" ] && echo "$COMMAND" | grep -qE '^\s*docker\s'; then
    CONTEXT="You're running a docker command. Relevant context:
- ${RULES_DIR}/pitfalls/devops.md (GHCR auth, image versions, env recovery from running containers)
- ${RULES_DIR}/patterns/devops.md (local image builds, no-cache rebuilds)"
fi

# --- alembic → migration pitfalls ---
if echo "$COMMAND" | grep -qE '^\s*alembic\s|uv run alembic'; then
    CONTEXT="You're running Alembic migrations. Before proceeding, read:
- ${RULES_DIR}/pitfalls/devops.md#devops-alembic-multiple-heads (check 'alembic heads' after branch merges)
- ${RULES_DIR}/pitfalls/devops.md#devops-alembic-duplicate-object-on-rerun (use IF NOT EXISTS in DDL)"
fi

# --- sops → secrets management ---
if echo "$COMMAND" | grep -qE '^\s*sops\s'; then
    CONTEXT="You're editing SOPS secrets. Before proceeding, read:
- ${RULES_DIR}/pitfalls/infrastructure.md (incomplete file wipes server, placeholder values)
- ${RULES_DIR}/patterns/infrastructure.md#sops-secret-edit (correct edit workflow)
- ${RULES_DIR}/patterns/devops.md#sops-env-sync (safety guards in sync-env.yml)
CRITICAL: SOPS file must be COMPLETE — missing vars will be wiped from the server on next sync."
fi

# --- sed/echo on .env files → server secrets danger ---
if echo "$COMMAND" | grep -qE "(sed|echo|cat\s*>).*\.env"; then
    CONTEXT="You're modifying a .env file via shell command. This is DANGEROUS.
- NEVER modify existing secrets via sed/echo — dollar signs get truncated by shell interpolation
- For new vars only: use single quotes: echo 'NEW=value' >> file
- For changes: use SOPS workflow (${RULES_DIR}/patterns/infrastructure.md#sops-secret-edit)
- After ANY change: verify with 'docker exec <container> printenv VAR_NAME'"
fi

# --- curl to production → platform knowledge ---
if echo "$COMMAND" | grep -qE 'curl.*(core-01|getklai\.com|localhost:8)'; then
    CONTEXT="You're curling a production or local service. Relevant context:
- ${RULES_DIR}/pitfalls/platform.md (Zitadel, LiteLLM, LibreChat, Caddy issues)
- Always use --connect-timeout 2 --max-time 3 to avoid hanging"
fi

# --- Output context if matched ---
if [ -n "$CONTEXT" ]; then
    echo "$CONTEXT"
fi

exit 0
