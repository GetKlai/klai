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
from contextlib import AsyncExitStack, asynccontextmanager

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


@asynccontextmanager
async def kb_operation(
    org_id: str | None, kb_slugs: Iterable[str] | None, *, execute: bool
) -> AsyncIterator[None]:
    """Hold a Postgres advisory lock per knowledge base for the length of a write run.

    A preview only reads and takes no lock. ``kb_slugs=None`` locks the whole
    org (``org_id=None``: every org) under its own key: runs of different width
    do not exclude each other, only runs of the same width do.
    """
    if not execute:
        yield
        return
    async with AsyncExitStack() as stack:
        for kb_slug in sorted(kb_slugs) if kb_slugs else [None]:
            key = f"kb-operation:{org_id or '*'}:{kb_slug or '*'}"
            conn = await stack.enter_async_context((await get_pool()).acquire())
            if not await conn.fetchval("SELECT pg_try_advisory_lock(hashtextextended($1, 0))", key):
                raise OperationBusy(f"another operation is running on {key}")
            stack.push_async_callback(
                conn.execute, "SELECT pg_advisory_unlock(hashtextextended($1, 0))", key
            )
        yield
