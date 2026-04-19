#!/usr/bin/env bash
# PostToolUse hook: CI verification reminder after git push.
# Injects a system reminder to verify CI after every push.
# SPEC: SPEC-CONFIDENCE-001

set -euo pipefail

raw=$(cat)

command=$(echo "$raw" | grep -o '"command":"[^"]*"' | head -1 | sed 's/"command":"//;s/"$//')

# Match git push only — not inside commit messages or strings
if ! echo "$command" | grep -qE '(^|&&|;|\|)[[:space:]]*git[[:space:]]+push\b'; then
    exit 0
fi

cat <<'EOF'
{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"[HARD] CI verification required after push.\n1. Run: gh run watch --exit-status\n2. If it fails: gh run view <run-id> --log-failed — fix and re-push\n3. For deploy workflows: verify server rollout (see .claude/rules/klai/post-push.md)\nDo NOT declare the task complete until CI is green."}}
EOF
