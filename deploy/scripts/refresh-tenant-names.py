#!/usr/bin/env python3
"""Regenerate the private customer-name list from the portal database.

Runs on core-01 as user klai. The list feeds rule 4 of
scripts/audit-public-tenant-data.py, which keeps customer names out of this
public repository. The names come from three tables (orgs, users, connectors)
and are written to a private file; this script never prints a name, only counts.

Each query is sent to the command given by --psql on stdin, so a test can
substitute any program that prints tab-separated rows. The default runs psql in
the Postgres container.

The new list replaces the old one only when it is not empty and not more than
30% shorter: a database that is briefly unreachable or half-restored would
otherwise blank the guard without anyone noticing.
"""

from __future__ import annotations

import argparse
import datetime
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_PSQL = "docker exec -i klai-core-postgres-1 psql -U klai -d klai -At -F"
DEFAULT_OUTPUT = Path("/opt/klai/private/tenant-names.txt")
# Platform and vendor domains that cannot go in the public allowlist because the
# guard would then see them as customer names; one per line, optional.
DEFAULT_EXTRA_ALLOWED = Path("/opt/klai/private/platform-domains.txt")
DEFAULT_ALLOWED = (
    Path(__file__).resolve().parents[2] / "scripts" / "allowed-test-domains.txt"
    if len(Path(__file__).resolve().parents) > 2
    else Path("/nonexistent/allowed-test-domains.txt")
)
MAX_SHRINK = 0.30
MIN_LABEL = 4
MIN_WORD = 5

STOPLIST = frozenset(
    [
        "admin",
        "b.v.",
        "bv",
        "cloud",
        "com",
        "company",
        "demo",
        "docs",
        "energized",
        "europa",
        "example",
        "getklai",
        "group",
        "growth",
        "help",
        "holding",
        "info",
        "intelligence",
        "internet",
        "klai",
        "local",
        "mail",
        "nieuw",
        "nl",
        "onderwijs",
        "online",
        "openbaar",
        "partners",
        "portal",
        "privacy",
        "prompters",
        "python",
        "report",
        "security",
        "services",
        "shop",
        "solutions",
        "specs",
        "stichting",
        "studio",
        "support",
        "team",
        "telecom",
        "test",
        "united",
        "villa",
        "voys",
        "wiki",
        "www",
    ]
)
_SECOND_LEVEL = frozenset({"co", "com", "org", "net", "eu"})

SOURCES = {
    "orgs": "select name, slug, primary_domain from public.portal_orgs where deleted_at is null",
    "users": "select email from public.portal_users",
    "connectors": "select config->>'base_url', config->>'discovery_seed_url' from public.portal_connectors",
}


def registrable(host: str) -> str:
    labels = host.split(".")
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def host_of(value: str) -> str | None:
    value = value.strip().lower()
    if not value:
        return None
    host = urlsplit(value if "//" in value else "//" + value).hostname
    return host if host and "." in host else None


def _usable(label: str) -> bool:
    return len(label) >= MIN_LABEL and label not in STOPLIST


def host_entries(host: str, allowed: list[str]) -> set[str]:
    """A registrable domain and its first label; nothing for a vendor or platform host."""
    if any(host == d or host.endswith("." + d) for d in allowed):
        return set()
    domain = registrable(host)
    first = domain.split(".")[0]
    if first in STOPLIST:
        return set()
    return {domain} | ({first} if _usable(first) else set())


def org_entries(name: str, slug: str, domain_label: str) -> set[str]:
    """A name is kept whole and only when it is specific: a phrase of two or more
    words, or one word that is also the org's own domain label or slug (an
    identifier, not a dictionary word). Names are never split into words."""
    name = " ".join(name.lower().split())
    if len(name.split()) >= 2:
        return {name}
    if (
        len(name) >= MIN_WORD
        and _usable(name)
        and name in (slug.strip().lower(), domain_label)
    ):
        return {name}
    return set()


def email_entries(email: str, allowed: list[str]) -> set[str]:
    return (
        host_entries(email.rsplit("@", 1)[1].strip().lower(), allowed)
        if "@" in email
        else set()
    )


def derive(rows: dict[str, list[list[str]]], allowed: list[str]) -> dict[str, set[str]]:
    """Entries per source. Rows are lists of column values, empty string for NULL."""
    out: dict[str, set[str]] = {"orgs": set(), "users": set(), "connectors": set()}
    for name, slug, domain in (_pad(r, 3) for r in rows.get("orgs", [])):
        host = host_of(domain)
        label = registrable(host).split(".")[0] if host else ""
        out["orgs"] |= org_entries(name, slug, label)
        if host:
            out["orgs"] |= host_entries(host, allowed)
    for (email,) in (_pad(r, 1) for r in rows.get("users", [])):
        out["users"] |= email_entries(email, allowed)
    for row in rows.get("connectors", []):
        for url in _pad(row, 2):
            host = host_of(url)
            if host:
                out["connectors"] |= host_entries(host, allowed)
    return out


def _pad(row: list[str], n: int) -> list[str]:
    return (row + [""] * n)[:n]


ALLOWED_URL = "https://raw.githubusercontent.com/GetKlai/klai/main/scripts/allowed-test-domains.txt"


def load_allowed(path: Path) -> list[str]:
    """The platform allowlist: the repo file when present, otherwise the same
    file from the public default branch. core-01 only receives deploy/, so the
    nightly run takes the second route; the file holds vendor domains only."""
    if path.is_file():
        text = path.read_text("utf-8")
    else:
        import urllib.request

        with urllib.request.urlopen(ALLOWED_URL, timeout=20) as response:
            text = response.read().decode("utf-8")
    lines = (line.split("#", 1)[0].strip().lower() for line in text.splitlines())
    return [line for line in lines if line]


def fetch(psql: list[str], sql: str) -> list[list[str]]:
    result = subprocess.run(
        psql, input=sql, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        # psql's stderr may quote row values; report the exit code only.
        raise SystemExit(
            f"refresh-tenant-names: the database command failed (exit {result.returncode})"
        )
    return [line.split("\t") for line in result.stdout.splitlines() if line.strip()]


def read_list(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return {
        line.strip()
        for line in path.read_text("utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


def write_atomic(path: Path, entries: set[str], today: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    body = f"# generated by deploy/scripts/refresh-tenant-names.py {today}\n" + "".join(
        f"{e}\n" for e in sorted(entries)
    )
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tenant-names.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--psql",
        default=None,
        help="command that runs SQL read from stdin (default: psql in the Postgres container)",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--allowed", type=Path, default=DEFAULT_ALLOWED)
    parser.add_argument("--extra-allowed", type=Path, default=DEFAULT_EXTRA_ALLOWED)
    parser.add_argument(
        "--dry-run", action="store_true", help="print counts only, write nothing"
    )
    args = parser.parse_args(argv)

    psql = shlex.split(args.psql) if args.psql else [*shlex.split(DEFAULT_PSQL), "\t"]
    try:
        allowed = load_allowed(args.allowed)
        if args.extra_allowed.is_file():
            allowed += load_allowed(args.extra_allowed)
    except OSError as exc:
        print(
            f"refresh-tenant-names: allowlist unavailable ({exc}); pass --allowed",
            file=sys.stderr,
        )
        return 2

    per_source = derive(
        {name: fetch(psql, sql) for name, sql in SOURCES.items()}, allowed
    )
    entries = set().union(*per_source.values())
    old = read_list(args.output)
    for name, found in per_source.items():
        print(f"{name}: {len(found)}")
    print(f"total: {len(entries)}")
    print(f"added: {len(entries - old)}, removed: {len(old - entries)}")

    if not entries:
        print(
            "refresh-tenant-names: refusing to write, the new list is empty",
            file=sys.stderr,
        )
        return 1
    if old and len(entries) < len(old) * (1 - MAX_SHRINK):
        print(
            f"refresh-tenant-names: refusing to write, the new list ({len(entries)}) is more than "
            f"{MAX_SHRINK:.0%} shorter than the current one ({len(old)})",
            file=sys.stderr,
        )
        return 1
    if args.dry_run:
        return 0
    write_atomic(args.output, entries, datetime.date.today().isoformat())
    return 0


if __name__ == "__main__":
    sys.exit(main())
