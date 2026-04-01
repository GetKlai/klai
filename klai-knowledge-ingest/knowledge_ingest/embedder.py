"""
TEI (text-embeddings-inference) client for BGE-M3 dense embeddings.
TEI runs on gpu-01 and is accessible via SSH tunnel at 172.18.0.1:7997.
Note: TEI uses the OpenAI-compatible /v1/embeddings API — do not confuse with
Infinity (port 7998), which is a separate service used exclusively for reranking.
"""
import asyncio
import structlog

import httpx

from knowledge_ingest.config import settings

logger = structlog.get_logger()

EMBED_DIM = 1024  # BGE-M3 dense output dimension
_EMBED_MODEL = "BAAI/bge-m3"

# Batch size for Infinity requests — keeps queue_time manageable
_BATCH_SIZE = 32


async def _embed_batch(
    client: httpx.AsyncClient, texts: list[str]
) -> list[list[float]]:
    """Embed a single batch with retry on transient errors."""
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = await client.post(
                "/v1/embeddings",
                json={"input": texts, "model": _EMBED_MODEL},
            )
            resp.raise_for_status()
            data = resp.json()["data"]
            data.sort(key=lambda x: x["index"])
            return [item["embedding"] for item in data]
        except (httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
            last_exc = exc
            wait = 2**attempt
            logger.warning(
                "tei_embed_timeout",
                attempt=attempt + 1,
                texts=len(texts),
                wait_s=wait,
            )
            await asyncio.sleep(wait)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                last_exc = exc
                wait = 2**attempt
                logger.warning(
                    "tei_embed_5xx",
                    attempt=attempt + 1,
                    status=exc.response.status_code,
                    wait_s=wait,
                )
                await asyncio.sleep(wait)
            else:
                raise
    raise last_exc  # type: ignore[misc]


async def embed(texts: list[str]) -> list[list[float]]:
    """Return dense embeddings for a list of texts.

    Splits into batches of _BATCH_SIZE to keep Infinity queue_time low
    and avoid client-side read timeouts on large documents.
    """
    if not texts:
        return []
    async with httpx.AsyncClient(
        base_url=settings.tei_url,
        timeout=settings.tei_timeout,
    ) as client:
        if len(texts) <= _BATCH_SIZE:
            return await _embed_batch(client, texts)

        results: list[list[float]] = []
        for start in range(0, len(texts), _BATCH_SIZE):
            batch = texts[start : start + _BATCH_SIZE]
            batch_result = await _embed_batch(client, batch)
            results.extend(batch_result)
        return results


async def embed_one(text: str) -> list[float]:
    results = await embed([text])
    return results[0]
