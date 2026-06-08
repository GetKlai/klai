#!/usr/bin/env python3
"""Upgrade the existing ``org_id`` payload index to a tenant index (is_tenant=True).

Qdrant 1.12+ native multitenancy builds a per-tenant HNSW subgraph for a
payload index flagged ``is_tenant=True``, so the mandatory ``org_id`` must-filter
does not degrade recall (GAP-TENANCY-01). ``ensure_collection`` now creates new
collections' ``org_id`` index with ``is_tenant=True``, but a collection that was
created earlier already has a plain keyword index. This one-off, idempotent
migration deletes that index and recreates it as a tenant index.

The operation is ONLINE and isolation-safe: while the index is being rebuilt,
``org_id`` filtered queries fall back to a (slower) full scan but still apply the
filter correctly — tenant isolation is never weakened.

Usage (on the server, where Qdrant is reachable):

    docker exec klai-core-knowledge-ingest-1 \
        python scripts/upgrade_org_id_tenant_index.py

Idempotent: safe to run multiple times.
"""

from __future__ import annotations

import asyncio

import structlog
from qdrant_client.models import KeywordIndexParams, KeywordIndexType

from knowledge_ingest import qdrant_store

logger = structlog.get_logger()


def _org_id_is_tenant(collection_info: object) -> bool:
    """Best-effort check whether the org_id index is already a tenant index."""
    schema = getattr(collection_info, "payload_schema", None) or {}
    org = schema.get("org_id") if isinstance(schema, dict) else None
    params = getattr(org, "params", None)
    return bool(getattr(params, "is_tenant", False))


async def upgrade() -> None:
    client = qdrant_store.get_client()
    collection = qdrant_store.COLLECTION

    info = await client.get_collection(collection)
    if _org_id_is_tenant(info):
        logger.info("org_id_tenant_index_already_current", collection=collection)
        return

    # Drop the existing plain keyword index (no-op if absent), then recreate as
    # a tenant index. delete_payload_index is safe on a present index; we guard
    # the absent case so the script is runnable on a fresh collection too.
    try:
        await client.delete_payload_index(collection, field_name="org_id")
        logger.info("org_id_index_dropped", collection=collection)
    except Exception:
        logger.warning("org_id_index_drop_skipped", collection=collection, exc_info=True)

    await client.create_payload_index(
        collection,
        field_name="org_id",
        field_schema=KeywordIndexParams(type=KeywordIndexType.KEYWORD, is_tenant=True),
    )
    logger.info("org_id_tenant_index_created", collection=collection, is_tenant=True)


if __name__ == "__main__":
    asyncio.run(upgrade())
