"""Validate a single .trivyignore.yaml file per SPEC-CI-TRIVY-POLICY-001 REQ-5.

Called by scripts/validate-trivyignore.sh. Not intended for direct use.

Required per entry:
  - id          (string, non-empty)
  - statement   (string, ≥40 chars to prevent boilerplate)
  - expired_at  (YYYY-MM-DD, future, ≤365 days)

Sections checked: vulnerabilities, secrets, misconfigurations, licenses.

Usage:
    python3 _validate_trivyignore.py <path-to-yaml> <today YYYY-MM-DD> \
            <max-future-days> <min-statement-len>

Reads YAML content from stdin (lets the shell wrapper pass either staged
content via `git show :path` or the working-tree file). Writes
::error annotations to stderr (GitHub-Actions-aware) on violations.

Exit codes:
    0  all entries valid (or no entries)
    1  one or more violations
    2  fatal (missing yaml module, malformed YAML, bad invocation)
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

try:
    import yaml
except ImportError:
    print(
        "[validate-trivyignore] FATAL: python3 yaml module not available. "
        "Install with `pip install pyyaml` or `uv pip install pyyaml`.",
        file=sys.stderr,
    )
    sys.exit(2)


def main() -> int:
    if len(sys.argv) != 5:
        print(
            "Usage: _validate_trivyignore.py <path> <today YYYY-MM-DD> "
            "<max-future-days> <min-statement-len>",
            file=sys.stderr,
        )
        return 2

    path = sys.argv[1]
    today = date.fromisoformat(sys.argv[2])
    max_future_days = int(sys.argv[3])
    min_statement_len = int(sys.argv[4])

    raw = sys.stdin.read()
    if not raw.strip():
        print(f"[validate-trivyignore] {path}: empty file — skipping.")
        return 0

    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        print(f"::error file={path}::malformed YAML: {exc}", file=sys.stderr)
        return 1

    if not isinstance(data, dict):
        print(
            f"::error file={path}::top-level must be a YAML mapping (got "
            f"{type(data).__name__})",
            file=sys.stderr,
        )
        return 1

    violations: list[tuple[str, int | None, str]] = []
    sections = ("vulnerabilities", "secrets", "misconfigurations", "licenses")
    total_entries = 0

    for section in sections:
        entries = data.get(section)
        if entries is None:
            continue
        if not isinstance(entries, list):
            violations.append((section, None, f"`{section}` must be a list"))
            continue

        for idx, entry in enumerate(entries):
            total_entries += 1
            if not isinstance(entry, dict):
                violations.append((section, idx, "entry must be a mapping"))
                continue

            # id
            entry_id = entry.get("id")
            if not entry_id or not isinstance(entry_id, str):
                violations.append(
                    (section, idx, "missing or empty `id` (CVE / GHSA / rule identifier)"),
                )

            # statement
            statement = entry.get("statement")
            if not isinstance(statement, str) or len(statement.strip()) < min_statement_len:
                got_len = len(statement.strip()) if isinstance(statement, str) else 0
                violations.append(
                    (
                        section,
                        idx,
                        f"missing or too-short `statement` (need ≥{min_statement_len} chars; "
                        f"got {got_len}). Boilerplate like 'low priority' / 'acceptable risk' "
                        "is rejected by design — describe WHY this finding is non-exploitable "
                        "in our deployment context.",
                    ),
                )

            # expired_at
            expired_at = entry.get("expired_at")
            if expired_at is None:
                violations.append((section, idx, "missing `expired_at` (YYYY-MM-DD)"))
            else:
                # PyYAML auto-parses YYYY-MM-DD → date.
                if isinstance(expired_at, date):
                    exp_date = expired_at
                elif isinstance(expired_at, str):
                    try:
                        exp_date = date.fromisoformat(expired_at)
                    except ValueError:
                        violations.append(
                            (
                                section,
                                idx,
                                f"`expired_at` must be ISO date (YYYY-MM-DD), got: "
                                f"{expired_at!r}",
                            ),
                        )
                        continue
                else:
                    violations.append(
                        (
                            section,
                            idx,
                            f"`expired_at` must be ISO date or string, got "
                            f"{type(expired_at).__name__}",
                        ),
                    )
                    continue

                if exp_date <= today:
                    violations.append(
                        (
                            section,
                            idx,
                            f"`expired_at: {exp_date}` is in the past — entry is no "
                            "longer valid. Renew with new `expired_at` or remove.",
                        ),
                    )
                elif exp_date > today + timedelta(days=max_future_days):
                    violations.append(
                        (
                            section,
                            idx,
                            f"`expired_at: {exp_date}` is more than {max_future_days} days "
                            "in the future — keep within 12 months for the SPEC's "
                            "re-evaluation cadence.",
                        ),
                    )

    if violations:
        for section, idx, msg in violations:
            loc = f"{section}[{idx}]" if idx is not None else section
            print(f"::error file={path}::{loc}: {msg}", file=sys.stderr)
        return 1

    print(f"[validate-trivyignore] {path}: {total_entries} entries OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
