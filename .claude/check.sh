#!/usr/bin/env bash
# Stop-gate: fast repo check. Exit 2 blocks the agent from claiming done.
cd "$(dirname "$0")/.." || exit 0
out=$(make lint 2>&1) || { echo "check gate failed (make lint):" >&2; echo "$out" | tail -20 >&2; exit 2; }
exit 0
