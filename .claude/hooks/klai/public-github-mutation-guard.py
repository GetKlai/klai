#!/usr/bin/env python3
"""Block autonomous publication to public GitHub issues.

Claude PreToolUse hooks receive the pending Bash invocation as JSON on stdin.
An explicit marker is available for a user-authorized public mutation; repo
instructions define when an agent may use it.
"""

from __future__ import annotations

import json
import re
import sys


APPROVAL_MARKER = "KLAI_ALLOW_PUBLIC_ISSUE_MUTATION=1"

READ_ONLY_ISSUE_VERBS = {"list", "status", "view"}
ISSUE_ENDPOINT = re.compile(
    r"(?:https?://api\.github\.com/)?repos/[^/\s'\"]+/[^/\s'\"]+/issues"
    r"(?:[/ ?'\"]|$)",
    re.IGNORECASE,
)
MUTATING_METHOD = re.compile(
    r"(?:-X|--method|--request)(?:=|\s+)(?:POST|PATCH|PUT|DELETE)\b",
    re.IGNORECASE,
)
BODY_ARGUMENT = re.compile(
    r"(?:^|\s)(?:-d|--data(?:-raw|-binary)?|-f|-F|--raw-field|--field|--input)"
    r"(?:=|\s)",
    re.IGNORECASE,
)
ISSUE_GRAPHQL_MUTATION = re.compile(
    r"\b(?:createIssue|updateIssue|closeIssue|reopenIssue|deleteIssue|addComment)\b",
    re.IGNORECASE,
)


def is_public_issue_mutation(command: str) -> bool:
    for match in re.finditer(r"\bgh\s+issue\s+([a-z-]+)\b", command, re.IGNORECASE):
        if match.group(1).lower() not in READ_ONLY_ISSUE_VERBS:
            return True

    if re.search(r"\bgh\s+api\b", command, re.IGNORECASE):
        if ISSUE_ENDPOINT.search(command) and (
            MUTATING_METHOD.search(command) or BODY_ARGUMENT.search(command)
        ):
            return True
        if re.search(r"\bgraphql\b", command, re.IGNORECASE) and (
            ISSUE_GRAPHQL_MUTATION.search(command)
        ):
            return True

    if re.search(r"\bcurl\b", command, re.IGNORECASE) and ISSUE_ENDPOINT.search(command):
        if MUTATING_METHOD.search(command) or BODY_ARGUMENT.search(command):
            return True

    return False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0

    command = payload.get("tool_input", {}).get("command", "")
    if not isinstance(command, str) or APPROVAL_MARKER in command:
        return 0

    if not is_public_issue_mutation(command):
        return 0

    print(
        "BLOCKED: autonomous public GitHub issue mutation.\n\n"
        "This repository is public. Report backlog and security findings in "
        "the private conversation. Only when the user's current request "
        "explicitly authorizes this exact public mutation may the command be "
        f"retried with {APPROVAL_MARKER}.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
