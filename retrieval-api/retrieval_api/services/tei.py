"""TEI (Text Embeddings Inference) client for embedding and reranking."""

from __future__ import annotations

import logging

import httpx

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
