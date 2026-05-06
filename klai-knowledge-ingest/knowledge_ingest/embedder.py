"""
TEI (text-embeddings-inference) client for BGE-M3 dense embeddings.
TEI runs on gpu-01 and is accessible via SSH tunnel at 172.18.0.1:7997.
Note: TEI uses the OpenAI-compatible /v1/embeddings API — do not confuse with
Infinity (port 7998), which is a separate service used exclusively for reranking.
"""

import asyncio
import random

import httpx
import structlog

from knowledge_ingest.config import settings

logger = structlog.get_logger()

EMBED_DIM = 1024  # BGE-M3 dense output dimension
_EMBED_MODEL = "BAAI/bge-m3"

# Batch size for Infinity requests — keeps queue_time manageable
_BATCH_SIZE = 32

# Audit 2026-05-06 finding 6: retry budget tuned for the realistic
# TEI-restart window on gpu-01 (BGE-M3 model reload typically 15-45s).
# Five attempts with full-jitter exponential backoff (capped at 30s
# per sleep) gives a worst-case sleep budget of ~62s and a mean of ~30s
# — enough to ride out a container restart without burning the call.
#
# Full jitter (random.uniform(0, base)) is the AWS-recommended pattern
# for thundering-herd protection: bulk-sync of 50 pages no longer wakes
# all in-flight retries at the same wall-clock instant during recovery.
_MAX_ATTEMPTS = 5
_MAX_BACKOFF_SECONDS = 30


def _jitter_backoff(attempt: int) -> float:
    """Full-jitter backoff: ``random.uniform(0, min(2**attempt, _MAX_BACKOFF_SECONDS))``.

    Extracted as a helper so the ruff ``S311`` (cryptographic randomness)
    suppression has a single, well-scoped home — jitter for retry
    spacing has no security implication.
    """
    return random.uniform(0, min(2**attempt, _MAX_BACKOFF_SECONDS))  # noqa: S311


async def _embed_batch(client: httpx.AsyncClient, texts: list[str]) -> list[list[float]]:
    """Embed a single batch with retry on transient errors.

    Retries on httpx.ReadTimeout, httpx.ConnectTimeout, and HTTP 5xx.
    Final exhaustion logs at error level so the existing
    obs-001-ingest-error-rate-elevated Grafana alert (level:error) fires.
    """
    last_exc: Exception | None = None
    last_status: int | None = None
    for attempt in range(_MAX_ATTEMPTS):
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
            wait = _jitter_backoff(attempt)
            logger.warning(
                "tei_embed_timeout",
                attempt=attempt + 1,
                max_attempts=_MAX_ATTEMPTS,
                texts=len(texts),
                wait_s=wait,
            )
            await asyncio.sleep(wait)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                last_exc = exc
                last_status = exc.response.status_code
                wait = _jitter_backoff(attempt)
                logger.warning(
                    "tei_embed_5xx",
                    attempt=attempt + 1,
                    max_attempts=_MAX_ATTEMPTS,
                    status=last_status,
                    wait_s=wait,
                )
                await asyncio.sleep(wait)
            else:
                raise

    # All retries exhausted. Promote to error so the existing
    # ingest-error-rate Grafana alert fires; bulk-sync callers
    # currently swallow embed exceptions and skip the page silently
    # (see audit finding 6 — combines with finding 5 silent-degrade).
    assert last_exc is not None
    logger.error(
        "tei_embed_failed_max_attempts",
        attempts=_MAX_ATTEMPTS,
        last_status=last_status,
        texts=len(texts),
        error_type=type(last_exc).__name__,
        error_message=str(last_exc),
    )
    raise last_exc


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
