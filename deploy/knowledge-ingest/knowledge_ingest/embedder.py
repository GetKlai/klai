"""
TEI (Text Embeddings Inference) client for BGE-M3 dense embeddings.
TEI is already running on klai-net with BAAI/bge-m3 loaded.
"""
import httpx

from knowledge_ingest.config import settings

EMBED_DIM = 1024  # BGE-M3 dense output dimension


async def embed(texts: list[str]) -> list[list[float]]:
    """Return dense embeddings for a list of texts."""
    if not texts:
        return []
    async with httpx.AsyncClient(base_url=settings.tei_url, timeout=30.0) as client:
        resp = await client.post("/embed", json={"inputs": texts})
        resp.raise_for_status()
        return resp.json()  # list[list[float]]


async def embed_one(text: str) -> list[float]:
    results = await embed([text])
    return results[0]
