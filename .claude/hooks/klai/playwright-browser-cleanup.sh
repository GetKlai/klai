#!/usr/bin/env bash
# Stop hook: remind agent to close Playwright browser before ending session
#
# Checks if the Brave profile lock file exists (indicating browser is still open).
# If so, injects a systemMessage telling the agent to call browser_close first.

set -euo pipefail

PROFILE_DIR="$HOME/.claude/mcp-brave-profile"
LOCK_FILE="$PROFILE_DIR/SingletonLock"

# Check if the browser profile lock exists (Chromium creates this while running)
if [ -f "$LOCK_FILE" ] || [ -L "$LOCK_FILE" ]; then
    python3 -c "
import json
print(json.dumps({
    'hookSpecificOutput': {
        'hookEventName': 'Stop',
        'systemMessage': 'BROWSER STILL OPEN: The Playwright browser is still running. You MUST call browser_close before ending this session, otherwise the next session cannot use the browser (\"Browser is already in use\" error). Call browser_close now, then stop.'
    }
}))
"
    exit 2  # Block the stop — force agent to close browser first
fi

# Browser not running, allow stop
exit 0
