"""
Sparse embedding client for BGE-M3 FlagEmbedding sidecar.

Calls the bge-m3-sparse FastAPI sidecar and returns Qdrant SparseVectors.
Every point in klai_knowledge needs one, so an unavailable sidecar raises
:class:`~knowledge_ingest.embedder.EmbeddingUnavailable` instead of handing back
nothing, and a request it rejects raises :class:`SparseEmbeddingRejected`.
"""

from __future__ import annotations

import asyncio
import random

import httpx
import structlog
from qdrant_client.models import SparseVector

from knowledge_ingest.config import settings
from knowledge_ingest.embedder import EmbeddingUnavailable

logger = structlog.get_logger()

# Three tries with full-jitter waits of at most 2 s and 4 s ride out a dropped
# tunnel or a sidecar swap inside one call. A restart of the GPU server (about
# six minutes on 2026-11-22 and 2026-11-23) is longer than any caller can wait;
# the jobs that write points wait that out (embedder.EMBEDDING_RETRY_SECONDS).
_MAX_ATTEMPTS = 3


class SparseEmbeddingRejected(ValueError):
    """The sidecar answered, and refused these texts: no retry makes it accept them."""


async def _embed_sub_batch(client: httpx.AsyncClient, texts: list[str]) -> list[SparseVector]:
    resp = await client.post(
        f"{settings.sparse_sidecar_url}/embed_sparse_batch",
        json={"texts": texts},
    )
    if 400 <= resp.status_code < 500:
        raise SparseEmbeddingRejected(f"sidecar answered HTTP {resp.status_code}")
    resp.raise_for_status()
    data = resp.json()
    if len(data["results"]) != len(texts):
        # Shifted vectors would land on the wrong chunks.
        raise SparseEmbeddingRejected(
            f"sidecar returned {len(data['results'])} results for {len(texts)} texts"
        )
    return [
        SparseVector(indices=item["indices"], values=item["values"]) for item in data["results"]
    ]


async def embed_sparse_batch(texts: list[str]) -> list[SparseVector]:
    """Embed ``texts`` via the sidecar in batches of ``sparse_sidecar_batch_size``.

    Raises :class:`EmbeddingUnavailable` when the sidecar is not configured or
    a batch still fails after ``_MAX_ATTEMPTS`` tries, and
    :class:`SparseEmbeddingRejected` at once on a 4xx or a result count that
    does not match the texts.
    """
    if not settings.sparse_sidecar_url:
        raise EmbeddingUnavailable("sparse_sidecar_url is not configured")

    results: list[SparseVector] = []
    batch_size = settings.sparse_sidecar_batch_size
    async with httpx.AsyncClient(timeout=settings.sparse_sidecar_timeout) as client:
        for start in range(0, len(texts), batch_size):
            sub_batch = texts[start : start + batch_size]
            for attempt in range(1, _MAX_ATTEMPTS + 1):
                try:
                    results += await _embed_sub_batch(client, sub_batch)
                    break
                except SparseEmbeddingRejected:
                    raise
                except Exception as exc:
                    logger.warning(
                        "sparse_sidecar_batch_unavailable",
                        attempt=attempt,
                        max_attempts=_MAX_ATTEMPTS,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                    if attempt == _MAX_ATTEMPTS:
                        raise EmbeddingUnavailable(
                            f"sparse sidecar failed {_MAX_ATTEMPTS} times: {type(exc).__name__}"
                        ) from exc
                    await asyncio.sleep(random.uniform(0, 2**attempt))  # noqa: S311
    return results
