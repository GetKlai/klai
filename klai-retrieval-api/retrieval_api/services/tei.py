"""TEI (Text Embeddings Inference) client for embedding and reranking."""

from __future__ import annotations

import logging

import httpx
from qdrant_client.models import SparseVector

from retrieval_api.config import settings

logger = logging.getLogger(__name__)


async def embed_single(text: str) -> list[float]:
    """Embed a single text string via the TEI /embed endpoint.

    Returns a dense float vector.
    """
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{settings.tei_url}/embed",
            json={"inputs": text, "normalize": True},
        )
        resp.raise_for_status()
        # TEI returns [[float, ...]] for a single input
        embeddings = resp.json()
        if isinstance(embeddings[0], list):
            return embeddings[0]
        return embeddings


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts via the TEI /embed endpoint."""
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{settings.tei_url}/embed",
            json={"inputs": texts, "normalize": True},
        )
        resp.raise_for_status()
        return resp.json()


async def embed_sparse(text: str) -> SparseVector | None:
    """Embed text via the BGE-M3 sparse sidecar. Returns None if unavailable."""
    if not settings.sparse_sidecar_url:
        return None
    try:
        async with httpx.AsyncClient(timeout=settings.sparse_sidecar_timeout) as client:
            resp = await client.post(
                f"{settings.sparse_sidecar_url}/embed_sparse_batch",
                json={"texts": [text]},
            )
            resp.raise_for_status()
            item = resp.json()["results"][0]
            return SparseVector(indices=item["indices"], values=item["values"])
    except Exception as exc:
        logger.warning("sparse_sidecar_unavailable: %s", exc)
        return None
