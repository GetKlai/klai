"""Tell a FalkorDB that died under an episode from one that was already down.

On 23 October 2026 one document's extraction segfaulted FalkorDB on every
attempt, and the retries kept it crashing for seven hours. The server's
``run_id`` changes on every restart, so reading it before and after a failed
``add_episode`` shows whether the server went away while that call ran.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import time
from importlib.metadata import version
from pathlib import Path

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


async def falkordb_run_id_when_back(
    *, budget_s: float = 180.0, interval_s: float = 5.0
) -> str | None:
    """Poll until FalkorDB answers again; None when it stays silent for ``budget_s``.

    180 s covers a crash plus a full reload: on 23 October 2026 the reload alone
    took 99-105 s. Only an answer can prove a restart, so a FalkorDB that is
    still silent at the end proves nothing either way.
    """
    deadline = time.monotonic() + budget_s
    while True:
        run_id = await falkordb_run_id()
        if run_id is not None or time.monotonic() >= deadline:
            return run_id
        await asyncio.sleep(interval_s)


@functools.cache
def graph_writer_build() -> str:
    """Identity of the code that turns a document into graph writes.

    A quarantine only holds for the build that earned it: a fix in graphiti-core,
    the FalkorDB compat layer or the ingest patches may be exactly what makes
    the document safe. klai-graphiti-compat is pinned at version 0.1.0 and never
    bumps (the stopword fix of 23 October did not), and the image carries no
    git sha, so the compat and patch sources are hashed instead.
    """
    import klai_graphiti_compat

    from knowledge_ingest import _patch_graphiti

    sources = sorted(Path(klai_graphiti_compat.__file__).parent.glob("*.py"))
    sources.append(Path(_patch_graphiti.__file__))
    digest = hashlib.sha256()
    for source in sources:
        digest.update(source.read_bytes())
    return f"graphiti-core {version('graphiti-core')} sources {digest.hexdigest()[:12]}"
