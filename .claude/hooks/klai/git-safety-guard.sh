#!/usr/bin/env bash
# PreToolUse hook: block destructive git commands
#
# Blocks: reset --hard, push --force, checkout ., restore ., clean -f,
#         branch -D, add -A, add .
# Allows: checkout -b (new branch), restore --staged (unstaging)
#
# Exit 0 = allow, exit 2 = block

set -euo pipefail

INPUT=$(cat)

COMMAND=$(echo "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('tool_input', {}).get('command', ''))
" 2>/dev/null || echo "")

# Only check git commands
if ! echo "$COMMAND" | grep -qE '^\s*git\s'; then
    exit 0
fi

# Allow safe variants before checking destructive patterns
# git checkout -b (create branch) is safe
echo "$COMMAND" | grep -qE 'git\s+checkout\s+-b\s' && exit 0
# git restore --staged (unstage) is safe
echo "$COMMAND" | grep -qE 'git\s+restore\s+--staged' && exit 0

# Block destructive commands
BLOCKED=""

echo "$COMMAND" | grep -qE 'git\s+reset\s+--hard' && BLOCKED="git reset --hard — discards all uncommitted changes"
echo "$COMMAND" | grep -qE 'git\s+push\s+.*--force' && BLOCKED="git push --force — rewrites remote history"
echo "$COMMAND" | grep -qE 'git\s+push\s+-f\b' && BLOCKED="git push -f — rewrites remote history"
echo "$COMMAND" | grep -qE 'git\s+checkout\s+\.\s*$' && BLOCKED="git checkout . — discards all unstaged changes"
echo "$COMMAND" | grep -qE 'git\s+checkout\s+--\s+\.' && BLOCKED="git checkout -- . — discards all unstaged changes"
echo "$COMMAND" | grep -qE 'git\s+restore\s+\.\s*$' && BLOCKED="git restore . — discards all unstaged changes"
echo "$COMMAND" | grep -qE 'git\s+clean\s+-f' && BLOCKED="git clean -f — deletes untracked files permanently"
echo "$COMMAND" | grep -qE 'git\s+branch\s+-D\s' && BLOCKED="git branch -D — force-deletes branch without merge check"
echo "$COMMAND" | grep -qE 'git\s+add\s+-A' && BLOCKED="git add -A — stages everything including secrets/unintended files. Stage specific files instead."
echo "$COMMAND" | grep -qE 'git\s+add\s+\.\s*$' && BLOCKED="git add . — stages everything including secrets/unintended files. Stage specific files instead."

if [ -n "$BLOCKED" ]; then
    python3 -c "
import json, sys
reason = sys.argv[1]
print(json.dumps({'decision': 'block', 'reason': f'BLOCKED: {reason}\n\nAsk the user for confirmation before running destructive git commands.'}))
" "$BLOCKED"
    exit 2
fi

exit 0
