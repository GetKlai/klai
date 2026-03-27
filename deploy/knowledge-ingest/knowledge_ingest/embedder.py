"""
TEI (Text Embeddings Inference) client for BGE-M3 dense embeddings.
TEI is already running on klai-net with BAAI/bge-m3 loaded.
"""
import asyncio
import logging

import httpx

from knowledge_ingest.config import settings

logger = logging.getLogger(__name__)

EMBED_DIM = 1024  # BGE-M3 dense output dimension

# Batch size for TEI requests — keeps queue_time manageable
_TEI_BATCH_SIZE = 32


async def _embed_batch(
    client: httpx.AsyncClient, texts: list[str]
) -> list[list[float]]:
    """Embed a single batch with retry on transient errors."""
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = await client.post("/embed", json={"inputs": texts})
            resp.raise_for_status()
            return resp.json()
        except (httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
            last_exc = exc
            wait = 2**attempt
            logger.warning(
                "TEI embed timeout (attempt %d/3, %d texts), retrying in %ds",
                attempt + 1,
                len(texts),
                wait,
            )
            await asyncio.sleep(wait)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                last_exc = exc
                wait = 2**attempt
                logger.warning(
                    "TEI embed 5xx (attempt %d/3, status %d), retrying in %ds",
                    attempt + 1,
                    exc.response.status_code,
                    wait,
                )
                await asyncio.sleep(wait)
            else:
                raise
    raise last_exc  # type: ignore[misc]


async def embed(texts: list[str]) -> list[list[float]]:
    """Return dense embeddings for a list of texts.

    Splits into batches of _TEI_BATCH_SIZE to keep TEI queue_time low
    and avoid client-side read timeouts on large documents.
    """
    if not texts:
        return []
    async with httpx.AsyncClient(
        base_url=settings.tei_url,
        timeout=settings.tei_timeout,
    ) as client:
        if len(texts) <= _TEI_BATCH_SIZE:
            return await _embed_batch(client, texts)

        results: list[list[float]] = []
        for start in range(0, len(texts), _TEI_BATCH_SIZE):
            batch = texts[start : start + _TEI_BATCH_SIZE]
            batch_result = await _embed_batch(client, batch)
            results.extend(batch_result)
        return results


async def embed_one(text: str) -> list[float]:
    results = await embed([text])
    return results[0]
