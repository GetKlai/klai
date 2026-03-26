"""
Sparse embedding client for BGE-M3 FlagEmbedding sidecar.

Calls the bge-m3-sparse FastAPI sidecar and returns a Qdrant SparseVector.
Falls back gracefully when the sidecar is unavailable.
"""
from __future__ import annotations

import logging

import httpx
from qdrant_client.models import SparseVector

from knowledge_ingest.config import settings

logger = logging.getLogger(__name__)


async def embed_sparse(text: str) -> SparseVector | None:
    """
    Call FlagEmbedding sidecar, return Qdrant SparseVector.
    Returns None if sidecar is unreachable (caller falls back to dense-only).
    """
    if not settings.sparse_sidecar_url:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{settings.sparse_sidecar_url}/embed_sparse",
                json={"text": text},
            )
            resp.raise_for_status()
            data = resp.json()
            return SparseVector(
                indices=data["indices"],
                values=data["values"],
            )
    except Exception as exc:
        logger.warning("sparse_sidecar_unavailable: %s", exc)
        return None
