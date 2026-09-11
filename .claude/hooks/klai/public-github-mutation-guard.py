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


# What this can and cannot do, stated plainly: it reads a bash string and
# decides where the command lands. Arbitrary shell can always compute a
# destination this cannot see, so this is a guard against MISREADING a
# sentence -- the failure that actually happened -- not against someone
# determined to get around it. Everything unclear therefore blocks, and the
# marker is the way through.

# An API call names the owner in its path rather than in a flag.
_REPO_PATH = re.compile(r"(?:^|[\s'\"/])repos/([^/\s'\"]+)/[^/\s'\"]+")
# gh takes [HOST/]OWNER/REPO, and a pull-request URL wherever a number fits.
_OWNER_OF = re.compile(
    r"^['\"]?(?:[A-Za-z0-9.-]+/)?([A-Za-z0-9][A-Za-z0-9-]*)/[^/\s'\"]+['\"]?$"
)
_PR_URL = re.compile(
    r"https?://[^/\s]*github\.com/([A-Za-z0-9][A-Za-z0-9-]*)/[^/\s]+/pull/",
    re.IGNORECASE,
)

# gh's own short forms. Without these, `gh pr new` is an unknown verb and
# `gh pr ls` looks like a mutation.
_PR_VERB_ALIASES = {"new": "create", "co": "checkout", "ls": "list"}

# A directory change means the cwd we were handed no longer describes where
# the command runs. `-` and `~` are here too: they change it just as much.
_CHANGES_DIRECTORY = re.compile(r"(?:^|[\s;&|(])(?:cd|pushd|popd)(?:\s|$)")
# A mutating gh pr verb, or the API routes that do the same thing.
_MUTATING_PR = re.compile(r"\bgh\s+pr\s+([a-z-]+)\b", re.IGNORECASE)


def _segments(command: str) -> list[str]:
    """Split into shell invocations, so one command cannot vouch for another.

    `gh pr view --repo GetKlai/klai && gh pr merge 42` names our repository
    exactly once, on the read-only half. Judging the merge on it is how a
    whole-string scan clears a mutation it never looked at.
    """
    return [part for part in re.split(r"&&|\|\||[;\n|]", command) if part.strip()]


def _named_owners(segment: str) -> tuple[set[str], bool]:
    """Owners this segment names, and whether any naming could not be read.

    Tokenised rather than scanned, because the difference matters: in
    ``--body 'see --repo GetKlai/klai please'`` the body is ONE token and the
    flag never appears on its own, while ``--repo GetKlai/klai`` is two real
    tokens.

    The second half of the return matters as much as the first: ``-R
    "$TARGET"`` names a repository this cannot resolve, and calling that "none
    named" would quietly fall back to the local checkout.
    """
    owners: set[str] = set()
    unresolved = False

    def record(value: str) -> None:
        nonlocal unresolved
        match = _OWNER_OF.match(value)
        if match is None:
            unresolved = True
        else:
            owners.add(match.group(1).lower())

    try:
        tokens = shlex.split(segment, comments=True)
    except ValueError:
        return set(), True

    index = 0
    while index < len(tokens):
        token = tokens[index]
        # `-R` selects a repository; lowercase `-r` is reviewer on create and
        # rebase on merge. Case matters here, so it is not folded away.
        if token in {"--repo", "-R"}:
            record(tokens[index + 1] if index + 1 < len(tokens) else "")
            index += 2
            continue
        if token.startswith("-R") and len(token) > 2:
            record(token[2:])          # gh accepts the value glued on
        elif token.startswith("--repo="):
            record(token[len("--repo="):])
        elif token.startswith("GH_REPO="):
            record(token[len("GH_REPO="):])
        else:
            url = _PR_URL.match(token.strip("'\""))
            if url:
                owners.add(url.group(1).lower())
        index += 1

    return owners, unresolved


def _path_owners(segment: str) -> set[str]:
    """Owners from a `repos/<owner>/<name>` API path, for gh api and curl only.

    Raw text rather than tokens, so it can only raise suspicion, never clear
    it -- and it is read only where such a path is the destination, so the
    same string quoted inside a PR body does not block an internal action.
    """
    if not re.search(r"\b(?:gh\s+api|curl)\b", segment, re.IGNORECASE):
        return set()
    return {m.group(1).lower() for m in _REPO_PATH.finditer(segment)}


def _origin_owner(cwd: str | None) -> str | None:
    """Owner of the repository ``gh`` would act on in ``cwd``, or None.

    ``gh repo set-default`` records its choice as `remote.<name>.gh-resolved`,
    and gh prefers it over the remote URL. The value is either `base` -- that
    remote IS the choice -- or an explicit `owner/name`. Reading only
    `origin` misses a default pointed at an upstream entirely.
    """
    if not cwd:
        return None

    def git(*args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", cwd, *args],
                capture_output=True, text=True, timeout=5, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    resolved = git("config", "--get-regexp", r"remote\..*\.gh-resolved")
    remote = "origin"
    if resolved:
        key, _, value = resolved.splitlines()[0].partition(" ")
        if value.strip() and value.strip() != "base":
            match = _OWNER_OF.match(value.strip())
            return match.group(1).lower() if match else None
        parts = key.split(".")
        if len(parts) >= 3:
            remote = ".".join(parts[1:-1])

    url = git("remote", "get-url", remote)
    if not url:
        return None
    match = re.search(r"(?:github\.com[:/])([^/\s]+)/", url)
    return match.group(1).lower() if match else None


def targets_another_org(segment: str, cwd: str | None, whole: str) -> bool:
    """True unless everything visible says this segment lands inside GetKlai.

    Fail-closed by construction. Being wrong this way costs one sentence
    asking the user; being wrong the other way costs a stranger their inbox.
    """
    owners, unresolved = _named_owners(segment)
    if unresolved:
        return True
    if any(owner != OUR_ORG for owner in owners | _path_owners(segment)):
        return True
    if owners:
        # A tokenised repository selector IS the destination, so this settles
        # it without asking the checkout.
        return False
    # gh also reads GH_REPO from the environment it inherits.
    inherited = os.environ.get("GH_REPO", "")
    if inherited:
        match = _OWNER_OF.match(inherited)
        return match is None or match.group(1).lower() != OUR_ORG
    if _CHANGES_DIRECTORY.search(whole):
        # The cwd we were handed no longer describes where this runs, and
        # nothing named a repository outright.
        return True
    return _origin_owner(cwd) != OUR_ORG


def is_public_pr_mutation(command: str, cwd: str | None = None) -> bool:
    """Does this command mutate a pull request somewhere that is not ours?

    Judged per shell segment, so a read-only invocation cannot vouch for a
    mutating one standing next to it.
    """
    for segment in _segments(command):
        mutates = False

        for match in _MUTATING_PR.finditer(segment):
            verb = _PR_VERB_ALIASES.get(match.group(1).lower(), match.group(1).lower())
            if verb in READ_ONLY_PR_VERBS:
                continue
            tail = segment[match.end():]
            if verb == "ready" and re.search(r"(?:^|\s)--undo(?:\s|$)", tail):
                # Putting a PR BACK to draft is the retreat, never the
                # publication.
                continue
            mutates = True
            break

        if not mutates and re.search(r"\bgh\s+api\b", segment, re.IGNORECASE):
            if re.search(r"\bgraphql\b", segment, re.IGNORECASE) and (
                PULL_GRAPHQL_MUTATION.search(segment)
            ):
                # A GraphQL mutation addresses an opaque node id, so nothing
                # in the command says which repository it lands in. Unknown is
                # elsewhere: the one shape where our checkout proves nothing.
                return True
            mutates = bool(
                PULL_ENDPOINT.search(segment)
                and (MUTATING_METHOD.search(segment) or BODY_ARGUMENT.search(segment))
            )

        if not mutates and re.search(r"\bcurl\b", segment, re.IGNORECASE):
            mutates = bool(
                PULL_ENDPOINT.search(segment)
                and (MUTATING_METHOD.search(segment) or BODY_ARGUMENT.search(segment))
            )

        # Resolved only once a segment turns out to be worth gating: this hook
        # runs before EVERY bash command, and `git remote get-url` on each
        # `echo hello` is a subprocess nobody asked for.
        if mutates and targets_another_org(segment, cwd, command):
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
