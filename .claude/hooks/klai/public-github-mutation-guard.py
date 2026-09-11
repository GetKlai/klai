#!/usr/bin/env python3
"""Block autonomous GitHub issue mutations, and anything aimed at a repo
that is not ours.

Claude PreToolUse hooks receive the pending Bash invocation as JSON on stdin.
An explicit marker is available for a user-authorized public mutation; repo
instructions define when an agent may use it.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from typing import NamedTuple

APPROVAL_MARKER = "KLAI_ALLOW_PUBLIC_ISSUE_MUTATION=1"
CODE_APPROVAL_MARKER = "KLAI_ALLOW_PUBLIC_CODE_MUTATION=1"

READ_ONLY_ISSUE_VERBS = {"list", "status", "view"}

# What this gate asks changed on 2026-09-11: WHERE a command is aimed, not
# which verb it uses.
#
# Inside GetKlai it blocks nothing. Klai has three people, all admins, and main
# is already gated by branch protection -- a PR, a green `quality` check (which
# itself waits on every affected service job), no force-push, no deletion. A
# marker the one developer types on every merge is not a second opinion, it is
# a keystroke. It also self-authorised once: a PR body that merely MENTIONED
# the marker satisfied the match.
#
# Outside GetKlai it blocks what lands on someone else's doorstep. Pushing a
# branch to your own fork notifies nobody; a pull request does. Not
# hypothetical: one was opened at unclecode/crawl4ai on 2026-09-11 from a
# sentence read as permission, and nothing here stopped it -- `gh pr create`
# was on the read-only list.
#
# There is deliberately no exemption for `--draft`. A draft still needs the
# user to have asked for one, and the exemption was where the holes were: a
# body reading "use --draft next time" satisfied it, and so did a `--draft` in
# a later command on the same line. One rule that always asks beats an
# exception that can be spelled six ways.
READ_ONLY_PR_VERBS = {"checks", "checkout", "diff", "list", "status", "view"}

OUR_ORG = "getklai"
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


class Heredoc(NamedTuple):
    delimiter: str
    strip_tabs: bool


def _heredocs_declared_on(line: str) -> list[Heredoc]:
    heredocs: list[Heredoc] = []
    index = 0
    while index < len(line):
        character = line[index]
        if character == "\\":
            index += 2
            continue
        if line.startswith("$((", index) or line.startswith("((", index):
            cursor = index + (3 if line.startswith("$((", index) else 2)
            depth = 1
            while cursor < len(line) and depth:
                if line.startswith("((", cursor):
                    depth += 1
                    cursor += 2
                elif line.startswith("))", cursor):
                    depth -= 1
                    cursor += 2
                elif line[cursor] == "\\":
                    cursor += 2
                else:
                    cursor += 1
            index = cursor
            continue
        if character in {"'", '"'}:
            quote = character
            index += 1
            while index < len(line):
                if quote == '"' and line[index] == "\\":
                    index += 2
                elif line[index] == quote:
                    index += 1
                    break
                else:
                    index += 1
            continue
        if character == "#" and (
            index == 0 or line[index - 1].isspace() or line[index - 1] in ";|&()"
        ):
            break
        if not line.startswith("<<", index) or line.startswith("<<<", index):
            index += 1
            continue

        cursor = index + 2
        strip_tabs = cursor < len(line) and line[cursor] == "-"
        if strip_tabs:
            cursor += 1
        while cursor < len(line) and line[cursor] in " \t":
            cursor += 1
        if cursor >= len(line) or line[cursor] in "\r\n;&|<>()":
            index = cursor
            continue

        if line[cursor] in {"'", '"'}:
            quote = line[cursor]
            cursor += 1
            delimiter_start = cursor
            while cursor < len(line) and line[cursor] != quote:
                cursor += 1
            if cursor >= len(line):
                index = cursor
                continue
            delimiter = line[delimiter_start:cursor]
            cursor += 1
            supported = quote == "'" or "\\" not in delimiter
            supported = supported and (
                cursor >= len(line)
                or line[cursor].isspace()
                or line[cursor] in ";&|<>()"
            )
        else:
            delimiter_start = cursor
            while cursor < len(line) and not (
                line[cursor].isspace() or line[cursor] in ";&|<>()"
            ):
                cursor += 1
            delimiter = line[delimiter_start:cursor]
            supported = not any(character in delimiter for character in "'\"\\")

        if delimiter and supported:
            heredocs.append(Heredoc(delimiter, strip_tabs))
        index = cursor
    return heredocs


def _without_heredoc_bodies(command: str) -> str:
    cleaned_lines: list[str] = []
    pending: list[Heredoc] = []
    for line in command.splitlines(keepends=True):
        if not pending:
            cleaned_lines.append(line)
            pending.extend(_heredocs_declared_on(line))
            continue

        content = line.rstrip("\r\n")
        line_ending = line[len(content) :]
        candidate = content.lstrip("\t") if pending[0].strip_tabs else content
        if candidate == pending[0].delimiter:
            pending.pop(0)
        cleaned_lines.append(line_ending)
    return "".join(cleaned_lines)


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


# gh accepts several ways to name a repository, and a guard that knows only
# the longest one is a guard with a documented bypass.
# An API call names the owner in its path rather than in a flag.
_REPO_PATH = re.compile(r"(?:^|[\s'\"/])repos/([^/\s'\"]+)/[^/\s'\"]+")
_OWNER_OF = re.compile(r"^['\"]?([A-Za-z0-9][A-Za-z0-9-]*)/[^/\s'\"]+['\"]?$")

# gh's own short forms. Without these, `gh pr new` is an unknown verb and
# `gh pr ls` looks like a mutation.
_PR_VERB_ALIASES = {"new": "create", "co": "checkout", "ls": "list"}

# A directory change means the cwd we were handed no longer describes where
# the command runs, and a shell variable means the destination is not in the
# text at all. Neither can be resolved here, and unresolved is elsewhere.
_CHANGES_DIRECTORY = re.compile(r"(?:^|[\s;&|(])cd\s", re.IGNORECASE)


def _named_owners(command: str) -> tuple[set[str], bool]:
    """Owners named in the text, and whether any naming could not be read.

    Tokenised rather than scanned, because the difference matters: in
    ``--body 'see --repo GetKlai/klai please'`` the body is ONE token and the
    flag never appears on its own, while ``--repo GetKlai/klai`` is two real
    tokens. Regex on the raw string cannot tell those apart, and read the
    first as a GetKlai destination.

    The second half of the return matters as much as the first: ``-R
    "$TARGET"`` names a repository this cannot resolve, and calling that "none
    named" would quietly fall back to the local checkout.
    """
    owners: set[str] = set()
    unresolved = False

    try:
        tokens = shlex.split(command, comments=False)
    except ValueError:
        # Unparseable shell. We cannot see the flags, so we have not proved
        # anything about where this lands.
        return set(), True

    flags = {"--repo", "-r"}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        lowered = token.lower()
        value: str | None = None
        if lowered in flags or lowered == "-r":
            value = tokens[index + 1] if index + 1 < len(tokens) else ""
            index += 1
        elif "=" in token:
            name, _, rest = token.partition("=")
            if name.lower() in flags or name == "GH_REPO":
                value = rest
        if value is not None:
            match = _OWNER_OF.match(value)
            if match is None:
                unresolved = True
            else:
                owners.add(match.group(1).lower())
        index += 1

    return owners, unresolved


def _path_owners(command: str) -> set[str]:
    """Owners appearing as ``repos/<owner>/<name>``, from the raw text.

    Used only to raise suspicion, never to clear it: unlike a tokenised flag,
    this shape can sit inside a quoted argument, so a GetKlai name here proves
    nothing about where the command lands.
    """
    return {m.group(1).lower() for m in _REPO_PATH.finditer(command)}


def _origin_owner(cwd: str | None) -> str | None:
    """Owner of the repository ``gh`` would act on in ``cwd``, or None.

    ``gh repo set-default`` records its choice in ``remote.<name>.gh-resolved``
    and gh prefers it over the remote URL, so reading only the URL would miss
    a default pointed somewhere else entirely.
    """
    if not cwd:
        return None
    for args in (
        ["config", "--get-regexp", r"remote\..*\.gh-resolved"],
        ["remote", "get-url", "origin"],
    ):
        try:
            result = subprocess.run(
                ["git", "-C", cwd, *args],
                capture_output=True, text=True, timeout=5, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            continue
        text = result.stdout.strip()
        if not text:
            continue
        # gh-resolved is either "base" (meaning origin) or "owner/name".
        match = re.search(r"(?:github\.com[:/])([^/\s]+)/", text) or re.search(
            r"\s([A-Za-z0-9][A-Za-z0-9-]*)/[^/\s]+$", text
        )
        if match:
            return match.group(1).lower()
    return None


def targets_another_org(command: str, cwd: str | None) -> bool:
    """True unless everything visible says this lands inside GetKlai.

    Fail-closed by construction. Being wrong this way costs one sentence
    asking the user; being wrong the other way costs a stranger their inbox.
    """
    flag_owners, unresolved = _named_owners(command)
    if unresolved:
        return True
    if any(owner != OUR_ORG for owner in flag_owners | _path_owners(command)):
        return True
    if flag_owners:
        # A tokenised --repo is the destination itself, so this settles it
        # without asking the checkout.
        return False
    if _CHANGES_DIRECTORY.search(command):
        # The cwd we were handed no longer describes where this runs, and
        # nothing named a repository outright.
        return True
    return _origin_owner(cwd) != OUR_ORG


def is_public_pr_mutation(command: str, cwd: str | None = None) -> bool:
    elsewhere: bool | None = None

    def aimed_elsewhere() -> bool:
        # Resolved at most once, and only once something worth gating is
        # found: this hook runs before EVERY bash command, and `git remote
        # get-url` on each `echo hello` is a subprocess nobody asked for.
        nonlocal elsewhere
        if elsewhere is None:
            elsewhere = targets_another_org(command, cwd)
        return elsewhere

    # Bounded to one invocation: a greedy tail let `gh pr view && gh pr create`
    # be judged entirely on the `view`.
    for match in re.finditer(
        r"\bgh\s+pr\s+([a-z-]+)\b([^\n;&|]*)", command, re.IGNORECASE
    ):
        verb = _PR_VERB_ALIASES.get(match.group(1).lower(), match.group(1).lower())
        if verb in READ_ONLY_PR_VERBS:
            continue
        if verb == "ready" and re.search(r"(?:^|\s)--undo(?:\s|$)", match.group(2)):
            # Putting a PR BACK to draft is the retreat, never the publication.
            continue
        if aimed_elsewhere():
            return True

    # The same publications reached through the API rather than the CLI.
    if re.search(r"\bgh\s+api\b", command, re.IGNORECASE):
        if (
            PULL_ENDPOINT.search(command)
            and (MUTATING_METHOD.search(command) or BODY_ARGUMENT.search(command))
            and aimed_elsewhere()
        ):
            return True
        if re.search(r"\bgraphql\b", command, re.IGNORECASE) and (
            PULL_GRAPHQL_MUTATION.search(command)
        ):
            # A GraphQL mutation addresses an opaque node id, so nothing in
            # the command says which repository it lands in. Unknown is
            # elsewhere: this is the one shape where our own checkout proves
            # nothing at all.
            return True

    if (
        re.search(r"\bcurl\b", command, re.IGNORECASE)
        and PULL_ENDPOINT.search(command)
        and (MUTATING_METHOD.search(command) or BODY_ARGUMENT.search(command))
        and aimed_elsewhere()
    ):
        return True

    return False


def _is_authorized(command: str, marker: str) -> bool:
    """True only when ``marker`` is set as an environment assignment.

    A plain ``marker in command`` also matches the marker appearing as DATA:
    a PR body explaining the marker, a commit message quoting it, a doc edit
    documenting it. Writing about the escape hatch then opens it. Heredoc
    bodies were already stripped before this point for the same reason; a
    quoted ``--body`` argument was not.

    So the marker has to survive shlex tokenising as its own bare word, in the
    assignment position: leading, or after another assignment, or right after
    a command separator. ``KLAI_...=1 gh pr create`` authorises;
    ``gh pr create --body "about KLAI_...=1"`` does not, because shlex hands
    that back as one token with the surrounding prose attached.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        # Unparseable quoting: fail closed, the caller will block.
        return False
    expect_assignment = True
    for token in tokens:
        if token in {"&&", "||", ";", "|", "&", "(", ")", "{", "}"}:
            expect_assignment = True
            continue
        if expect_assignment and token == marker:
            return True
        # An assignment keeps us in the prefix position; anything else ends it.
        expect_assignment = expect_assignment and "=" in token and not token.startswith("-")
    return False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0

    command = payload.get("tool_input", {}).get("command", "")
    if not isinstance(command, str):
        return 0
    command = _without_heredoc_bodies(command)

    if not _is_authorized(command, APPROVAL_MARKER) and is_public_issue_mutation(command):
        print(
            "BLOCKED: autonomous public GitHub issue mutation.\n\n"
            "This repository is public. Report backlog and security findings in "
            "the private conversation. Only when the user's current request "
            "explicitly authorizes this exact public mutation may the command be "
            f"retried with {APPROVAL_MARKER}.",
            file=sys.stderr,
        )
        return 2

    # Claude sends the invocation's cwd; falling back to ours keeps the hook
    # correct when it is run directly, where the process cwd IS the checkout.
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        cwd = os.getcwd()

    if not _is_authorized(command, CODE_APPROVAL_MARKER) and is_public_pr_mutation(command, cwd):
        print(
            "BLOCKED: this may land in a repository outside GetKlai.\n\n"
            "Pushing a branch to a fork notifies nobody; a pull request puts "
            "it in front of someone else's maintainers, and that is the "
            "user's call, not yours. Ask, then do exactly what they said -- "
            "including whether it opens as a draft. This also fires when the "
            "destination cannot be read from the command, which is deliberate: "
            "unproven is treated as elsewhere. Only when the user's current "
            "request authorizes this exact publication may the command be "
            f"retried with {CODE_APPROVAL_MARKER}.",
            file=sys.stderr,
        )
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
