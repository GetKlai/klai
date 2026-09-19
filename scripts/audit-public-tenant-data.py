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
3. A customer name and a count in the same sentence of added prose, a table row,
   or a code comment: "<customer> had 272 answers" leaks as much as the table did, and
   the first version let it through.

Only added rows are reported, but the surrounding context is read, because a
row appended to a table whose header is untouched would otherwise be invisible.
That is why the caller passes a diff WITH context.

It never prints the offending text. A finding names the file, the line and the
kind — echoing the row would copy the customer data into a CI log, and workflow
logs of a public repository are themselves public.

The customer names for rule 3 never live in this repository, because the list
would itself be the disclosure. They come from ``KLAI_TENANT_NAMES`` (one per
line; a GitHub secret in CI) or from ``~/.config/klai/tenant-names.txt``
locally. Without either, rules 1 and 2 still run and the script says loudly that
rule 3 is off. A tenant added after the list was written is not covered until
the list is refreshed from ``portal_orgs``.

``--text <label>`` checks plain text instead of a diff: commit messages before a
push, and pull-request or issue bodies before they are sent.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

_TENANT_WORD = re.compile(
    r"\b(tenant|tenants|klant|klanten|organisatie|organisaties|customer|customers|org|orgs)\b",
    re.IGNORECASE,
)
# Outer pipes are optional: "Tenant | Usage" is a valid Markdown table.
_TABLE_LINE = re.compile(r"^\s*\|?[^|]*\|.*$")
_SEPARATOR_CELL = re.compile(r"^:?-{2,}:?$")
_LIBRECHAT_DB = re.compile(r"\blibrechat-([a-z0-9][a-z0-9-]{2,})\b")
_DIGIT = re.compile(r"\d")
# A count, not a number that is part of a name: "SPEC-VOYS-001", "v2", "#1527",
# "privacy1" and dates ("18 sep", "2026") are not usage data.
_COUNT = re.compile(
    r"(?<![\w.#/-])\d+(?:[.,]\d+)*(?![\w-])(?!\s*(?:jan|feb|mar|maa|apr|mei|may|jun|jul|aug|sep|okt|oct|nov|dec)\w*\b)",
    re.IGNORECASE,
)
_YEAR = re.compile(r"^20\d\d$")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# Code files are checked in their comments only: a fixture with an org slug and
# an id is not a leak, a comment reporting that org's volume is.
_CODE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".sh", ".sql", ".go", ".rs", ".css"}
_COMMENT = re.compile(r"^\s*(#|//|/\*|\*|--)")
_NAMES_FILE = Path.home() / ".config" / "klai" / "tenant-names.txt"

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


def _has_count(text: str) -> bool:
    return any(not _YEAR.match(m.group(0)) for m in _COUNT.finditer(text))


def _names_pattern(names: list[str]) -> re.Pattern[str] | None:
    if not names:
        return None
    alternatives = "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?<![\w-])(?:{alternatives})(?![\w-])", re.IGNORECASE)


def check(diff: str, names: list[str] | None = None) -> list[str]:
    """Findings as "<file>:<line>: <kind>". Never includes the matched text."""
    problems: list[str] = []
    known = _known_librechat_names()
    customer = _names_pattern(names or [])
    path = ""
    line_no = 0
    in_tenant_table = False
    exempt = False
    prose: list[tuple[int, str]] = []

    def flush() -> None:
        if customer and prose:
            text = " ".join(body for _, body in prose)
            for sentence in _SENTENCE_END.split(text):
                if customer.search(sentence) and _has_count(customer.sub("", sentence)):
                    problems.append(f"{path}:{prose[0][0]}: a customer name next to a number")
                    break
        prose.clear()

    for raw in diff.splitlines():
        if raw.startswith("+++ b/"):
            flush()
            path = raw[6:]
            exempt = path == _EXEMPT
            # Table state may not survive a file boundary: a header at the end of
            # one file would otherwise condemn an innocent numeric row in the next.
            in_tenant_table = False
            line_no = 0
            continue
        if raw.startswith("@@"):
            flush()
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

        is_code = Path(path).suffix in _CODE_SUFFIXES
        if added and body.strip() and (not is_code or _COMMENT.match(body)):
            if _TABLE_LINE.match(body):
                flush()
                prose.append((line_no, " ".join(_cells(body)) + "."))
                flush()
            else:
                prose.append((line_no, body.strip()))
        else:
            flush()

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

    flush()
    return list(dict.fromkeys(problems))


def as_diff(text: str, label: str) -> str:
    """Plain text as a diff that adds every line, so one check covers both."""
    lines = text.splitlines()
    return f"+++ b/{label}\n@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{line}\n" for line in lines)


def load_names() -> list[str]:
    raw = os.environ.get("KLAI_TENANT_NAMES")
    if raw is None and _NAMES_FILE.is_file():
        raw = _NAMES_FILE.read_text(encoding="utf-8")
    if raw is None:
        return []
    return [line.strip() for line in raw.splitlines() if line.strip() and not line.startswith("#")]


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
    elif "--text" in sys.argv:
        label = sys.argv[sys.argv.index("--text") + 1]
        diff = as_diff(sys.stdin.read(), label)
    else:
        diff = sys.stdin.read()
    names = load_names()
    if not names:
        print(
            "public-tenant-data: no customer-name list (KLAI_TENANT_NAMES or "
            f"{_NAMES_FILE}); names next to numbers in prose are NOT checked.",
            file=sys.stderr,
        )
    problems = check(diff, names)
    if not problems:
        return 0
    print("public-tenant-data: customer usage data may not enter this public repository.\n")
    for problem in problems:
        print(f"  {problem}")
    print(f"\n{_ADVICE}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
