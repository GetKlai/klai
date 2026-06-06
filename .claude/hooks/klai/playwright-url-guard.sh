#!/usr/bin/env bash
# PreToolUse hook: inject Klai browser-test context when Playwright navigates
#
# Three behaviors:
# 1. BLOCK navigation to portal.getklai.com (wrong URL)
# 2. BLOCK local portal navigation when the local-dev preflight fails
# 3. INJECT additionalContext with correct URLs on every navigation

set -euo pipefail

INPUT=$(cat)
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"

URL=$(echo "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('tool_input', {}).get('url', ''))
" 2>/dev/null || echo "")

# Block wrong URL
if echo "$URL" | grep -q 'portal\.getklai\.com'; then
    python3 -c "
import json
print(json.dumps({
    'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'permissionDecision': 'deny',
        'permissionDecisionReason': 'WRONG URL: portal.getklai.com serves nothing. The Klai portal runs at https://getklai.getklai.com/ — use that URL instead.'
    }
}))
"
    exit 0
fi

if echo "$URL" | grep -qE '^https?://(localhost|127\.0\.0\.1):[0-9]+/(admin|app)(/|$)'; then
    if ! "$PROJECT_DIR/scripts/local-dev-status.sh" --mode local --strict --quiet --expected-url "$URL" >/tmp/klai-local-dev-status.out 2>&1; then
        REASON=$(cat /tmp/klai-local-dev-status.out 2>/dev/null || true)
        python3 -c "
import json, sys
reason = sys.stdin.read().strip()
print(json.dumps({
    'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'permissionDecision': 'deny',
        'permissionDecisionReason': 'LOCAL DEV PREFLIGHT FAILED. Do not navigate local portal routes until scripts/local-dev-status.sh --mode local --strict is green. Details: ' + reason
    }
}))
" <<< "$REASON"
        exit 0
    fi
fi

# Inject context for all navigations
# Include localhost URLs for local dev testing
python3 -c "
import json
print(json.dumps({
    'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'permissionDecision': 'allow',
        'additionalContext': 'Klai browser testing contract: run scripts/local-dev-status.sh --mode local --strict before local portal checks. Local standalone must use VITE_AUTH_DEV_MODE=true and AUTH_DEV_MODE=true; if it redirects to my.getklai.com/login, STOP and diagnose. Production E2E must be validated with scripts/local-dev-status.sh --mode prod-e2e and must not target localhost. URLs: production portal = https://getklai.getklai.com/ | docs = https://docs.getklai.com/ | portal.getklai.com serves NOTHING. Local defaults: frontend http://localhost:5174/ and backend http://localhost:8010/, or CONDUCTOR_PORT / CONDUCTOR_PORT+1 in Conductor. IMPORTANT: after ALL Playwright testing is done, ALWAYS call browser_close to release the browser for future sessions.'
    }
}))
"
exit 0
