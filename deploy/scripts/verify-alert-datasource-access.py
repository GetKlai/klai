#!/usr/bin/env python3
"""Prove that every Postgres-backed Grafana alert can actually read its data.

Why this exists
---------------

On 2026-08-14 a freshly provisioned alert (spec-kb-015-feedback-correlation-low)
queried portal_feedback_events directly. grafana_reader has no SELECT grant on
that table, so every evaluation threw "permission denied" -- and because the
rule copied execErrState: OK from a neighbour, that failure was
indistinguishable from a healthy system. An alert built to catch silence was
itself silent. Nobody would have noticed until the thing it watches broke.

There are TWO distinct ways a rule can be blind, and only checking the first is
how the second one hid for months:

  1. PERMISSION -- no SELECT grant. Loud at the datasource, invisible in
     Grafana when execErrState is OK. Caught here by EXPLAIN.

  2. RLS BLACKHOLE -- the grant exists, the query succeeds, and it returns zero
     rows forever because the table's SELECT policy is scoped to another role
     (or to a tenant GUC that Grafana never sets). Nothing errors. From Grafana
     this is identical to "no data", which is identical to "all healthy".
     Caught here by comparing what the superuser sees against what
     grafana_reader sees on the same relation.

Class 2 is why this script compares row counts instead of just running EXPLAIN.

Usage
-----

    verify-alert-datasource-access.py --plan [DIR]
        Parse only. Prints "uid<TAB>relation" per referenced relation. No DB
        access, so it runs anywhere -- this is the mode the self-test drives.

    verify-alert-datasource-access.py [DIR]
        Full check. Requires docker + the postgres container on this host.
        Exits non-zero on any unexplained failure.

The DB half is deliberately NOT mockable. An earlier guard in this repo shipped
with an env-var that could redirect its check away from the real target, which
made it pass against a nonexistent image. Only the parser -- the fragile part --
is exercised offline.
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import yaml

DEFAULT_DIR = Path("deploy/grafana/provisioning/alerting")

# The role Grafana authenticates as. Must match the `user:` field in
# deploy/grafana/provisioning/datasources/portal-postgres.yaml.
READER_ROLE = "grafana_reader"

POSTGRES_CONTAINER = "klai-core-postgres-1"

# Rules known to be blind, keyed by uid. An entry does NOT make a rule work --
# it records that we looked, understood, and chose not to fix it in that change.
#
# Currently empty. The one entry this started with
# (spec-priv-001-tenant-stuck-full) was retired on 2026-08-17 by giving the rule
# a superuser-owned view over the telemetry transitions it needs. Future entries
# are permitted only while they satisfy the validation below.
#
# The shape mirrors .trivyignore.yaml deliberately, because that file solved the
# harder half of this problem: an exception with only a reason lives forever, so
# every entry also needs a date on which someone must look again. Enforced by
# _validate_allowlist below -- a reason under 40 characters, a missing or
# malformed date, a date already past, or a date more than a year out all fail
# CI. So does a uid that no longer exists, which is how the list cannot outlive
# the rules it excuses.
#
#   "some-rule-uid": {
#       "statement": "Why it is blind, what it costs, and who owns the fix.",
#       "expired_at": "2027-01-31",
#   },
KNOWN_BLIND: dict[str, dict[str, str]] = {}

_MIN_STATEMENT_CHARS = 40
_MAX_EXEMPTION_DAYS = 365


def _validate_allowlist(known_uids: set[str]) -> list[str]:
    """Return the reasons KNOWN_BLIND is itself invalid (empty = fine).

    An allowlist that is never checked becomes the place findings go to die. The
    two failure modes are a stale entry (the rule is gone, so the excuse is
    fiction) and a permanent entry (nobody ever looks again).
    """
    problems: list[str] = []
    today = date.today()

    for uid in sorted(set(KNOWN_BLIND) - known_uids):
        problems.append(
            f"{uid}: listed in KNOWN_BLIND but no rule with that uid exists -- "
            "the exemption no longer excuses anything. Remove it."
        )

    for uid, entry in sorted(KNOWN_BLIND.items()):
        statement = (entry.get("statement") or "").strip()
        if len(statement) < _MIN_STATEMENT_CHARS:
            problems.append(
                f"{uid}: statement is {len(statement)} chars, needs at least "
                f"{_MIN_STATEMENT_CHARS}. Say why it is blind, what it costs, "
                "and who owns the fix -- 'known issue' is not a reason."
            )

        raw_expiry = (entry.get("expired_at") or "").strip()
        if not raw_expiry:
            problems.append(f"{uid}: no expired_at. Every exemption needs a date to look again.")
            continue
        try:
            expires = date.fromisoformat(raw_expiry)
        except ValueError:
            problems.append(f"{uid}: expired_at {raw_expiry!r} is not YYYY-MM-DD.")
            continue
        if expires < today:
            problems.append(
                f"{uid}: exemption expired on {expires}. Fix the rule, or renew "
                "the date deliberately with a statement that still holds."
            )
        elif (expires - today).days > _MAX_EXEMPTION_DAYS:
            problems.append(
                f"{uid}: expired_at {expires} is more than "
                f"{_MAX_EXEMPTION_DAYS} days out, which is indistinguishable "
                "from permanent."
            )

    return problems


def _postgres_rules(directory: Path) -> list[tuple[str, str, str]]:
    """Return (source_file, uid, rawSql) for every Postgres-datasource query."""
    found: list[tuple[str, str, str]] = []
    for path in sorted(directory.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text()) or {}
        for group in doc.get("groups") or []:
            for rule in group.get("rules") or []:
                uid = rule.get("uid", "<no-uid>")
                for query in rule.get("data") or []:
                    model = query.get("model") or {}
                    raw_sql = model.get("rawSql")
                    if not raw_sql:
                        continue
                    ds_type = (model.get("datasource") or {}).get("type", "")
                    if "postgres" not in ds_type:
                        continue
                    found.append((path.name, uid, raw_sql))
    return found


_CTE_RE = re.compile(r"(?:\bWITH\b|,)\s*([a-zA-Z_][\w]*)\s+AS\s*\(", re.IGNORECASE)
_REL_RE = re.compile(r"\b(?:FROM|JOIN)\s+([a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*)?)", re.IGNORECASE)
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_comments(raw_sql: str) -> str:
    """Remove SQL comments before any pattern matching.

    Prose is full of "from" and "join". A rule whose rawSql carries a comment
    explaining WHY it reads a particular view -- exactly the kind of comment
    worth writing -- would otherwise have words from that sentence extracted as
    relation names. Caught by the phrase "from the day it was provisioned",
    which produced `relation "the" does not exist` and reported a healthy rule
    as blind.
    """
    return _BLOCK_COMMENT_RE.sub(" ", _LINE_COMMENT_RE.sub(" ", raw_sql))


def referenced_relations(raw_sql: str) -> list[str]:
    """Relations a query really reads, with comments and CTE names removed.

    CTE names appear after FROM exactly like tables do, but they are not
    relations and have no grants -- counting rows on one would error and look
    like a finding. Subqueries need no special handling: their FROM clauses are
    matched on their own.
    """
    sql = _strip_comments(raw_sql)
    ctes = {m.lower() for m in _CTE_RE.findall(sql)}
    seen: list[str] = []
    for rel in _REL_RE.findall(sql):
        if rel.lower() in ctes or rel.lower() in seen:
            continue
        seen.append(rel.lower())
    return seen


def _psql(sql: str) -> tuple[int, str, str]:
    """Run SQL in the postgres container as the superuser.

    Returns (rc, stdout, stderr). Keep the two streams apart: psql writes
    errors to stderr and command tags like "SET" to stdout, so merging them
    both truncates error messages to a stray caret AND makes a scalar result
    unparseable the moment the statement is preceded by SET ROLE.
    """
    proc = subprocess.run(  # noqa: S603
        [
            "docker",
            "exec",
            POSTGRES_CONTAINER,
            "sh",
            "-c",
            'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -v ON_ERROR_STOP=1 -c '
            + _shell_quote(sql),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _first_error(stderr: str) -> str:
    """The actionable line of a psql error, not the caret that follows it."""
    for line in stderr.splitlines():
        if line.startswith(("ERROR:", "FATAL:")):
            return line
    return stderr.splitlines()[0] if stderr.splitlines() else "unknown error"


def _scalar(stdout: str) -> str:
    """Last non-empty stdout line -- skips the "SET" tag from SET ROLE."""
    lines = [line for line in stdout.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _check_rule(uid: str, raw_sql: str) -> list[str]:
    """Return a list of failure descriptions for one rule (empty = healthy)."""
    failures: list[str] = []

    # Class 1: can the role execute the query at all? EXPLAIN plans it without
    # running it, which is enough to surface a missing grant.
    rc, _out, err = _psql(f"SET ROLE {READER_ROLE}; EXPLAIN {raw_sql.rstrip().rstrip(';')}")
    if rc != 0:
        failures.append(f"cannot execute as {READER_ROLE}: {_first_error(err)}")
        return failures  # No point counting rows on a query that cannot run.

    # Class 2: the query runs but a relation is silently empty for this role.
    for rel in referenced_relations(raw_sql):
        rc_super, super_out, super_err = _psql(f"SELECT count(*) FROM {rel}")
        if rc_super != 0:
            failures.append(f"relation {rel} unreadable even as superuser: {_first_error(super_err)}")
            continue
        rc_reader, reader_out, reader_err = _psql(
            f"SET ROLE {READER_ROLE}; SELECT count(*) FROM {rel}"
        )
        if rc_reader != 0:
            failures.append(f"relation {rel} unreadable as {READER_ROLE}: {_first_error(reader_err)}")
            continue
        try:
            super_n, reader_n = int(_scalar(super_out)), int(_scalar(reader_out))
        except ValueError:
            failures.append(
                f"relation {rel}: unparseable counts ({_scalar(super_out)!r} / {_scalar(reader_out)!r})"
            )
            continue
        if super_n > 0 and reader_n == 0:
            failures.append(
                f"relation {rel} is an RLS blackhole: superuser sees {super_n} rows, "
                f"{READER_ROLE} sees 0. The query will never return data and the "
                f"alert can never fire."
            )
    return failures


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if a != "--plan"]
    plan_only = "--plan" in argv[1:]
    directory = Path(args[0]) if args else DEFAULT_DIR

    if not directory.is_dir():
        print(f"ERROR: {directory} is not a directory", file=sys.stderr)
        return 2

    rules = _postgres_rules(directory)
    if not rules:
        print(f"ERROR: no Postgres-datasource alert rules found under {directory}.")
        print("Either the path is wrong or the rule shape changed -- refusing to")
        print("report success on an empty check.")
        return 2

    problems = _validate_allowlist({uid for _f, uid, _s in rules})
    if problems:
        print("ERROR: KNOWN_BLIND is not valid:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    if plan_only:
        for _file, uid, raw_sql in rules:
            for rel in referenced_relations(raw_sql):
                print(f"{uid}\t{rel}")
        return 0

    exit_code = 0
    for source, uid, raw_sql in rules:
        failures = _check_rule(uid, raw_sql)
        if not failures:
            print(f"OK       {uid} ({source})")
            continue
        entry = KNOWN_BLIND.get(uid)
        excuse = (entry or {}).get("statement", "")
        label = "KNOWN" if entry else "BLIND"
        print(f"{label}    {uid} ({source})")
        for failure in failures:
            print(f"         - {failure}")
        if entry:
            print(f"         allowlisted until {entry.get('expired_at', '?')}: {excuse[:110]}")
        else:
            exit_code = 1

    if exit_code:
        print()
        print("At least one alert cannot read its own data. It will never fire, and")
        print("with execErrState: OK it will look healthy while doing nothing.")
        print("Fix the grant, or expose a superuser-owned view for the columns the")
        print("rule needs -- see deploy/grafana/sql/grafana-reader-setup.sql.")
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
