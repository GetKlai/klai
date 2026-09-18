#!/usr/bin/env python3
"""Block per-customer usage data from entering this public repository.

On 2026-09-18 a table naming five tenants with their monthly internal-chat
volume and how many of those answers carried a citation was committed here and
pushed. It never reached main and the branch was deleted, but the commit stays
reachable by SHA through the closed pull request, and GitHub keeps the edit
history of the PR body. That is the failure this guard exists for: the damage is
done at ``git push``, so a CI check that runs afterwards can only report it.

What it looks for, in ADDED lines only, so existing content is never re-judged:

1. A Markdown table whose header names tenants, organisations or customers and
   whose rows carry numbers. That is the exact artefact that leaked: a name and
   a count on the same row.
2. A LibreChat database name (``librechat-<slug>``) that does not already appear
   on the default branch. Those names are per-customer by construction.

What it deliberately does NOT do: carry a list of customer names. Such a list
would itself be the disclosure, and it would go stale. The shape is enough.

Run by .githooks/pre-commit on staged files, and by CI as a backstop for commits
made without the hook installed.
"""

from __future__ import annotations

import re
import subprocess
import sys

# A header cell that means "this row is about one organisation".
_TENANT_HEADER = re.compile(
    r"\|[^|]*\b(tenant|tenants|klant|klanten|organisatie|organisaties|customer|customers|org)\b[^|]*\|",
    re.IGNORECASE,
)
_TABLE_ROW = re.compile(r"^\+\s*\|.*\|\s*$")
_HAS_NUMBER = re.compile(r"\|\s*\*{0,2}\d[\d.,]*\*{0,2}\s*\|")
_LIBRECHAT_DB = re.compile(r"\blibrechat-([a-z0-9][a-z0-9-]{2,})\b")

_ADVICE = (
    "Per-customer figures belong in the private klai-infra documentation.\n"
    "  Keep the shape of the distribution here if the decision needs it, without the\n"
    "  names or the counts: \"one tenant accounted for almost all cited answers; three had none\"."
)


def _added_lines(diff: str) -> list[tuple[str, str]]:
    """(file, added line) for every + line, ignoring the +++ header."""
    out: list[tuple[str, str]] = []
    current = ""
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            out.append((current, line))
    return out


def _known_librechat_names() -> set[str]:
    """Names already on the default branch stay; only new ones are the risk."""
    try:
        existing = subprocess.run(
            ["git", "grep", "-hoE", r"librechat-[a-z0-9][a-z0-9-]{2,}", "origin/main"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
    except Exception:
        return set()
    return {m.group(1) for m in _LIBRECHAT_DB.finditer(existing)}


def check(diff: str) -> list[str]:
    problems: list[str] = []
    known = _known_librechat_names()
    in_tenant_table = False

    for path, line in _added_lines(diff):
        body = line[1:]
        if _TENANT_HEADER.search(body):
            in_tenant_table = True
            continue
        if in_tenant_table:
            if not _TABLE_ROW.match(line):
                in_tenant_table = False
            elif _HAS_NUMBER.search(body) and not set("-: ") >= set(body.replace("|", "")):
                problems.append(f"{path}: a per-tenant row with numbers -> {body.strip()[:90]}")
        for match in _LIBRECHAT_DB.finditer(body):
            if match.group(1) not in known:
                problems.append(f"{path}: a customer database name that is new here -> {match.group(0)}")
    # The same name twice on one line is one problem, not two.
    return list(dict.fromkeys(problems))


def main() -> int:
    # An explicit flag, not a guess at stdin. The first version asked
    # ``sys.stdin.isatty()``, and a git hook runs with stdin already redirected,
    # so it read an empty string, found nothing, and let a real table through.
    if "--staged" in sys.argv:
        diff = subprocess.run(
            ["git", "diff", "--cached", "-U0"], capture_output=True, text=True, check=True
        ).stdout
    else:
        diff = sys.stdin.read()
    problems = check(diff)
    if not problems:
        return 0
    print("public-tenant-data: customer usage data may not enter this public repository.\n")
    for problem in problems:
        print(f"  {problem}")
    print(f"\n{_ADVICE}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
