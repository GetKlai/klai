"""Shared start conditions of every operation that rewrites knowledge data.

Three properties, the same for rebuild, patch, backfill, purge and repair:

* the owner's approval for this one run, carried as ``INGEST_OK=1``
  (AGENTS.md: production knowledge data is rewritten only with approval per run);
* a preview that only counts and shows the first changes, which is what a run
  does unless ``--execute`` is given;
* mutual exclusion per knowledge base, so two runs cannot overlap on one.
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from typing import Any

from knowledge_ingest.db import get_pool

MARKER = "INGEST_OK"


class ApprovalRequired(RuntimeError):
    """A write was started without the owner's approval marker."""


class OperationBusy(RuntimeError):
    """Another run holds this knowledge base."""


def require_ingest_ok(name: str) -> None:
    if os.environ.get(MARKER) != "1":
        raise ApprovalRequired(
            f"{name}: a write run needs {MARKER}=1, set after the owner's yes for this run"
        )


def guard_cli(name: str, execute: bool) -> None:
    """Command-line entry: refuse a write run without the marker, exit code 2."""
    if not execute:
        return
    try:
        require_ingest_ok(name)
    except ApprovalRequired as exc:
        sys.stderr.write(f"{exc}\n")
        raise SystemExit(2) from None


async def _take(conn: Any, key: str, shared: bool) -> bool:
    if shared:
        return await conn.fetchval(
            "SELECT pg_try_advisory_lock_shared(hashtextextended($1, 0))", key
        )
    return await conn.fetchval("SELECT pg_try_advisory_lock(hashtextextended($1, 0))", key)


async def _release(conn: Any, key: str, shared: bool) -> None:
    if shared:
        await conn.execute("SELECT pg_advisory_unlock_shared(hashtextextended($1, 0))", key)
    else:
        await conn.execute("SELECT pg_advisory_unlock(hashtextextended($1, 0))", key)


@asynccontextmanager
async def kb_operation(
    org_id: str | None, kb_slugs: Iterable[str] | None, *, execute: bool
) -> AsyncIterator[None]:
    """Hold Postgres advisory locks on a knowledge base for the length of a write run.

    A preview only reads and takes no lock. ``kb_slugs=None`` is a run on the
    whole org, ``org_id=None`` a run on every org. A run holds its own level
    exclusively and every wider level shared, so a run on one knowledge base
    and a run on its org (or on every org) exclude each other, while runs on
    different knowledge bases go side by side.
    """
    if not execute:
        yield
        return
    if org_id is None:
        wanted = [("*:*", False)]
    elif not kb_slugs:
        wanted = [("*:*", True), (f"{org_id}:*", False)]
    else:
        wanted = [("*:*", True), (f"{org_id}:*", True)]
        wanted += [(f"{org_id}:{kb_slug}", False) for kb_slug in sorted(kb_slugs)]
    # Advisory locks belong to the session, so one connection holds them all.
    async with (await get_pool()).acquire() as conn:
        held: list[tuple[str, bool]] = []
        try:
            for name, shared in wanted:
                key = f"kb-operation:{name}"
                if not await _take(conn, key, shared):
                    raise OperationBusy(f"another operation is running on {key}")
                held.append((key, shared))
            yield
        finally:
            for key, shared in reversed(held):
                await _release(conn, key, shared)
