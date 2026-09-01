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
from typing import NamedTuple

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

SHELL_WORD = r"""(?:'[^']*'|"(?:\\.|[^"])*"|(?:\\.|[^\s;&|])+)"""
GIT_PUSH = re.compile(
    rf"\bgit(?P<git_options>(?:\s+-C\s+{SHELL_WORD})*)\s+push\b",
    re.IGNORECASE,
)
UNPARSED_GIT_PUSH = re.compile(r"\bgit\b[^;&|\n]*\bpush\b", re.IGNORECASE)
PUSH_FLAG_OPTIONS = {
    "-f",
    "--force",
    "-u",
    "--set-upstream",
    "--force-if-includes",
    "--no-force-if-includes",
    "--atomic",
    "--no-atomic",
    "-n",
    "--dry-run",
    "--porcelain",
    "--prune",
    "--no-prune",
    "--follow-tags",
    "--no-follow-tags",
    "--signed",
    "--no-signed",
    "--ipv4",
    "--ipv6",
    "-q",
    "--quiet",
    "-v",
    "--verbose",
    "--verify",
    "--no-verify",
    "--progress",
    "--no-progress",
    "--thin",
    "--no-thin",
}


class PushInvocation(NamedTuple):
    arguments: list[str] | None
    git_options: list[str]
    cwd: str | None
    branch_context_known: bool


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


def _branch_context(command: str, git_start: int) -> tuple[str | None, bool]:
    prefix = command[:git_start]
    if not prefix.strip():
        return None, True
    has_shell_context = re.search(r"&&|\|\||[;|\n]", prefix) is not None
    try:
        tokens = shlex.split(prefix)
    except ValueError:
        starts_with_cd = re.match(r"^\s*cd\b", prefix, re.IGNORECASE) is not None
        return None, not (starts_with_cd or has_shell_context)
    if len(tokens) == 3 and tokens[0].lower() == "cd" and tokens[2] == "&&":
        return tokens[1], True
    if has_shell_context or any(token.lower() == "cd" for token in tokens):
        return None, False
    return None, True


def _push_invocations(command: str) -> list[PushInvocation]:
    invocations: list[PushInvocation] = []
    parsed_starts: set[int] = set()
    for match in GIT_PUSH.finditer(command):
        parsed_starts.add(match.start())
        invocation = re.split(r"&&|\|\||[;|\n]", command[match.start() :], maxsplit=1)[
            0
        ]
        cwd, branch_context_known = _branch_context(command, match.start())
        if not branch_context_known:
            invocations.append(PushInvocation(None, [], None, False))
            continue
        try:
            tokens = shlex.split(invocation)
        except ValueError:
            invocations.append(PushInvocation(None, [], cwd, False))
            continue
        try:
            push_index = len(shlex.split(match.group(0))) - 1
        except ValueError:
            invocations.append(PushInvocation(None, [], cwd, False))
            continue
        invocations.append(
            PushInvocation(
                tokens[push_index + 1 :],
                tokens[1:push_index],
                cwd,
                branch_context_known,
            )
        )
    for match in UNPARSED_GIT_PUSH.finditer(command):
        if match.start() not in parsed_starts:
            invocations.append(PushInvocation(None, [], None, False))
    return invocations


def _current_git_branch(git_options: list[str], cwd: str | None) -> str | None:
    try:
        result = subprocess.run(
            ["git", *git_options, "symbolic-ref", "--quiet", "--short", "HEAD"],
            capture_output=True,
            check=False,
            cwd=cwd,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    branch = result.stdout.strip()
    return branch or None


def _push_refspecs(arguments: list[str]) -> tuple[list[str] | None, bool]:
    positionals: list[str] = []
    delete = False
    index = 0
    options_done = False
    while index < len(arguments):
        argument = arguments[index]
        if not options_done and argument == "--":
            options_done = True
        elif not options_done and argument in {"--all", "--mirror"}:
            return None, True
        elif not options_done and argument == "--delete":
            delete = True
        elif not options_done and argument.startswith("--delete="):
            positionals.append(argument.split("=", 1)[1])
            delete = True
        elif not options_done and (
            argument in PUSH_FLAG_OPTIONS
            or argument == "--force-with-lease"
            or argument.startswith(("--force-with-lease=", "--signed="))
        ):
            pass
        elif not options_done and argument.startswith("-"):
            return None, True
        else:
            positionals.append(argument)
        index += 1

    if delete:
        if not positionals:
            return None, True
        return positionals[1:] if len(positionals) > 1 else None, False
    return positionals[1:] if positionals else [], False


def _destination_for_refspec(refspec: str, invocation: PushInvocation) -> str | None:
    normalized = refspec.removeprefix("+")
    if "*" in normalized:
        return None
    if ":" in normalized:
        destination = normalized.rsplit(":", 1)[1]
        return destination or None
    if normalized not in {"@", "HEAD"}:
        return normalized
    if not invocation.branch_context_known:
        return None
    return _current_git_branch(invocation.git_options, invocation.cwd)


def is_public_main_push(command: str) -> bool:
    for invocation in _push_invocations(command):
        if invocation.arguments is None:
            return True
        refspecs, must_block = _push_refspecs(invocation.arguments)
        if must_block:
            return True
        if refspecs is None:
            return True
        if not refspecs:
            if not invocation.branch_context_known:
                return True
            branch = _current_git_branch(invocation.git_options, invocation.cwd)
            if branch is None:
                return True
            refspecs = [branch]
        for refspec in refspecs:
            destination = _destination_for_refspec(refspec, invocation)
            if destination is None:
                return True
            if destination in {"main", "refs/heads/main"}:
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

    if CODE_APPROVAL_MARKER not in command and is_public_main_push(command):
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
