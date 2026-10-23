"""Tell a FalkorDB that died under an episode from one that was already down.

On 23 October 2026 one document's extraction segfaulted FalkorDB on every
attempt, and the retries kept it crashing for seven hours. The server's
``run_id`` changes on every restart, so reading it before and after a failed
``add_episode`` shows whether the server went away while that call ran.
"""

from __future__ import annotations

import asyncio

import redis.asyncio as aioredis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from knowledge_ingest.config import settings


class FalkorDBDiedDuringEpisode(RuntimeError):
    """FalkorDB restarted, or stopped answering, while this episode's write ran."""


async def falkordb_run_id() -> str | None:
    """FalkorDB's current ``run_id``, or None when it does not answer.

    While FalkorDB reloads its dump (99-105 s on 23 October) it accepts the
    connection and answers nothing, so the 3 s bound is what returns None.
    No retries: a down server is an answer here, not something to wait out.
    """
    client = aioredis.Redis(
        host=settings.falkordb_host,
        port=settings.falkordb_port,
        retry=Retry(NoBackoff(), 0),
    )
    try:
        info = await asyncio.wait_for(client.info("server"), timeout=3.0)
    except Exception:
        return None
    finally:
        await client.aclose()
    return info.get("run_id")
