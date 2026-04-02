#!/usr/bin/env python3
"""PostToolUse hook: CI verification reminder after git push.

Injects a system reminder to verify CI after every push.
Uses additionalContext in hookSpecificOutput per Claude Code docs.

Detailed rollout verification steps: .claude/rules/klai/post-push.md
(loads via paths: when working on deploy/CI files).

SPEC: SPEC-CONFIDENCE-001
"""

import json
import re
import sys


def main():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return

    command = data.get("tool_input", {}).get("command", "")
    # Match git push only as an actual command — at the start or after a
    # shell operator (&&, ;, |) — not inside commit messages or strings.
    if not re.search(r"(?:^|&&|;|\|)\s*git\s+push\b", command):
        return

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": (
                        "[HARD] CI verification required after push.\n"
                        "1. Run: gh run watch --exit-status\n"
                        "2. If it fails: gh run view <run-id> --log-failed "
                        "— fix and re-push\n"
                        "3. For deploy workflows: verify server rollout "
                        "(see .claude/rules/klai/post-push.md)\n"
                        "Do NOT declare the task complete until CI is green."
                    ),
                }
            }
        )
    )


if __name__ == "__main__":
    main()
