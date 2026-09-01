#!/usr/bin/env python3
"""Block autonomous public GitHub issue, PR, and main-branch mutations.

Claude PreToolUse hooks receive the pending Bash invocation as JSON on stdin.
An explicit marker is available for a user-authorized public mutation; repo
instructions define when an agent may use it.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys


APPROVAL_MARKER = "KLAI_ALLOW_PUBLIC_ISSUE_MUTATION=1"
CODE_APPROVAL_MARKER = "KLAI_ALLOW_PUBLIC_CODE_MUTATION=1"

READ_ONLY_ISSUE_VERBS = {"list", "status", "view"}
READ_ONLY_PR_VERBS = {"checks", "checkout", "diff", "list", "status", "view"}
ISSUE_ENDPOINT = re.compile(
    r"(?:https?://api\.github\.com/)?repos/[^/\s'\"]+/[^/\s'\"]+/issues"
    r"(?:[/ ?'\"]|$)",
    re.IGNORECASE,
)
PULL_ENDPOINT = re.compile(
    r"(?:https?://api\.github\.com/)?repos/[^/\s'\"]+/[^/\s'\"]+/pulls"
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
PULL_GRAPHQL_MUTATION = re.compile(
    r"\b(?:addPullRequestReview|closePullRequest|convertPullRequestToDraft|"
    r"disablePullRequestAutoMerge|enablePullRequestAutoMerge|mergePullRequest|"
    r"markPullRequestReadyForReview|reopenPullRequest|updatePullRequest)\b",
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


def is_public_pr_mutation(command: str) -> bool:
    for match in re.finditer(r"\bgh\s+pr\s+([a-z-]+)\b", command, re.IGNORECASE):
        if match.group(1).lower() not in READ_ONLY_PR_VERBS:
            return True

    if re.search(r"\bgh\s+api\b", command, re.IGNORECASE):
        if PULL_ENDPOINT.search(command) and (
            MUTATING_METHOD.search(command) or BODY_ARGUMENT.search(command)
        ):
            return True
        if re.search(r"\bgraphql\b", command, re.IGNORECASE) and (
            PULL_GRAPHQL_MUTATION.search(command)
        ):
            return True

    if re.search(r"\bcurl\b", command, re.IGNORECASE) and PULL_ENDPOINT.search(command):
        if MUTATING_METHOD.search(command) or BODY_ARGUMENT.search(command):
            return True

    return False


def _push_arguments(command: str) -> list[list[str]]:
    invocations: list[list[str]] = []
    for match in re.finditer(r"\bgit\s+push\b", command, re.IGNORECASE):
        invocation = re.split(r"&&|\|\||[;|\n]", command[match.start() :], maxsplit=1)[0]
        try:
            tokens = shlex.split(invocation)
        except ValueError:
            tokens = invocation.replace("'", "").replace('"', "").split()
        if len(tokens) >= 2:
            invocations.append(tokens[2:])
    return invocations


def _current_git_branch() -> str | None:
    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        # Branch state is contextual evidence only. Fail open when it cannot be
        # resolved; explicit main destinations are still blocked above.
        return None
    if result.returncode != 0:
        # Detached HEAD and non-repository working directories have no branch.
        # Fail open because the command string alone cannot resolve HEAD safely.
        return None
    branch = result.stdout.strip()
    return branch or None


def is_public_main_push(command: str, current_branch: str | None = None) -> bool:
    for arguments in _push_arguments(command):
        if any(argument in {"--all", "--mirror"} for argument in arguments):
            return True

        for index, argument in enumerate(arguments):
            normalized = argument.lstrip("+")
            if normalized.startswith("--delete="):
                destination = normalized.split("=", 1)[1]
                if destination in {"main", "refs/heads/main"}:
                    return True
            if ":" in normalized:
                destination = normalized.rsplit(":", 1)[1]
                if destination in {"main", "refs/heads/main"}:
                    return True
            if (
                normalized in {"main", "refs/heads/main"}
                and index > 0
                and ":" not in normalized
            ):
                return True

        if current_branch == "main":
            positionals = [
                argument for argument in arguments if not argument.startswith("-")
            ]
            refspecs = positionals[1:]
            if not refspecs or any(
                refspec.lstrip("+") in {"@", "HEAD"} for refspec in refspecs
            ):
                return True

    return False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0

    command = payload.get("tool_input", {}).get("command", "")
    if not isinstance(command, str):
        return 0

    if APPROVAL_MARKER not in command and is_public_issue_mutation(command):
        print(
            "BLOCKED: autonomous public GitHub issue mutation.\n\n"
            "This repository is public. Report backlog and security findings in "
            "the private conversation. Only when the user's current request "
            "explicitly authorizes this exact public mutation may the command be "
            f"retried with {APPROVAL_MARKER}.",
            file=sys.stderr,
        )
        return 2

    if CODE_APPROVAL_MARKER not in command and is_public_pr_mutation(command):
        print(
            "BLOCKED: autonomous public GitHub PR mutation.\n\n"
            "A public PR is a disclosure surface. Only when the user's current "
            "request explicitly authorizes this exact public mutation may the "
            f"command be retried with {CODE_APPROVAL_MARKER}.",
            file=sys.stderr,
        )
        return 2

    if CODE_APPROVAL_MARKER not in command and is_public_main_push(
        command, _current_git_branch()
    ):
        print(
            "BLOCKED: public main branch push.\n\n"
            "Direct publication to main requires explicit authorization. Routine "
            "feature-branch pushes remain allowed. Retry an authorized main push "
            f"with {CODE_APPROVAL_MARKER}.",
            file=sys.stderr,
        )
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
