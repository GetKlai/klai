#!/usr/bin/env bash
# PreToolUse hook: block `git worktree add <path> main`
#
# A worktree that checks out `main` in a non-canonical location forces
# the canonical main repo into detached-HEAD whenever the user wants
# to switch to main. This caused 33 worktrees of cross-worktree
# friction in the 2026-05-04 cleanup. Block at source.
#
# Allowed: `git worktree add <path> -b <branch> main`
#          (creates a NEW branch FROM main — main stays canonical)
# Blocked: `git worktree add <path> main`
#          `git worktree add <path> origin/main`
#          `git worktree add <path> refs/heads/main`
#
# Output: exit 2 + stderr message → CC interprets as block.
# SPEC-INFRA-AI-WORKFLOW-001 REQ-1.

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

# Only check git worktree add commands.
if ! echo "$COMMAND" | grep -qE 'git[[:space:]]+worktree[[:space:]]+add[[:space:]]'; then
    exit 0
fi

# Allow `-b <branch> [<base>]` form — that creates a new branch FROM the base.
if echo "$COMMAND" | grep -qE 'git[[:space:]]+worktree[[:space:]]+add[[:space:]].*[[:space:]]-b[[:space:]]'; then
    exit 0
fi

# Block dangerous patterns:
BLOCKED=""
if echo "$COMMAND" | grep -qE 'git[[:space:]]+worktree[[:space:]]+add[[:space:]].*[[:space:]](origin/)?main([[:space:]]|$)'; then
    BLOCKED="git worktree add ... main"
fi
if echo "$COMMAND" | grep -qE 'git[[:space:]]+worktree[[:space:]]+add[[:space:]].*refs/heads/main'; then
    BLOCKED="git worktree add ... refs/heads/main"
fi

if [ -n "$BLOCKED" ]; then
    cat >&2 <<EOF
BLOCKED: $BLOCKED — would claim 'main' in a non-canonical location.

A worktree on main forces the canonical repo into detached-HEAD when
you switch to main. This is the friction that caused the 2026-05-04
cleanup mess (33 worktrees of cross-collision).

Use one of these instead:
  git worktree add <path> -b feature/<task> origin/main
  git worktree add <path> -b fix/<task> origin/main

If you genuinely need a read-only main snapshot:
  git worktree add --detach <path> origin/main

See SPEC-INFRA-AI-WORKFLOW-001 REQ-1.
EOF
    exit 2
fi

exit 0
