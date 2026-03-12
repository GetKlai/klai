"""
HTTP client for Text Embeddings Inference (TEI) running BGE-M3.
Produces 1024-dimensional dense vectors.
"""
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_BATCH_SIZE = 32  # TEI default limit for BGE-M3


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Embed a list of texts using TEI. Returns list of 1024-dim vectors.
    Batches requests to stay within TEI batch limits.
    """
    all_embeddings: list[list[float]] = []

    async with httpx.AsyncClient(timeout=120.0) as client:
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i : i + _BATCH_SIZE]
            resp = await client.post(
                f"{settings.tei_url}/embed",
                json={"inputs": batch, "normalize": True},
            )
            resp.raise_for_status()
            embeddings = resp.json()
            all_embeddings.extend(embeddings)

    return all_embeddings


async def embed_single(text: str) -> list[float]:
    """Embed a single text and return its vector."""
    results = await embed_texts([text])
    return results[0]
