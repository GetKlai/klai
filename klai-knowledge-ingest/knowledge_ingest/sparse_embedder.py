"""
Sparse embedding client for BGE-M3 FlagEmbedding sidecar.

Calls the bge-m3-sparse FastAPI sidecar and returns a Qdrant SparseVector.
Falls back gracefully when the sidecar is unavailable.
"""
from __future__ import annotations

import structlog

import httpx
from qdrant_client.models import SparseVector

from knowledge_ingest.config import settings

logger = structlog.get_logger()


async def embed_sparse_batch(texts: list[str]) -> list[SparseVector | None]:
    """
    Embed a list of texts via the FlagEmbedding sidecar.
    Sends texts in batches of settings.sparse_sidecar_batch_size.
    Never raises -- sidecar failures produce None entries with WARNING log.
    """
    if not settings.sparse_sidecar_url:
        return [None] * len(texts)

    results: list[SparseVector | None] = []
    batch_size = settings.sparse_sidecar_batch_size

    for start in range(0, len(texts), batch_size):
        sub_batch = texts[start : start + batch_size]
        try:
            async with httpx.AsyncClient(timeout=settings.sparse_sidecar_timeout) as client:
                resp = await client.post(
                    f"{settings.sparse_sidecar_url}/embed_sparse_batch",
                    json={"texts": sub_batch},
                )
                resp.raise_for_status()
                data = resp.json()
                for item in data["results"]:
                    results.append(
                        SparseVector(
                            indices=item["indices"],
                            values=item["values"],
                        )
                    )
        except Exception as exc:
            logger.warning("sparse_sidecar_batch_unavailable", error=str(exc))
            results.extend([None] * len(sub_batch))

    return results


async def embed_sparse(text: str) -> SparseVector | None:
    """
    Call FlagEmbedding sidecar, return Qdrant SparseVector.
    Returns None if sidecar is unreachable (caller falls back to dense-only).
    """
    batch_result = await embed_sparse_batch([text])
    return batch_result[0]
