#!/usr/bin/env python3
"""Check text for per-customer data before a gh command publishes it.

The 2026-09-18 leak was in a pull-request body as well as in a commit, and the
git hooks never see a body: it goes straight from the command line to GitHub,
where the edit history keeps every version. This reads the whole pending
command (an inline --body, a heredoc, a title) plus any --body-file / -F file,
and runs scripts/audit-public-tenant-data.py over it.

Claude PreToolUse hooks receive the pending Bash invocation as JSON on stdin;
exit 2 blocks the call and shows stderr to the agent.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

# Only the verbs that publish text. Reading (`gh pr view`, `gh issue list`) must
# stay possible even when the command line itself names a customer, e.g. in a
# grep for the very data being cleaned up; the first version blocked that.
PUBLISHING = re.compile(
    r"\bgh\s+(?:"
    r"(?:pr|issue)\s+(?:create|edit|comment|review|close|reopen)"
    r"|release\s+(?:create|edit)"
    r"|gist\s+(?:create|edit)"
    r"|api\b(?=[^|;&]*(?:\s-[fF]\s|\s--field|\s--raw-field|\s--input|\s-X\s*(?:POST|PATCH|PUT)|\s--method\s*(?:POST|PATCH|PUT)))"
    r")"
)
FILE_FLAGS = {"--body-file", "-F", "--input", "--notes-file"}


def _attached_files(command: str) -> list[str]:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return []
    files = []
    for flag, value in zip(tokens, tokens[1:]):
        if flag in FILE_FLAGS and value != "-":
            # gh api -F key=@file reads a file; -F key=value is a field.
            files.append(value.split("=@", 1)[1] if "=@" in value else value)
    return [f for f in files if Path(f).is_file()]


def main() -> int:
    payload = json.load(sys.stdin)
    command = payload.get("tool_input", {}).get("command", "")
    match = PUBLISHING.search(command)
    if not match:
        return 0
    # From the publishing command on: an inline body or heredoc always follows
    # it, and a body written earlier arrives through the file flags below. What
    # comes before (a grep while cleaning up) is not sent.
    text = command[match.start():]
    for path in _attached_files(command):
        text += "\n\n" + Path(path).read_text(encoding="utf-8", errors="replace")
    root = os.environ.get("CLAUDE_PROJECT_DIR", ".")
    result = subprocess.run(
        [sys.executable, "-B", f"{root}/scripts/audit-public-tenant-data.py", "--text", "gh-command"],
        input=text,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return 0
    sys.stderr.write(result.stdout + result.stderr)
    sys.stderr.write("\nThis text would be published on GitHub. Remove the customer data and try again.\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
