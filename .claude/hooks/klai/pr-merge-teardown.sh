#!/usr/bin/env bash
# PostToolUse hook: after `gh pr merge`, surface the worktree-teardown
# command if the current dir is a worktree (not the canonical repo).
#
# Why: `gh pr merge --delete-branch` from inside a worktree silently
# fails its local-branch cleanup (worktree-bound branch can't be
# deleted) and leaves the worktree on disk. After many PRs, you have
# 33 dead worktrees. This hook surfaces the cleanup command in stdout.
#
# Does NOT auto-execute — surprise removals are bad. Just prints.
#
# SPEC-INFRA-AI-WORKFLOW-001 REQ-2.

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

# Only act on `gh pr merge` commands.
if ! echo "$COMMAND" | grep -qE 'gh[[:space:]]+pr[[:space:]]+merge\b'; then
    exit 0
fi

# Detect if PWD is a git worktree (and not the canonical repo).
WT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
if [ -z "$WT_ROOT" ]; then
    exit 0
fi

# Walk the worktree list and find the canonical repo (first entry).
CANONICAL=$(git worktree list --porcelain 2>/dev/null | awk '/^worktree / {sub(/^worktree /, ""); print; exit}')
if [ -z "$CANONICAL" ]; then
    exit 0
fi

# Normalize paths for comparison (Windows backslash → forward slash, lowercase).
norm() { echo "$1" | tr '\\' '/' | tr '[:upper:]' '[:lower:]'; }
if [ "$(norm "$WT_ROOT")" = "$(norm "$CANONICAL")" ]; then
    # We're in the canonical repo — no teardown needed.
    exit 0
fi

CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "HEAD")

cat <<EOF

──────────────────────────────────────────────────────────────────
[pr-merge-teardown] After this PR is confirmed MERGED, tear down the
worktree to prevent accumulation:

  cd "$CANONICAL"
  git worktree remove --force "$WT_ROOT"
  git branch -D "$CURRENT_BRANCH"

Verify the merge first:
  gh pr view <pr#> --json state,mergeCommit

See SPEC-INFRA-AI-WORKFLOW-001 REQ-2.
──────────────────────────────────────────────────────────────────

EOF

exit 0
