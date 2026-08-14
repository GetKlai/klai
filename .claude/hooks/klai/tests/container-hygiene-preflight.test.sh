#!/usr/bin/env bash
# SPEC-INFRA-CONTAINER-HYGIENE-001 REQ-1 — the preflight hook must actually block.
#
# The hook is the enforcement layer for the librechat-voys class: a markdown rule
# is a gap that reopens with every context truncation, so the block is the only
# thing that survives all failure modes. It had no tests, and that cost a real
# defect: `CLAUDE_PROJECT_DIR` was dereferenced under `set -u` BEFORE the blocking
# checks, so anything running the hook without that variable got an error message
# instead of a guard. It failed open, quietly, with exit 1 rather than 2.
#
# So this suite runs every case twice — with the variable and without it. A guard
# that only guards under one environment is the bug, not the fix.

set -uo pipefail

HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/container-hygiene-preflight.sh"
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

cases() {
    # Klasse C — a running one is an in-progress recording with no other source.
    expect block "docker rm vexa-mtg-5-9a935aa1"     "klasse-C bot workload"
    expect block "docker rm -f vexa-mtg-3-37b44f92"  "klasse-C, flag before target"
    # Klasse B — the original incident.
    expect block "docker rm librechat-voys"          "klasse-B tenant container"
    # Global prunes that have no safe one-shot use.
    expect block "docker volume prune"               "customer data"
    # Reversible and read-only operations must stay out of the way.
    expect allow "docker ps --filter name=vexa-mtg"  "read-only"
    expect allow "docker logs vexa-mtg-5-9a935aa1"   "read-only"
    expect allow "docker stop vexa-mtg-5-9a935aa1"   "reversible"
    # Check 2 is an open policy by design: an unrecognised name is not blocked.
    expect allow "docker rm some-scratch-box"        "no pattern match"
}

echo "── container-hygiene preflight, WITHOUT CLAUDE_PROJECT_DIR ──"
unset CLAUDE_PROJECT_DIR
cases

echo "── container-hygiene preflight, WITH CLAUDE_PROJECT_DIR ──"
export CLAUDE_PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cases

echo "─────────────────────────────────────────────────────────────"
if [ "$FAIL" -eq 0 ]; then
    echo "container-hygiene preflight guard: OK"
else
    echo "container-hygiene preflight guard: FAILED" >&2
fi
exit "$FAIL"
