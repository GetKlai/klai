"""RLS silent-filter guard.

PostgreSQL Row-Level Security policies implemented as `USING (...)` filter
rows — they do NOT raise an error when the current session context doesn't
match. For SELECT this returns zero rows; for UPDATE and DELETE it quietly
affects zero rows. When `app.current_org_id` is not set (or set to a wrong
value), this turns into a class of silent bug where:

  - A /internal/* callback thinks it wrote taxonomy_node_ids to a gap row,
    but the UPDATE matched zero rows and nothing was persisted.
  - A fire-and-forget partner_api_keys last_used update appears to succeed
    but zero rows changed.

This module hooks SQLAlchemy's `after_cursor_execute` event to detect these
silent-filter patterns and log them at ERROR level with traceback.

Two modes:

- STRICT=False (default, production): log an error with stack context so the
  pattern surfaces in VictoriaLogs. The app keeps running — re-raising from
  an event hook would turn harmless rowcount=0 UPDATEs (valid business
  logic: "UPDATE ... WHERE id = X" where X was already deleted) into 500s.
- STRICT=True (tests only): raise RuntimeError. Lets regression tests assert
  that a code path doesn't accidentally drop into the silent-filter case.
"""

from __future__ import annotations

import logging
import os
import traceback
from collections.abc import Iterable
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# Tables with RLS policies that cover UPDATE and/or DELETE. Sourced from
# `pg_policies` via `cmd IN ('ALL','UPDATE','DELETE')` on 2026-04-21. If a
# new RLS policy is added to a table, append it here so the guard covers it.
#
# Omitted deliberately:
#   - portal_audit_log, product_events: SELECT-scoped / INSERT-permissive;
#     UPDATE/DELETE are not a real path.
#   - portal_users: ALL policy but updates come via admin endpoints only,
#     and tenant context is always bound by that point.
RLS_DML_TABLES: frozenset[str] = frozenset(
    {
        "partner_api_key_kb_access",
        "partner_api_keys",
        "portal_connectors",
        "portal_group_kb_access",
        "portal_group_memberships",
        "portal_group_products",
        "portal_groups",
        "portal_kb_tombstones",
        "portal_knowledge_bases",
        "portal_retrieval_gaps",
        "portal_taxonomy_nodes",
        "portal_taxonomy_proposals",
        "portal_user_kb_access",
        "portal_user_products",
        "vexa_meetings",
        "widget_kb_access",
        "widgets",
    }
)


def _strict_mode() -> bool:
    """Whether rowcount=0 detections should raise. Set via env var for tests."""
    return os.environ.get("PORTAL_RLS_GUARD_STRICT", "").lower() in ("1", "true", "yes")


def _extract_dml_table(statement: str) -> tuple[str, str] | None:
    """Return (op, table) if statement is a DML against a known RLS table.

    Cheap pattern match — no SQL parser. Handles the three DML shapes
    SQLAlchemy emits: `UPDATE x SET ...`, `DELETE FROM x WHERE ...`,
    and their aliased / schema-qualified variants.
    """
    head = statement.lstrip()[:64].lower()
    if head.startswith("update "):
        op = "UPDATE"
        rest = head[len("update ") :].lstrip()
    elif head.startswith("delete "):
        op = "DELETE"
        rest = head[len("delete ") :].lstrip()
        if rest.startswith("from "):
            rest = rest[len("from ") :].lstrip()
    else:
        return None

    # Strip optional `"schema".` or `schema.` prefix; peel the identifier
    # until whitespace / delimiter. Dots are accepted so schema-qualified
    # names like `public.portal_groups` parse correctly; quotes are peeled
    # too (`"public"."portal_groups"`).
    token = rest
    name = ""
    for ch in token:
        if ch.isalnum() or ch in ("_", ".", '"'):
            name += ch
        else:
            break
    # Strip all-quotes segments and schema prefix, leave only the final
    # identifier (the actual table name).
    name = name.replace('"', "")
    if "." in name:
        name = name.rsplit(".", 1)[-1]
    if not name:
        return None
    if name not in RLS_DML_TABLES:
        return None
    return op, name


def _on_after_cursor_execute(
    _conn: Any,
    cursor: Any,
    statement: str,
    _parameters: Any,
    _context: Any,
    _executemany: bool,
) -> None:
    rowcount = getattr(cursor, "rowcount", None)
    if rowcount is None or rowcount > 0:
        return
    match = _extract_dml_table(statement)
    if match is None:
        return
    op, table = match
    # The calling frame tells us which application code triggered this.
    # Limit traceback to application frames to keep log volume reasonable.
    stack = traceback.extract_stack(limit=25)
    app_frames = [f for f in stack if "/site-packages/" not in f.filename][-8:]
    caller = "\n".join(f"    {f.filename}:{f.lineno} in {f.name}" for f in app_frames)
    statement_preview = " ".join(statement.split())[:180]
    msg = (
        f"RLS silent-filter: {op} on {table} matched 0 rows. "
        f"Likely cause: app.current_org_id missing or mismatched on this "
        f"connection. Statement: {statement_preview}"
    )
    if _strict_mode():
        raise RuntimeError(msg)
    logger.error("%s\n  caller:\n%s", msg, caller)


# @MX:ANCHOR fan_in=1 — single caller (app.main lifespan) but this is the
#   lock-in point that makes the entire strict-RLS regime observable. Removing
#   this install call silently disables silent-filter detection across all
#   category-D tables. Treat as load-bearing.
# @MX:REASON listener registration is idempotent but install_rls_guard is the
#   only legitimate way to enable it — direct event.listen() calls would skip
#   the idempotency check.
# @MX:SPEC SPEC-SEC-007
def install_rls_guard(engine: AsyncEngine | Engine) -> None:
    """Install the silent-filter guard on the given engine.

    Call once at application startup. Idempotent — re-registering the
    same listener has no effect.
    """
    sync_engine = engine.sync_engine if isinstance(engine, AsyncEngine) else engine
    if event.contains(sync_engine, "after_cursor_execute", _on_after_cursor_execute):
        return
    event.listen(sync_engine, "after_cursor_execute", _on_after_cursor_execute)


# Re-export for test assertions
__all__: Iterable[str] = (
    "RLS_DML_TABLES",
    "install_rls_guard",
)
