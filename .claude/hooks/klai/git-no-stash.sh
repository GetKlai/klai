#!/usr/bin/env bash
# PreToolUse hook: block `git stash push` (and bare `git stash`).
#
# Stashes are silent backlog — they accumulate across sessions and
# never get reviewed. The 2026-05-04 cleanup found 10 stashes with
# WIP from branches that no longer exist.
#
# Alternative: WIP commit pattern (commit with WIP- prefix, switch
# context, return, amend or interactive-rebase the WIP commit away).
#
# Exception: `git stash push -m "RESCUE-..."` is allowed (cleanup-time
# rescue is a legitimate use, the prefix marks intent).
#
# Output: exit 2 + stderr message → CC interprets as block.
# SPEC-INFRA-AI-WORKFLOW-001 REQ-3.

set -euo pipefail

INPUT=$(cat)

PY_BIN=""
if command -v python3 >/dev/null 2>&1 && python3 -c "import sys" >/dev/null 2>&1; then
    PY_BIN="python3"
elif command -v python >/dev/null 2>&1 && python -c "import sys" >/dev/null 2>&1; then
    PY_BIN="python"
fi

if [ -n "$PY_BIN" ]; then
    COMMAND=$(echo "$INPUT" | "$PY_BIN" -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('tool_input', {}).get('command', ''))
" 2>/dev/null || echo "")
else
    COMMAND=$(echo "$INPUT" | sed -n 's/.*"command"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
fi

# Only check git stash creation commands.
# `git stash list/show/pop/drop/apply` are read/safe — let them through.
IS_STASH_PUSH=0
if echo "$COMMAND" | grep -qE 'git[[:space:]]+stash[[:space:]]+push\b'; then
    IS_STASH_PUSH=1
fi
# Bare `git stash` defaults to push.
if echo "$COMMAND" | grep -qE 'git[[:space:]]+stash[[:space:]]*$'; then
    IS_STASH_PUSH=1
fi

if [ "$IS_STASH_PUSH" -eq 0 ]; then
    exit 0
fi

# Allow RESCUE-prefixed stashes.
if echo "$COMMAND" | grep -qE -- '-m[[:space:]]+["'\'']?RESCUE-'; then
    exit 0
fi

cat >&2 <<'EOF'
BLOCKED: git stash push — stashes accumulate as silent backlog.

The 2026-05-04 cleanup found 10 stashes with WIP from branches that
no longer exist. None had been resolved.

Alternative: commit-with-WIP-marker pattern.

  git add -A
  git commit -m "WIP: <what you were doing>" --no-verify
  # ... switch context, do other work, come back ...
  git checkout <your branch>
  # ... finish the work ...
  git commit --amend           # OR
  git rebase -i HEAD~2         # to squash WIP away

Why this is better than stash:
  - WIP commit is visible in `git log` — you can't forget it.
  - Survives branch switching without a stash-pop step.
  - Survives `git worktree remove` of the original worktree.
  - Clearly named so reviewers know it's intentional.

Exception: `git stash push -m "RESCUE-..."` is allowed for cleanup ops.

See SPEC-INFRA-AI-WORKFLOW-001 REQ-3.
EOF
exit 2
