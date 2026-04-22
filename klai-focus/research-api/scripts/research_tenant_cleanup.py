"""Manual tenant cleanup for research-uploads.

Use when a tenant is decommissioned and we need to purge their uploaded
documents from the volume + the database. Until portal-api gains a tenant-
deletion flow that calls this automatically, ops runs it on demand.

Run inside the research-api container:

    docker exec -it klai-core-research-api-1 \\
        python -m scripts.research_tenant_cleanup --tenant-id <ID> --dry-run

Steps:
  1. Lists everything that would be removed (files, notebooks, sources).
  2. With --execute, drops Source/Notebook/Chunk rows for that tenant,
     deletes Qdrant vectors, and finally removes the on-disk subtree.

Idempotent: re-running on an already-cleaned tenant is a no-op. Refuses
to run with an empty tenant_id (would otherwise wipe every tenant's files).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.models.chunk import Chunk
from app.models.notebook import Notebook
from app.models.source import Source
from app.services import qdrant_store, upload_storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("research-tenant-cleanup")


async def _summarise(db: AsyncSession, tenant_id: str) -> tuple[int, int, int]:
    """Count notebooks / sources / on-disk files for the tenant."""
    nb_count = (await db.execute(
        select(Notebook).where(Notebook.tenant_id == tenant_id)
    )).scalars().all()
    src_count = (await db.execute(
        select(Source).where(Source.tenant_id == tenant_id)
    )).scalars().all()
    files = list(upload_storage.list_tenant_files(tenant_id))
    return len(nb_count), len(src_count), len(files)


async def _execute(db: AsyncSession, tenant_id: str) -> None:
    """Drop DB rows + Qdrant vectors + on-disk files for the tenant."""
    # Notebooks → fan out to sources → fan out to chunks.
    nb_ids = [
        row[0]
        for row in (await db.execute(
            select(Notebook.id).where(Notebook.tenant_id == tenant_id)
        )).fetchall()
    ]
    src_ids = [
        row[0]
        for row in (await db.execute(
            select(Source.id).where(Source.tenant_id == tenant_id)
        )).fetchall()
    ]

    if src_ids:
        await db.execute(delete(Chunk).where(Chunk.source_id.in_(src_ids)))
    await db.execute(delete(Source).where(Source.tenant_id == tenant_id))
    await db.execute(delete(Notebook).where(Notebook.tenant_id == tenant_id))
    await db.commit()
    log.info("postgres rows removed: %d notebooks, %d sources", len(nb_ids), len(src_ids))

    for nb_id in nb_ids:
        try:
            qdrant_store.delete_by_notebook(nb_id, tenant_id)
        except Exception as exc:
            log.warning("qdrant delete failed for nb %s: %s", nb_id, exc)
    log.info("qdrant vectors deleted across %d notebooks", len(nb_ids))

    removed = upload_storage.cleanup_tenant(tenant_id)
    log.info("upload files removed: %d", removed)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--tenant-id", required=True, help="The tenant (org) id to purge")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete (default: dry-run, only prints the plan).",
    )
    args = parser.parse_args()

    if not args.tenant_id.strip():
        log.error("Refusing to run with empty --tenant-id (would wipe every tenant).")
        return 2

    async with get_session() as db:
        nb, src, files = await _summarise(db, args.tenant_id)
        log.info(
            "Tenant %s: %d notebooks, %d sources, %d files on disk",
            args.tenant_id, nb, src, files,
        )
        if nb == 0 and src == 0 and files == 0:
            log.info("Nothing to clean — exiting.")
            return 0
        if not args.execute:
            log.info("Dry-run: re-run with --execute to actually purge.")
            return 0
        log.info("Executing cleanup…")
        await _execute(db, args.tenant_id)
        log.info("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
