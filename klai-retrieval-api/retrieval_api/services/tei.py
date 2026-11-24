"""TEI (text-embeddings-inference) client for dense embeddings.
TEI runs on gpu-01 at port 7997, tunneled to 172.18.0.1:7997 on core-01.
Uses the OpenAI-compatible /v1/embeddings API (same format as Infinity).
Do NOT confuse with Infinity (port 7998), which is the separate reranker service.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from qdrant_client.models import SparseVector

from retrieval_api.config import settings

logger = logging.getLogger(__name__)

# A restart of the dense service drops connections (on 2026-11-23 a search got
# "Server disconnected" as it came back) or answers 5xx. A search cannot wait
# out a whole restart (its health check failed for 2 and 4 minutes on
# 2026-11-22 and 2026-11-23): the widget gives /retrieve 10 s and retrieval_ms
# p95 was 4.6 s over the 7 days to 2026-11-24, so the retries wait at most
# 2.75 s in total and then the search answers 503 embedding_unavailable.
_RETRY_DELAYS_S = (0.25, 0.5, 1.0, 1.0)
# A connect that hangs (the tunnel endpoint gone) fails fast and is retried;
# reading the vector keeps the long timeout.
_TIMEOUT = httpx.Timeout(120.0, connect=1.0)


class EmbeddingUnavailable(RuntimeError):
    """The dense embedding service did not answer within the retry budget."""


def _transient(exc: httpx.HTTPError) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError) and not isinstance(exc, httpx.ReadTimeout)


def _embedding_payload(text_or_texts: str | list[str]) -> dict[str, object]:
    payload: dict[str, object] = {"input": text_or_texts}
    if settings.tei_model.strip():
        payload["model"] = settings.tei_model.strip()
    return payload


async def embed_single(text: str) -> list[float]:
    """Embed a single text string via the Infinity /v1/embeddings endpoint.

    Returns a dense float vector. A dropped connection or a 5xx is retried
    after each of ``_RETRY_DELAYS_S``; after the last one it raises
    :class:`EmbeddingUnavailable`.
    """
    for delay in _RETRY_DELAYS_S:
        try:
            return await _embed_once(text)
        except httpx.HTTPError as exc:
            if not _transient(exc):
                raise
            await asyncio.sleep(delay)
    try:
        return await _embed_once(text)
    except httpx.HTTPError as exc:
        if not _transient(exc):
            raise
        logger.warning("dense_embedding_unavailable", exc_info=True)
        raise EmbeddingUnavailable(type(exc).__name__) from exc


async def _embed_once(text: str) -> list[float]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{settings.tei_url}/v1/embeddings",
            json=_embedding_payload(text),
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts via the Infinity /v1/embeddings endpoint."""
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{settings.tei_url}/v1/embeddings",
            json=_embedding_payload(texts),
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
    except Exception:
        # F6 audit cleanup (TRY401): exc_info=True preserves traceback.
        logger.warning("sparse_sidecar_unavailable", exc_info=True)
        return None
