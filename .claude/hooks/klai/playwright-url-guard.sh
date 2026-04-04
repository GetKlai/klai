#!/usr/bin/env bash
# PreToolUse hook: inject Klai production URLs when Playwright navigates
#
# Two behaviors:
# 1. BLOCK navigation to portal.getklai.com (wrong URL)
# 2. INJECT additionalContext with correct URLs on every navigation

set -euo pipefail

INPUT=$(cat)

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

# Inject context for all navigations
# Include localhost URLs for local dev testing
python3 -c "
import json
print(json.dumps({
    'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'permissionDecision': 'allow',
        'additionalContext': 'Klai URLs: Production portal = https://getklai.getklai.com/ | Docs = https://docs.getklai.com/ | portal.getklai.com serves NOTHING. Local dev: frontend = http://localhost:5174/ | backend API = http://localhost:8010/. IMPORTANT: after ALL Playwright testing is done, ALWAYS call browser_close to release the browser for future sessions.'
    }
}))
"
exit 0
