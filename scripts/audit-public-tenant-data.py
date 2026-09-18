#!/usr/bin/env python3
"""Block per-customer usage data from entering this public repository.

On 2026-09-18 a table naming five tenants with their monthly internal-chat
volume and how many of those answers carried a citation was committed here and
pushed. It never reached main and the branch was deleted, but the commit stays
reachable by SHA through the closed pull request, and GitHub keeps the edit
history of the PR body. That is the failure this guard exists for: the damage is
done at ``git push``, so a check that runs afterwards can only report it.

What it looks for:

1. A Markdown table whose header names tenants, organisations or customers, with
   a digit anywhere in a data cell of an ADDED row. That is the artefact that
   leaked: a name and a count on the same row.
2. A LibreChat database name (``librechat-<slug>``) that does not already appear
   on the default branch. Those names are per-customer by construction.

Only added rows are reported, but the surrounding context is read, because a
row appended to a table whose header is untouched would otherwise be invisible.
That is why the caller passes a diff WITH context.

It never prints the offending text. A finding names the file, the line and the
kind — echoing the row would copy the customer data into a CI log, and workflow
logs of a public repository are themselves public.

It deliberately carries no list of customer names: such a list would be the
disclosure, and it would go stale. The shape is enough.
"""

from __future__ import annotations

import re
import subprocess
import sys

_TENANT_WORD = re.compile(
    r"\b(tenant|tenants|klant|klanten|organisatie|organisaties|customer|customers|org|orgs)\b",
    re.IGNORECASE,
)
# Outer pipes are optional: "Tenant | Usage" is a valid Markdown table.
_TABLE_LINE = re.compile(r"^\s*\|?[^|]*\|.*$")
_SEPARATOR_CELL = re.compile(r"^:?-{2,}:?$")
_LIBRECHAT_DB = re.compile(r"\blibrechat-([a-z0-9][a-z0-9-]{2,})\b")
_DIGIT = re.compile(r"\d")

# This guard's own tests exist to contain the shapes it detects, so scanning
# them blocks every change to them. Exactly one path, spelled out: anything
# wider would let a real leak hide in a file with "test" in its name.
_EXEMPT = "scripts/tests/test_audit_public_tenant_data.py"

_ADVICE = (
    "Per-customer figures belong in the private klai-infra documentation.\n"
    "  Keep the shape of the distribution here if the decision needs it, without the\n"
    "  names or the counts: \"one tenant accounted for almost all cited answers; three had none\"."
)


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_separator(line: str) -> bool:
    cells = _cells(line)
    return bool(cells) and all(_SEPARATOR_CELL.match(cell) for cell in cells if cell)


def check(diff: str) -> list[str]:
    """Findings as "<file>:<line>: <kind>". Never includes the matched text."""
    problems: list[str] = []
    known = _known_librechat_names()
    path = ""
    line_no = 0
    in_tenant_table = False
    exempt = False

    for raw in diff.splitlines():
        if raw.startswith("+++ b/"):
            path = raw[6:]
            exempt = path == _EXEMPT
            # Table state may not survive a file boundary: a header at the end of
            # one file would otherwise condemn an innocent numeric row in the next.
            in_tenant_table = False
            line_no = 0
            continue
        if raw.startswith("@@"):
            match = re.search(r"\+(\d+)", raw)
            line_no = int(match.group(1)) - 1 if match else 0
            in_tenant_table = False
            continue
        if raw.startswith("---") or raw.startswith("diff "):
            continue

        added = raw.startswith("+")
        context = raw.startswith(" ")
        if not (added or context):
            continue  # a removed line is not in the result
        body = raw[1:]
        line_no += 1
        if exempt:
            continue

        # Before the table logic: a database name on a header line is still one.
        if added:
            for match in _LIBRECHAT_DB.finditer(body):
                if match.group(1) not in known:
                    problems.append(f"{path}:{line_no}: a customer database name that is new here")

        if not _TABLE_LINE.match(body):
            in_tenant_table = False
            continue
        if _TENANT_WORD.search(body):
            # Context counts here on purpose: a row appended under an untouched
            # header is exactly the case a diff without context cannot see.
            in_tenant_table = True
            continue
        if _is_separator(body):
            continue
        if in_tenant_table and added and any(_DIGIT.search(cell) for cell in _cells(body)):
            problems.append(f"{path}:{line_no}: a per-tenant row carrying numbers")

    return list(dict.fromkeys(problems))


def _known_librechat_names() -> set[str]:
    """Names already on the default branch stay; only new ones are the risk."""
    for ref in ("origin/main", "main"):
        result = subprocess.run(
            ["git", "grep", "-hoE", r"librechat-[a-z0-9][a-z0-9-]{2,}", ref],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return {m.group(1) for m in _LIBRECHAT_DB.finditer(result.stdout)}
    return set()


def main() -> int:
    # An explicit flag, not a guess at stdin: a git hook runs with stdin already
    # redirected, and the first version read an empty string there and let a real
    # table through. Context lines are requested because a row appended under an
    # untouched header is invisible without them.
    if "--staged" in sys.argv:
        diff = subprocess.run(
            ["git", "diff", "--cached", "-U3"], capture_output=True, text=True, check=True
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
