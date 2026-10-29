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
3. A customer name and a count in the same sentence. Folded into rule 4 on
   2026-10-29: a name with a count is a finding, and so is a name without one.
4. A customer name in ANY added line (prose, comment, code, fixture, commit
   message, pull-request text), number or not. Rule 3 only blocked a name next
   to a number, and a customer's wiki hostname then sat in 46 files of tests,
   fixtures and comments because no number stood beside it. The owner's rule is
   absolute: no customer or partner name, ever.
5. A real hostname in an added line of a test or fixture path (``tests/``,
   ``__tests__/``, ``fixtures/``, ``stories/``, ``seeds/``, ``e2e/``, ``*.test.*``,
   ``*.spec.*``, ``*_test.*``, ``*.stories.*``). Needs no name list. Allowed:
   the reserved example domains (RFC 2606/6761), ``localhost``, IP literals, and
   what ``scripts/allowed-test-domains.txt`` lists. A token only counts as a
   hostname when its last label is a known top-level domain (a fixed set below,
   so ``user.id``, ``foo.py`` and module paths are not hostnames). The ceiling:
   a customer host under a top-level domain missing from that set passes rule 5
   (rule 4 still catches it once its name is on the list).

Only added rows are reported, but the surrounding context is read, because a
row appended to a table whose header is untouched would otherwise be invisible.
That is why the caller passes a diff WITH context.

It never prints the offending text. A finding names the file, the line and the
kind — echoing the row would copy the customer data into a CI log, and workflow
logs of a public repository are themselves public.

The customer names for rules 3 and 4 never live in this repository, because the list
would itself be the disclosure. They come from ``KLAI_TENANT_NAMES`` (one per
line; a GitHub secret in CI) or from ``~/.config/klai/tenant-names.txt``
locally. A name may be a hostname or domain; lines starting with ``#`` and blank
lines are ignored. The list is generated nightly on core-01 by
``deploy/scripts/refresh-tenant-names.py``, and a local file older than 7 days
draws a warning. Without a list, rules 1, 2 and 5 still run and the script says
loudly that rules 3 and 4 are off.

Only added lines are judged, so what is already in the tree does not fail a
push. ``--tree`` scans the whole working tree with rules 4 and 5 and prints
file:line:kind and a total, for the cleanup and for a scheduled audit.

``--text <label>`` checks plain text instead of a diff: commit messages before a
push, and pull-request or issue bodies before they are sent.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
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
_NAMES_FILE = Path.home() / ".config" / "klai" / "tenant-names.txt"
# The list is regenerated nightly on core-01 and fetched at session start, so a
# week without an update means the fetch is broken, not that nothing changed.
_STALE_AFTER_DAYS = 7
_ALLOWED_DOMAINS_FILE = Path(__file__).resolve().parent / "allowed-test-domains.txt"

_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|fixtures?|stories|seeds?|e2e)(/|$)|(\.|_|-)(test|spec|stories)\.")
_DOTTED = re.compile(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+")
_ESCAPE = re.compile(r"\\[tnr]")
# Reserved by RFC 2606 / 6761: never resolve to a real party. Their reserved TLDs
# (.test, .example, .invalid, .localhost) and "localhost" are not in _TLDS, so a
# host under them is never a hostname here.
_RESERVED_DOMAINS = ("example.com", "example.net", "example.org")
# Top-level domains a hostname token may end in. A fixed set, not "any two
# letters": that would make file names (``foo.py``, ``x.md``), property access
# (``row.id``, ``list.at``) and module paths hostnames. Left out on purpose:
# the codes that are also common file extensions or identifiers, and ``app``,
# ``email``, ``info``, ``page``, which are mostly Python and JS attributes.
_TLDS = {
    # generic
    "com", "net", "org", "io", "dev", "cloud", "ai", "co", "biz", "eu", "xyz", "online", "tech", "wiki", "edu", "gov",
    # country codes
    "nl", "be", "de", "fr", "uk", "es", "it", "ch", "dk", "se", "fi", "pl", "cz", "pt", "ie", "lu", "us", "ca", "au",
    "nz", "jp", "cn", "br", "mx", "ru", "ua", "tr", "gr", "hu", "ro", "bg", "sk", "si", "hr",
}  # fmt: skip


# This guard's own tests exist to contain the shapes it detects, so scanning
# them blocks every change to them. Exactly one path, spelled out: anything
# wider would let a real leak hide in a file with "test" in its name.
_EXEMPT = "scripts/tests/test_audit_public_tenant_data.py"

_ADVICE = (
    "No customer or partner name and no real hostname belongs in this repository: use\n"
    "  example.com-style names (tests and fixtures: example.com, example.net, example.org, .test).\n"
    "  Per-customer figures belong in the private klai-infra documentation.\n"
    "  Keep the shape of the distribution here if the decision needs it, without the\n"
    '  names or the counts: "one tenant accounted for almost all cited answers; three had none".'
)


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_separator(line: str) -> bool:
    cells = _cells(line)
    return bool(cells) and all(_SEPARATOR_CELL.match(cell) for cell in cells if cell)


def _names_pattern(names: list[str]) -> re.Pattern[str] | None:
    if not names:
        return None
    alternatives = "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    # Only [\w-] bounds a name, never a dot: a name that is a domain must match
    # inside a URL or a dotted host ("https://wiki.<name>.cloud/x"), and so must
    # a bare slug that is one label of such a host.
    return re.compile(rf"(?<![\w-])(?:{alternatives})(?![\w-])", re.IGNORECASE)


def _allowed_domains() -> list[str]:
    if not _ALLOWED_DOMAINS_FILE.is_file():
        return []
    lines = (line.split("#", 1)[0].strip().lower() for line in _ALLOWED_DOMAINS_FILE.read_text("utf-8").splitlines())
    return [line for line in lines if line]


def _has_real_hostname(body: str, allowed: list[str]) -> bool:
    # A shell escape (``$'\tghcr.io'``) would otherwise glue its letter onto the
    # host, and a parameter default (``${IMAGE:-ghcr.io}``) its dash; no host
    # label starts with a dash.
    text = _ESCAPE.sub(" ", body)
    for match in _DOTTED.finditer(text):
        token = match.group(0).lstrip("-")
        labels = token.lower().split(".")
        # ``self.org`` or ``state.org`` is attribute access, not a host. Only
        # the ``.org`` ending doubles as a common attribute name, so only a bare
        # two-label ``.org`` token is excused, and only outside a string, URL
        # or address (no quote, slash or @ before it) and not followed by a
        # path or port. A bare ``customer.nl`` in prose is still a finding.
        before = text[match.start() - 1] if match.start() > 0 else " "
        after = text[match.end()] if match.end() < len(text) else " "
        if len(labels) == 2 and labels[-1] == "org" and before not in "\"'`/@" and after not in "/:":
            continue
        # Module paths and snake_case attributes carry underscores; versions and
        # IP literals end in a digit label; neither is a hostname.
        if "_" in token or labels[-1] not in _TLDS or not any(c.isalpha() for c in labels[-2]):
            continue
        host = ".".join(labels)
        if any(host == d or host.endswith("." + d) for d in (*_RESERVED_DOMAINS, *allowed)):
            continue
        return True
    return False


def _line_findings(
    path: str, line_no: int, body: str, customer: re.Pattern[str] | None, allowed: list[str]
) -> list[str]:
    """Rules 4 and 5 for one added line. Kinds only, never the matched text."""
    found = []
    if customer and customer.search(body):
        found.append(f"{path}:{line_no}: customer-name")
    if _TEST_PATH.search(path) and _has_real_hostname(body, allowed):
        found.append(f"{path}:{line_no}: real-hostname")
    return found


def check(diff: str, names: list[str] | None = None) -> list[str]:
    """Findings as "<file>:<line>: <kind>". Never includes the matched text."""
    problems: list[str] = []
    known = _known_librechat_names()
    customer = _names_pattern(names or [])
    allowed = _allowed_domains()
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
        if raw.startswith(("---", "diff ")):
            continue

        added = raw.startswith("+")
        context = raw.startswith(" ")
        if not (added or context):
            continue  # a removed line is not in the result
        body = raw[1:]
        line_no += 1
        if exempt:
            continue

        if added:
            problems.extend(_line_findings(path, line_no, body, customer, allowed))
            # Before the table logic: a database name on a header line is still one.
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


def scan_tree(root: Path, names: list[str]) -> list[str]:
    """Rules 4 and 5 over every tracked file as it is on disk now."""
    customer = _names_pattern(names)
    allowed = _allowed_domains()
    listing = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True).stdout
    problems: list[str] = []
    for rel in filter(None, listing.decode("utf-8").split("\0")):
        file = root / rel
        if rel == _EXEMPT or file.is_symlink() or not file.is_file():
            continue
        try:
            text = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # a binary file
        for number, line in enumerate(text.splitlines(), start=1):
            problems.extend(_line_findings(rel, number, line, customer, allowed))
    return problems


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


def _stale_list_warning() -> str | None:
    """Only the local file can go stale; the CI secret is pushed from the fresh copy."""
    if os.environ.get("KLAI_TENANT_NAMES") is not None or not _NAMES_FILE.is_file():
        return None
    age_days = (time.time() - _NAMES_FILE.stat().st_mtime) / 86400
    if age_days <= _STALE_AFTER_DAYS:
        return None
    return (
        f"public-tenant-data: the customer-name list {_NAMES_FILE} is older than {_STALE_AFTER_DAYS} days "
        f"({int(age_days)}); new customers are NOT covered: fetch the current list from core-01."
    )


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


def _print_tree(problems: list[str]) -> None:
    for problem in problems:
        print(problem)
    for kind in ("customer-name", "real-hostname"):
        print(f"{kind}: {sum(p.endswith(': ' + kind) for p in problems)}")
    print(f"total: {len(problems)}")


def main() -> int:
    # An explicit flag, not a guess at stdin: a git hook runs with stdin already
    # redirected, and the first version read an empty string there and let a real
    # table through. Context lines are requested because a row appended under an
    # untouched header is invisible without them.
    names = load_names()
    if not names:
        print(
            "public-tenant-data: no customer-name list (KLAI_TENANT_NAMES or "
            f"{_NAMES_FILE}); rules 3 and 4 are OFF: customer names are NOT checked.",
            file=sys.stderr,
        )
    stale = _stale_list_warning()
    if stale:
        print(stale, file=sys.stderr)
    if "--tree" in sys.argv:
        root = Path(
            subprocess.run(
                ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
            ).stdout.strip()
        )
        problems = scan_tree(root, names)
        _print_tree(problems)
        return 1 if problems else 0
    if "--staged" in sys.argv:
        diff = subprocess.run(["git", "diff", "--cached", "-U3"], capture_output=True, text=True, check=True).stdout
    elif "--text" in sys.argv:
        label = sys.argv[sys.argv.index("--text") + 1]
        diff = as_diff(sys.stdin.read(), label)
    else:
        diff = sys.stdin.read()
    problems = check(diff, names)
    if not problems:
        return 0
    print("public-tenant-data: customer data may not enter this public repository.\n")
    for problem in problems:
        print(f"  {problem}")
    print(f"\n{_ADVICE}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
