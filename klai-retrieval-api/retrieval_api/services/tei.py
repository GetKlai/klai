"""TEI (text-embeddings-inference) client for dense embeddings.
TEI runs on gpu-01 at port 7997, tunneled to 172.18.0.1:7997 on core-01.
Uses the OpenAI-compatible /v1/embeddings API (same format as Infinity).
Do NOT confuse with Infinity (port 7998), which is the separate reranker service.
"""

from __future__ import annotations

import logging

import httpx
from qdrant_client.models import SparseVector

from retrieval_api.config import settings

logger = logging.getLogger(__name__)

_EMBED_MODEL = "BAAI/bge-m3"


async def embed_single(text: str) -> list[float]:
    """Embed a single text string via the Infinity /v1/embeddings endpoint.

    Returns a dense float vector.
    """
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{settings.tei_url}/v1/embeddings",
            json={"input": text, "model": _EMBED_MODEL},
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts via the Infinity /v1/embeddings endpoint."""
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{settings.tei_url}/v1/embeddings",
            json={"input": texts, "model": _EMBED_MODEL},
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        # Sort by index to preserve original order
        data.sort(key=lambda x: x["index"])
        return [item["embedding"] for item in data]


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
