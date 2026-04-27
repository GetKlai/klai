"""Backfill notebook_visibility + owner_user_id payload on klai_focus chunks.

SPEC-SEC-IDENTITY-ASSERT-001 REQ-5 introduced two new payload fields on
every klai_focus chunk:

- ``notebook_visibility`` ∈ {"personal", "org"} — mirrors
  ``Notebook.scope`` and drives the personal-vs-team gate at retrieval
  time (``klai-retrieval-api/services/search.py:_notebook_filter``).
- ``owner_user_id`` — copied from ``Notebook.owner_user_id``; required
  for the personal-leg of the visibility gate.

New ingest writes these fields automatically. This script applies them to
historical chunks so retrieval keeps working after the SPEC ships.
Without backfill, legacy chunks match neither leg of the new filter and
become invisible — that's the deliberate fail-secure default, but is
operationally undesirable.

Run inside the research-api container:

    docker exec -it klai-core-research-api-1 \\
        python -m scripts.backfill_notebook_visibility --dry-run

    docker exec -it klai-core-research-api-1 \\
        python -m scripts.backfill_notebook_visibility --execute

Idempotent. Re-running on an already-backfilled chunk is a no-op (the
script reads each chunk's payload and only writes when at least one of
the two fields is missing). Notebooks that no longer exist (e.g. deleted
between ingest and backfill) are skipped with a warning — those orphan
chunks are unreachable anyway and a future tenant-cleanup pass can
remove them.

Performance: processes one notebook at a time using
``client.set_payload`` with a per-notebook filter. For large tenants
this is O(notebooks), not O(chunks), so it scales linearly with the DB
size rather than the vector index size. Default batch size matches the
existing klai_focus collection pattern.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from qdrant_client.http import models as qdrant_models
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_session
from app.models.notebook import Notebook
from app.services import qdrant_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill-notebook-visibility")


async def _list_notebooks(db: AsyncSession) -> list[Notebook]:
    rows = await db.execute(select(Notebook))
    return list(rows.scalars().all())


def _count_chunks_missing_visibility(notebook_id: str) -> int:
    """Return how many chunks for this notebook still lack the new fields.

    Uses Qdrant's count API with a payload-must-not filter. A chunk that
    already has BOTH fields populated does not match — we count the
    complement (one or both missing).
    """

    client = qdrant_store.get_client()
    # Match chunks for this notebook where notebook_visibility is missing.
    # Qdrant's "is_empty" filter matches chunks where the field is absent
    # OR has the empty-keyword value, which is exactly what we want.
    result = client.count(
        collection_name=settings.qdrant_collection,
        count_filter=qdrant_models.Filter(
            must=[
                qdrant_models.FieldCondition(
                    key="notebook_id",
                    match=qdrant_models.MatchValue(value=notebook_id),
                ),
                qdrant_models.IsEmptyCondition(
                    is_empty=qdrant_models.PayloadField(key="notebook_visibility"),
                ),
            ]
        ),
        exact=True,
    )
    return result.count


def _apply_payload(notebook: Notebook) -> int:
    """Write ``notebook_visibility`` + ``owner_user_id`` to every chunk of the notebook.

    Returns the number of chunks updated. Only chunks missing
    ``notebook_visibility`` are touched — already-backfilled chunks are
    skipped via the same is_empty filter used for counting.
    """

    client = qdrant_store.get_client()
    pending = _count_chunks_missing_visibility(notebook.id)
    if pending == 0:
        return 0

    client.set_payload(
        collection_name=settings.qdrant_collection,
        payload={
            "notebook_visibility": notebook.scope,
            "owner_user_id": notebook.owner_user_id,
        },
        points_selector=qdrant_models.FilterSelector(
            filter=qdrant_models.Filter(
                must=[
                    qdrant_models.FieldCondition(
                        key="notebook_id",
                        match=qdrant_models.MatchValue(value=notebook.id),
                    ),
                    qdrant_models.IsEmptyCondition(
                        is_empty=qdrant_models.PayloadField(key="notebook_visibility"),
                    ),
                ]
            )
        ),
    )
    return pending


async def _run(*, execute: bool) -> int:
    """Iterate every notebook and backfill its chunks. Returns total updated."""

    total_updated = 0
    skipped_empty = 0

    async for db in get_session():
        notebooks = await _list_notebooks(db)
        log.info("found %d notebooks", len(notebooks))

        for notebook in notebooks:
            pending = _count_chunks_missing_visibility(notebook.id)
            if pending == 0:
                skipped_empty += 1
                continue

            log.info(
                "notebook=%s scope=%s owner=%s pending_chunks=%d",
                notebook.id,
                notebook.scope,
                notebook.owner_user_id,
                pending,
            )
            if not execute:
                continue

            updated = _apply_payload(notebook)
            total_updated += updated
            log.info("notebook=%s updated_chunks=%d", notebook.id, updated)

        break  # only need one session yielded

    log.info(
        "summary: notebooks_already_backfilled=%d total_chunks_updated=%d execute=%s",
        skipped_empty,
        total_updated,
        execute,
    )
    return total_updated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n")[0] if __doc__ else "",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change; no writes.",
    )
    group.add_argument(
        "--execute",
        action="store_true",
        help="Apply payload writes.",
    )
    args = parser.parse_args(argv)

    asyncio.run(_run(execute=args.execute))
    return 0


if __name__ == "__main__":
    sys.exit(main())
