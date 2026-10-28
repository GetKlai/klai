#!/usr/bin/env python3
"""Expand-then-contract check for Alembic migrations (SPEC-INFRA-AUTO-ROLLBACK-001 R3).

An automatic rollback puts the previous image back on the newer schema; it never
runs ``alembic downgrade``. That is only safe when a migration that removes,
renames, retypes or tightens something ships on its own, after the release that
stopped depending on it. This check fails a diff that adds such a migration
together with a change to the service's application code. The same migration in
a PR without application code (the contract PR) passes.

Usage, from anywhere inside the repository:

    check-expand-contract.py <base-ref> <migrations-dir> <app-dir> [<app-dir> ...]

Paths are relative to the repository root. The head is HEAD. When <base-ref> is
empty or not present in a shallow checkout, origin/main is fetched and used.

Known ceiling: the check sees that a contract PR carries no application code,
not that the release which stopped using the column is already live; that half
stays a review rule.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
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


def _strings(node: ast.AST, names: dict[str, str]) -> list[str]:
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            out.append(sub.value)
        elif isinstance(sub, ast.Name) and sub.id in names:
            out.append(names[sub.id])
    return out


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
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names[target.id] = node.value.value
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
                    match = DESTRUCTIVE_SQL.search(text)
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


def _resolve_base(base: str) -> str:
    if (
        base
        and subprocess.run(
            ["git", "cat-file", "-e", f"{base}^{{commit}}"],
            capture_output=True,
            check=False,
        ).returncode
        == 0
    ):
        return base
    _git("fetch", "--quiet", "--depth=1", "origin", "main")
    return "FETCH_HEAD"


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
        if status != "A" or not _under(path, migrations_dir):
            continue
        source = (root / path).read_text()
        if path.endswith(".py"):
            ops = destructive_ops_py(source)
        elif path.endswith(".sql") and "_rollback_" not in Path(path).name:
            # deploy-portal-api.sh skips *_rollback_*.sql; every other one runs.
            ops = destructive_ops_sql(source)
        else:
            continue
        if ops:
            destructive[path] = ops
    app_changes = [
        path for _, path in changes if any(_under(path, d) for d in app_dirs)
    ]

    if not destructive:
        print(f"expand-contract: no destructive migration added under {migrations_dir}")
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
