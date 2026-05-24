"""Require post-deploy SQL for migration changes that cannot run as portal_api.

The deploy pipeline runs Alembic as the ``portal_api`` role, while several
production tables are owned by ``klai`` and have FORCE RLS enabled. A migration
that touches those surfaces must be paired with an idempotent
``post_deploy_<revision>*.sql`` script that runs as ``klai``.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

OWNER_SENSITIVE_TABLES = frozenset({"widgets", "widget_kb_access"})
OWNER_SENSITIVE_OPS = frozenset({"add_column", "drop_column", "alter_column", "create_index", "drop_index"})
RLS_SQL_MARKERS = (
    "CREATE POLICY ",
    "ENABLE ROW LEVEL SECURITY",
    "FORCE ROW LEVEL SECURITY",
)


def _revision(path: Path, source: str) -> str | None:
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return None

    for node in tree.body:
        if not isinstance(node, (ast.AnnAssign, ast.Assign)):
            continue
        targets: list[ast.expr]
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            targets = list(node.targets)
            value = node.value
        if not any(isinstance(target, ast.Name) and target.id == "revision" for target in targets):
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
    return None


def _static_text(node: ast.AST | None) -> str:
    """Return the literal text carried by a Python AST node.

    We intentionally inspect Python syntax rather than grep source text:
    Alembic migrations express risky work through calls like
    ``op.add_column("widgets", ...)`` and ``conn.execute(sa.text("..."))``.
    For f-strings, literal chunks are enough to catch stable SQL keywords such
    as ``CREATE POLICY`` and ``ENABLE ROW LEVEL SECURITY``.
    """

    if node is None:
        return ""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(_static_text(value) for value in node.values)
    if isinstance(node, ast.Call) and _call_name(node.func) == "sa.text" and node.args:
        return _static_text(node.args[0])
    return ""


def _call_name(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _normalized_sql(sql: str) -> str:
    stripped = sql.replace('"', "").replace("'", "")
    return " ".join(stripped.upper().split())


def _sql_is_risky(sql: str) -> bool:
    normalized = _normalized_sql(sql)
    if any(marker in normalized for marker in RLS_SQL_MARKERS):
        return True
    return any(
        f"ALTER TABLE {table.upper()}" in normalized or f"ALTER TABLE PUBLIC.{table.upper()}" in normalized
        for table in OWNER_SENSITIVE_TABLES
    )


def _table_arg(call: ast.Call) -> str | None:
    if call.args:
        table_name = _static_text(call.args[0])
        if table_name:
            return table_name
    for keyword in call.keywords:
        if keyword.arg == "table_name":
            table_name = _static_text(keyword.value)
            if table_name:
                return table_name
    return None


def _call_is_risky(call: ast.Call) -> bool:
    call_name = _call_name(call.func)
    method = call_name.rsplit(".", 1)[-1]

    if call_name.startswith("op.") and method in OWNER_SENSITIVE_OPS:
        table_name = _table_arg(call)
        if table_name in OWNER_SENSITIVE_TABLES:
            return True

    if method == "execute":
        sql = _static_text(call.args[0]) if call.args else ""
        if _sql_is_risky(sql):
            return True

    return False


def _is_risky(source: str, path: Path) -> bool:
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return False
    return any(_call_is_risky(node) for node in ast.walk(tree) if isinstance(node, ast.Call))


def main(argv: list[str]) -> int:
    failures: list[str] = []
    versions_dir = Path("alembic/versions")

    for raw in argv:
        path = Path(raw)
        if not path.exists() or path.suffix != ".py" or path.name.startswith("__"):
            continue
        source = path.read_text(encoding="utf-8")
        if not _is_risky(source, path):
            continue

        revision = _revision(path, source)
        if not revision:
            failures.append(f"{path}: risky migration but no parseable revision id")
            continue

        matches = list(versions_dir.glob(f"post_deploy_{revision}*.sql"))
        if not matches:
            failures.append(
                f"{path}: touches owner/RLS-sensitive DDL but has no alembic/versions/post_deploy_{revision}*.sql"
            )

    if failures:
        print("Post-deploy SQL guard failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print("Post-deploy SQL guard OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
