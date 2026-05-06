# SPEC-INGEST-RECONCILE-001 Fix 4 — synthetic fixture: this is the
# canonical post-Fix-1 shape. The ast-grep rule MUST NOT flag this:
# no asyncio.gather over crawl_page calls anywhere in the file.

import httpx


async def fetch_bulk(candidates, crawler_config):
    # GOOD: single bulk request to crawl4ai's /crawl endpoint —
    # server-side MemoryAdaptiveDispatcher handles concurrency and
    # we get per-URL outcomes back in the response body.
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(
            "https://crawl4ai.example/crawl",
            json={"urls": candidates, "crawler_config": crawler_config},
        )
        resp.raise_for_status()
        return resp.json()
