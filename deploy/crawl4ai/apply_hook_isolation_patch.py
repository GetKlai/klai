"""Give a hook-carrying crawl request its own browser instead of a shared one.

Why this exists: crawl4ai's declarative hooks are attached by mutating the
crawler that serves the request (``api.py::_attach_declarative_hooks`` calls
``crawler.crawler_strategy.set_hook``), but ``crawler_pool.get_crawler``
hands out a SHARED crawler — the permanent one for the default browser
config, which is what every Klai crawl uses. ``release_crawler`` only
decrements ``active_requests``; the hook stays attached. So the cookies one
request injects are re-injected into every later request served by the same
crawler, and a concurrent request can have its hooks overwritten by another.

Measured on this image before the patch, four sequential requests to a
cookie-echoing URL:

    1. anonymous, before      -> no cookie
    2. with add_cookies hook  -> cookie present   (correct)
    3. anonymous, after       -> cookie present   (wrong)
    4. anonymous, after       -> cookie present   (wrong)

Klai runs one crawl4ai for all tenants, so step 3 means one tenant's session
cookie riding along on another tenant's crawl of the same site. Cookies are
domain-scoped by the browser, so the reach is "same domain, any tenant" — not
arbitrary exfiltration, but a cross-tenant leak all the same.

The patch keeps the pool for everything else and takes a crawler out of it
only when hooks are involved: hooks make a crawler request-specific, and a
request-specific crawler cannot be shared. Cost is one browser start per
authenticated crawl request (our client batches up to 100 URLs per request,
so this is per batch, not per page), and it is closed again on release.

Drop this patch when upstream scopes hooks to the request. Fails the build
loudly when an anchor is missing or ambiguous, so a crawl4ai base-image bump
can never silently ship an unpatched image.
"""

import pathlib
import sys

APP = pathlib.Path("/app")
API = APP / "api.py"
POOL = APP / "crawler_pool.py"

for path in (API, POOL):
    if not path.is_file():
        sys.exit(f"REFUSING TO BUILD: {path} not found.")


def patch(path, anchor, replacement, expected):
    text = path.read_text()
    count = text.count(anchor)
    if count != expected:
        sys.exit(
            f"REFUSING TO BUILD: anchor found {count}x in {path} (expected "
            f"{expected}). The crawl4ai base image changed; re-verify the "
            "patch against the new source.\n"
            f"--- anchor ---\n{anchor}"
        )
    path.write_text(text.replace(anchor, replacement))


# 1. The pool grows a way to hand out a crawler that is NOT shared, with the
#    same memory guard it applies before starting any other browser.
NEW_FN = '''async def get_dedicated_crawler(cfg: BrowserConfig) -> AsyncWebCrawler:
    """Start a crawler for one request only, outside the pool.

    Callers that attach hooks need this: a hook is set on the crawler and
    outlives the request, so a pooled crawler would carry it into whatever
    request comes next. Released via release_crawler(), which closes it.
    """
    mem_pct = get_container_memory_percent()
    if mem_pct >= MEM_LIMIT:
        logger.error(f"\\U0001f4a5 Memory pressure: {mem_pct:.1f}% >= {MEM_LIMIT}%")
        raise MemoryError(f"Memory at {mem_pct:.1f}%, refusing new browser")
    logger.info(f"\\U0001f512 Creating dedicated browser for hooked request (mem={mem_pct:.1f}%)")
    crawler = AsyncWebCrawler(config=cfg, thread_safe=False)
    await crawler.start()
    crawler.klai_dedicated = True
    return crawler


'''
patch(POOL, "async def release_crawler(crawler: AsyncWebCrawler):",
      NEW_FN + "async def release_crawler(crawler: AsyncWebCrawler):", 1)

# 2. Releasing a dedicated crawler closes it; it is in no pool, so the
#    janitor will never come for it.
patch(
    POOL,
    "    async with LOCK:\n"
    "        if hasattr(crawler, 'active_requests'):\n"
    "            crawler.active_requests = max(0, crawler.active_requests - 1)",
    "    if getattr(crawler, 'klai_dedicated', False):\n"
    "        with suppress(Exception):\n"
    "            await crawler.close()\n"
    "        return\n"
    "    async with LOCK:\n"
    "        if hasattr(crawler, 'active_requests'):\n"
    "            crawler.active_requests = max(0, crawler.active_requests - 1)",
    1,
)

# 3. Both request handlers (streaming and not) route through it. Identical
#    two lines in each, so one anchor covers both.
patch(
    API,
    "        from crawler_pool import get_crawler, release_crawler\n"
    "        crawler = await get_crawler(browser_config)",
    "        from crawler_pool import (\n"
    "            get_crawler,\n"
    "            get_dedicated_crawler,\n"
    "            release_crawler,\n"
    "        )\n"
    "        # Hooks are attached by mutating the crawler and are never\n"
    "        # detached, so a hooked request must not get a shared one.\n"
    "        crawler = (\n"
    "            await get_dedicated_crawler(browser_config)\n"
    "            if hooks_config\n"
    "            else await get_crawler(browser_config)\n"
    "        )",
    2,
)

# Read back and verify — do not trust the write.
pool_text = POOL.read_text()
api_text = API.read_text()
if "async def get_dedicated_crawler" not in pool_text:
    sys.exit("REFUSING TO BUILD: get_dedicated_crawler missing after write.")
if "klai_dedicated" not in pool_text:
    sys.exit("REFUSING TO BUILD: release_crawler patch missing after write.")
if api_text.count("get_dedicated_crawler(browser_config)") != 2:
    sys.exit("REFUSING TO BUILD: api.py call sites not patched.")
if "suppress" not in pool_text.split("async def")[0]:
    sys.exit("REFUSING TO BUILD: 'suppress' is not imported in crawler_pool.py.")
print(f"patched OK: {API}, {POOL}")
