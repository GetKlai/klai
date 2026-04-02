#!/usr/bin/env python3
"""PostToolUse hook: CI verification reminder after git push.

Injects a reminder to verify CI passes after every push.
Detailed rollout verification steps are in post-push.md
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

    tool_name = data.get("tool_name", "")
    if tool_name != "Bash":
        return

    command = data.get("tool_input", {}).get("command", "")
    if not re.search(r"\bgit\s+push\b", command):
        return

    print(
        "[HARD] CI verification required after push.\n"
        "1. Run: gh run watch --exit-status\n"
        "2. If it fails: gh run view <run-id> --log-failed — fix and re-push\n"
        "3. For deploy workflows: verify server rollout "
        "(see .claude/rules/klai/post-push.md)\n"
        "Do NOT declare the task complete until CI is green."
    )


if __name__ == "__main__":
    main()
