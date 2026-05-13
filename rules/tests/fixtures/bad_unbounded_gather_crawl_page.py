# SPEC-INGEST-RECONCILE-001 Fix 4 — synthetic fixture: this is what the
# old supplement loop in crawl4ai_client.crawl_site looked like before
# Fix 1. The ast-grep rule MUST flag this pattern. Three observable
# defects (no Semaphore, no per-result outcome, exact-string dedup) all
# follow once unbounded gather over crawl_page is allowed back in.

import asyncio


async def crawl_page(url, selector=None):  # local stub for the fixture
    return None


async def supplement_pages(supplement_urls, selector=None):
    # BAD: unbounded asyncio.gather over crawl_page — exactly the pattern
    # that produced Bug A on help.voys.nl (167 parallel calls, ~0 ingested).
    return await asyncio.gather(
        *[crawl_page(u, selector=selector) for u in supplement_urls],
        return_exceptions=True,
    )
