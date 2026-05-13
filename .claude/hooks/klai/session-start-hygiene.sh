#!/usr/bin/env bash
# SessionStart hook: hygiene audit. Warn if the local repo is
# accumulating cruft.
#
# Thresholds (SPEC-INFRA-AI-WORKFLOW-001 REQ-4):
#   - More than 5 worktrees besides the canonical repo
#   - More than 3 local branches with `gone` upstream
#   - More than 3 stashes that are NOT prefixed with RESCUE-
#
# Does NOT auto-clean — humans review and decide. Just prints the
# audit + exact cleanup commands when thresholds are exceeded.

set -euo pipefail

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"

cd "$PROJECT_DIR" 2>/dev/null || exit 0

# Skip if not a git repo.
if ! git rev-parse --git-dir >/dev/null 2>&1; then
    exit 0
fi

WT_COUNT=$(git worktree list 2>/dev/null | wc -l)
WT_EXTRA=$((WT_COUNT - 1))  # subtract canonical repo

GONE_COUNT=$(git branch -vv 2>/dev/null | grep -c ': gone\]' || true)

STASH_COUNT=$(git stash list 2>/dev/null | grep -cv 'RESCUE-' || true)

# Always-emit one-line summary.
echo "[hygiene] worktrees=$WT_EXTRA  gone-branches=$GONE_COUNT  non-rescue-stashes=$STASH_COUNT"

WARN=""
if [ "$WT_EXTRA" -gt 5 ]; then
    WARN="${WARN}  ⚠ $WT_EXTRA worktrees besides canonical repo (threshold 5)\n"
    WARN="${WARN}    Audit:    git worktree list\n"
    WARN="${WARN}    Cleanup:  see SPEC-INFRA-AI-WORKFLOW-001\n"
fi
if [ "$GONE_COUNT" -gt 3 ]; then
    WARN="${WARN}  ⚠ $GONE_COUNT local branches with 'gone' upstream (threshold 3)\n"
    WARN="${WARN}    Audit:    git branch -vv | grep ': gone]'\n"
    WARN="${WARN}    Cleanup:  for b in \$(git branch -vv | grep ': gone]' | awk '{print \$1}'); do git branch -D \$b; done\n"
fi
if [ "$STASH_COUNT" -gt 3 ]; then
    WARN="${WARN}  ⚠ $STASH_COUNT non-rescue stashes (threshold 3)\n"
    WARN="${WARN}    Audit:    git stash list\n"
    WARN="${WARN}    Each one: git stash show stash@{N} --stat\n"
fi

if [ -n "$WARN" ]; then
    echo ""
    echo "[hygiene] thresholds exceeded:"
    printf "$WARN"
    echo ""
fi

exit 0
