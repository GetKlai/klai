#!/usr/bin/env bash
# PreToolUse hook: block find|grep / xargs grep / grep -r patterns in Bash
#
# Fires on Bash tool calls. Detects large-scope search commands that produce
# "Output too large" results and blocks them, redirecting to the Grep tool.
# Exit 2 = block the Bash call entirely.

set -euo pipefail

INPUT=$(cat)

COMMAND=$(echo "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('tool_input', {}).get('command', ''))
" 2>/dev/null || echo "")

# Detect search anti-patterns:
#   find ... | grep
#   find ... | xargs grep
#   xargs grep
#   grep -r / grep -l / grep -rl across large paths (not scoped to a single file)
MATCH=0

# find piped into grep or xargs grep
if echo "$COMMAND" | grep -qE 'find\s+.+\|\s*(xargs\s+grep|grep)'; then
    MATCH=1
fi

# xargs grep (without find, e.g. from a list)
if echo "$COMMAND" | grep -qE '\|\s*xargs\s+grep'; then
    MATCH=1
fi

# grep -r or grep -rl or grep -l targeting a directory (not a specific file)
if echo "$COMMAND" | grep -qE 'grep\s+(-[a-zA-Z]*r[a-zA-Z]*|-[a-zA-Z]*l[a-zA-Z]*)\s'; then
    MATCH=1
fi

if [ "$MATCH" -eq 1 ]; then
    echo '{"decision":"block","reason":"BASH GREP GUARD: This command pattern (find|grep, xargs grep, or grep -r/-l) produces output too large for the context window.\n\nUse the dedicated Grep tool instead:\n  Grep(pattern=\"...\", path=\"...\", output_mode=\"files_with_matches\")\n  Grep(pattern=\"...\", glob=\"**/*.py\", output_mode=\"content\")\n\nGrep handles large result sets correctly, supports -A/-B/-C context, and never truncates silently.\nScope with: path=, glob=, or type= parameters."}'
    exit 2
fi

exit 0
