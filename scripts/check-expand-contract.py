#!/usr/bin/env python3
"""Expand-then-contract check for Alembic migrations (SPEC-INFRA-AUTO-ROLLBACK-001 R3).

An automatic rollback puts the previous image back on the newer schema; it never
runs ``alembic downgrade``. That is only safe when a migration that removes,
renames, retypes or tightens something ships on its own, after the release that
stopped depending on it. This check fails a diff that adds such a migration
together with a change to the service's application code. The same migration in
a PR without application code (the contract PR) passes. New migration files are
checked whole; an edited one is checked for operations its base version did not
have.

Usage, from anywhere inside the repository:

    check-expand-contract.py <base-ref> <migrations-dir> <app-dir> [<app-dir> ...]

Paths are relative to the repository root. The head is HEAD, and the diff runs
from the merge base of <base-ref> and HEAD. In a shallow checkout the base is
fetched and HEAD deepened until that merge base exists; only when that fails (or
<base-ref> is empty) does the check fall back to the current origin/main, with a
warning.

Known ceiling: the check sees that a contract PR carries no application code,
not that the release which stopped using the column is already live. A contract
migration shipped on its own still runs while the previous release serves, so
deploying one requires the release that stopped using the column to be live
already. That half is a review rule, not CI (spec R3).
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

# Alembic operations whose result the previous image may not survive. The
# receiver is not checked, so batch_op.drop_column counts as well.
#
# drop_constraint is deliberately absent: of the 19 existing migrations that
# drop a constraint, 16 drop a CHECK and recreate it wider to admit a new enum
# value, which is an expand step that ships with the code using that value.
# Tightening a column to NOT NULL is listed instead, because the previous image
# may still insert rows without it.
DESTRUCTIVE_OPS = {"drop_table", "drop_column", "rename_table"}
SQL_CALLS = {"execute", "text", "exec_driver_sql"}
DESTRUCTIVE_SQL = re.compile(
    r"\bDROP\s+(TABLE|COLUMN)\b"
    r"|\bALTER\s+TABLE\s+(IF\s+EXISTS\s+)?(ONLY\s+)?[\w.\"]+\s+RENAME\b"
    r"|\bALTER\s+COLUMN\s+[\w\"]+\s+(SET\s+DATA\s+)?TYPE\b"
    r"|\bSET\s+NOT\s+NULL\b",
    re.IGNORECASE,
)
SQL_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
# Stands in for an interpolated or unknown part of a SQL string.
PLACEHOLDER = "__expr__"


def _str_value(node: ast.AST, names: dict[str, str]) -> str | None:
    """The SQL text an expression builds, with PLACEHOLDER for the parts only known at runtime."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return names.get(node.id)
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value
            if isinstance(v, ast.Constant) and isinstance(v.value, str)
            else PLACEHOLDER
            for v in node.values
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return _str_value(node.left, names)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _str_value(node.left, names), _str_value(node.right, names)
        if left is None and right is None:
            return None
        return (PLACEHOLDER if left is None else left) + (
            PLACEHOLDER if right is None else right
        )
    return None


def _strings(node: ast.AST, names: dict[str, str]) -> list[str]:
    value = _str_value(node, names)
    if value is not None:
        return [value]
    return [
        text for child in ast.iter_child_nodes(node) for text in _strings(child, names)
    ]


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def destructive_ops_py(source: str) -> list[str]:
    """Destructive operations outside downgrade(); comments and docstrings never count."""
    tree = ast.parse(source)
    skip: set[int] = set()
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "downgrade":
            skip.update(id(sub) for sub in ast.walk(node))
    for node in ast.walk(tree):
        if id(node) in skip or not isinstance(node, ast.Assign):
            continue
        value = _str_value(node.value, names)
        if value is not None:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names[target.id] = value
    found = []
    for node in ast.walk(tree):
        if id(node) in skip or not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name in DESTRUCTIVE_OPS:
            found.append(f"line {node.lineno}: {name}")
        elif name == "alter_column":
            for kw in node.keywords:
                if kw.arg in ("type_", "new_column_name") or (
                    kw.arg == "nullable"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is False
                ):
                    found.append(f"line {node.lineno}: alter_column({kw.arg}=...)")
        elif name in SQL_CALLS:
            for arg in [*node.args, *(kw.value for kw in node.keywords)]:
                for text in _strings(arg, names):
                    match = DESTRUCTIVE_SQL.search(SQL_COMMENT.sub("", text))
                    if match:
                        found.append(
                            f"line {node.lineno}: {' '.join(match.group(0).split())}"
                        )
    return found


def destructive_ops_sql(source: str) -> list[str]:
    stripped = SQL_COMMENT.sub("", source)
    return [" ".join(m.group(0).split()) for m in DESTRUCTIVE_SQL.finditer(stripped)]


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


def _git_ok(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False)


def _resolve_base(base: str) -> str:
    """Merge base of <base-ref> and HEAD, so commits that landed on main later never count."""
    if base:
        if _git_ok("cat-file", "-e", f"{base}^{{commit}}").returncode != 0:
            _git_ok("fetch", "--quiet", "--depth=1", "origin", base)
        head = _git("rev-parse", "HEAD").strip()
        for attempt in range(2):
            if attempt:
                # A depth-1 checkout has no path from HEAD back to the base yet.
                _git_ok("fetch", "--quiet", "--deepen=100", "origin", head)
            merge_base = _git_ok("merge-base", base, "HEAD")
            if merge_base.returncode == 0:
                return merge_base.stdout.strip()
    print(
        f"::warning::no merge base for '{base}' and HEAD; diffing against the current origin/main instead",
        file=sys.stderr,
    )
    _git("fetch", "--quiet", "--depth=1", "origin", "main")
    return "FETCH_HEAD"


def _ops(path: str, source: str) -> list[str]:
    if path.endswith(".py"):
        return destructive_ops_py(source)
    if path.endswith(".sql") and "_rollback_" not in Path(path).name:
        # deploy-portal-api.sh skips *_rollback_*.sql; every other one runs.
        return destructive_ops_sql(source)
    return []


def _added_ops(before: list[str], after: list[str]) -> list[str]:
    """Operations in `after` that `before` did not have; line numbers do not count."""
    seen = Counter(op.split(": ", 1)[-1] for op in before)
    added = []
    for op in after:
        key = op.split(": ", 1)[-1]
        if seen[key]:
            seen[key] -= 1
        else:
            added.append(op)
    return added


def _under(path: str, directory: str) -> bool:
    return path.startswith(directory.rstrip("/") + "/")


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    base, migrations_dir, app_dirs = argv[0], argv[1], argv[2:]
    root = Path(_git("rev-parse", "--show-toplevel").strip())
    base = _resolve_base(base)
    diff = _git("-C", str(root), "diff", "--name-status", "--no-renames", base, "HEAD")
    changes = [line.split("\t", 1) for line in diff.splitlines() if "\t" in line]

    destructive: dict[str, list[str]] = {}
    for status, path in changes:
        if status not in ("A", "M") or not _under(path, migrations_dir):
            continue
        ops = _ops(path, (root / path).read_text())
        if status == "M":
            ops = _added_ops(
                _ops(path, _git("-C", str(root), "show", f"{base}:{path}")), ops
            )
        if ops:
            destructive[path] = ops
    app_changes = [
        path for _, path in changes if any(_under(path, d) for d in app_dirs)
    ]

    if not destructive:
        print(f"expand-contract: no new destructive operation under {migrations_dir}")
        return 0
    if not app_changes:
        print(
            f"expand-contract: destructive migration without application code (contract PR): {', '.join(destructive)}"
        )
        return 0
    for path, ops in destructive.items():
        print(
            f"::error file={path}::destructive migration ships with application code: {'; '.join(ops)}"
        )
    print("Application code changed in the same diff:")
    for path in app_changes[:20]:
        print(f"  {path}")
    print(
        "An automatic rollback runs the previous image on the new schema (SPEC-INFRA-AUTO-ROLLBACK-001 R3).\n"
        "Ship the code that stops using the old shape first; once that release is live, ship this\n"
        "migration in a PR that changes no application code."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
