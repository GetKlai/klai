"""
HTTP client for TEI (text-embeddings-inference) running BGE-M3.
TEI is on gpu-01 port 7997, tunneled to 172.18.0.1:7997 on core-01.
Uses the OpenAI-compatible /v1/embeddings API. Produces 1024-dim dense vectors.
Not to be confused with Infinity (port 7998), which handles reranking only.
"""
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_BATCH_SIZE = 32
_EMBED_MODEL = "BAAI/bge-m3"


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Embed a list of texts using Infinity. Returns list of 1024-dim vectors.
    Batches requests to stay within Infinity batch limits.
    """
    all_embeddings: list[list[float]] = []

    async with httpx.AsyncClient(timeout=120.0) as client:
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i : i + _BATCH_SIZE]
            resp = await client.post(
                f"{settings.tei_url}/v1/embeddings",
                json={"input": batch, "model": _EMBED_MODEL},
            )
            resp.raise_for_status()
            data = resp.json()["data"]
            data.sort(key=lambda x: x["index"])
            all_embeddings.extend(item["embedding"] for item in data)

    return all_embeddings


async def embed_single(text: str) -> list[float]:
    """Embed a single text and return its vector."""
    results = await embed_texts([text])
    return results[0]
