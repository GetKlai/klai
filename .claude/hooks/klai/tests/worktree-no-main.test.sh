#!/usr/bin/env bash
# SPEC-INFRA-AI-WORKFLOW-001 REQ-1 — the worktree guard must block a worktree that
# CLAIMS main, and must not block the escapes it tells you to use.
#
# It shipped without tests and both escapes were broken. `--detach`, which the
# block message recommends verbatim for a read-only snapshot, hit the block rule
# because nothing exempted it. And the `-b` exemption required a space before the
# flag that `git worktree add[[:space:]]` had already consumed, so it only matched
# `add <path> -b <branch>` and not `add -b <branch> <path>` — the same command
# with its arguments in the other order, which git accepts just as happily.
#
# A guard that blocks its own documented workaround teaches people to work around
# the guard, so every escape here is a test case.

set -uo pipefail

HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/worktree-no-main.sh"
FAIL=0

run() {
    printf '%s' "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"$1\"}}" \
        | bash "$HOOK" >/dev/null 2>&1
    echo $?
}

expect() {
    want="$1"; cmd="$2"; why="$3"
    case "$want" in block) code=2 ;; allow) code=0 ;; esac
    got=$(run "$cmd")
    if [ "$got" = "$code" ]; then
        echo "OK:   $want  $cmd"
    else
        echo "FAIL: expected $want (exit $code), got exit $got — $cmd ($why)" >&2
        FAIL=1
    fi
}

# What the guard exists for: a worktree that checks out main itself.
expect block "git worktree add /tmp/wt main" "bare main claims the branch"
expect block "git worktree add /tmp/wt origin/main" "tracking main claims the branch"
expect block "git worktree add /tmp/wt refs/heads/main" "same branch by full ref"
expect block "git worktree add -q /tmp/wt origin/main" "flags must not hide the ref"

# The escapes the block message itself recommends.
expect allow "git worktree add /tmp/wt -b fix/thing origin/main" "new branch from main"
expect allow "git worktree add -b fix/thing /tmp/wt origin/main" "same, flag before path"
expect allow "git worktree add -B fix/thing /tmp/wt origin/main" "-B creates a branch too"
expect allow "git worktree add --detach /tmp/wt origin/main" "detached never claims main"
expect allow "git worktree add /tmp/wt origin/main --detach" "same, flag last"

# Untouched neighbours.
expect allow "git worktree add /tmp/wt some-feature-branch" "another branch is fine"
expect allow "git worktree list" "not an add command"

exit $FAIL
