"""URL source extractor (SPEC-KB-SOURCES-001 Module 2).

Fetches a user-supplied URL through crawl4ai and returns its title +
markdown. SSRF guarding happens before any outbound fetch via
``_url_validator.validate_url``. crawl4ai itself then performs the HTTP
get; see ``klai-knowledge-ingest/crawl4ai_client.py`` for the same
response shape and pipeline switching pattern (we use the untrusted
pipeline — no custom selector).
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

from app.core.config import settings
from app.services.source_extractors._url_validator import validate_url
from app.services.source_extractors.exceptions import SourceFetchError

logger = structlog.get_logger()

# Most pages come back in ~15s, but heavy JS apps are far slower and the old
# 30s ceiling cut them off mid-flight. Measured on support.ascendcloud.com
# (Oracle B2C / AngularJS) 2026-08-06: crawl4ai logged [COMPLETE] ✓ at
# 34-35s on six consecutive attempts while portal-api gave up at 30s, 4-6s
# short each time. The crawl succeeded every time; only the client quit
# early, and the user saw "Pagina onbereikbaar - probeer opnieuw".
#
# 60s keeps a comfortable margin above that measured worst case. It is a
# client-side ceiling only — a genuinely unreachable host still fails fast
# via connect error rather than burning the full budget.
_CRAWL4AI_TIMEOUT = 60.0

_TITLE_MAX_CHARS = 120

# First ATX-style H1 in the markdown — greedy match on the heading line only.
_H1_PATTERN = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)


def _crawl_config() -> dict[str, Any]:
    """Crawler config for a single user-supplied URL source.

    Deliberately LEAN — and deliberately different from the connector
    full-crawl pipeline (knowledge_ingest.build_crawl_config). Do NOT "sync"
    this back up to that config.

    Single-page URL-add is interactive (the user waits on this exact fetch),
    and every heavy crawl-pipeline knob broke real sites on 2026-05-22:
      - ``wait_for: >N words`` + default ``networkidle`` hung the full
        page_timeout (32s) on small/visual one-pagers → success=False → empty
        → 502 "Pagina onbereikbaar".
      - ``js_code`` chrome-stripping injection 500'd crawl4ai on some sites.
      - ``excluded_tags`` (nav/header/aside) + ``PruningContentFilter`` stripped
        one-pagers (whose content lives inside those semantic tags) to nothing.

    ``wait_until=domcontentloaded`` + a page_timeout BELOW _CRAWL4AI_TIMEOUT is
    what makes it fast and reliable. Verified: 200 + content in <1s on
    jantinedoornbos.nl, example.com, and a large Wikipedia page. Page chrome
    ends up in the markdown — acceptable for a single page; downstream chunking
    handles it.
    """
    return {
        "type": "CrawlerRunConfig",
        "params": {
            "cache_mode": "bypass",
            "wait_until": "domcontentloaded",
            # Keep below _CRAWL4AI_TIMEOUT so crawl4ai answers before the httpx
            # client gives up (otherwise portal-api 502s with "unreachable").
            "page_timeout": 20_000,
            "markdown_generator": {
                "type": "DefaultMarkdownGenerator",
                "params": {
                    "options": {"type": "dict", "value": {"ignore_links": False, "body_width": 0}},
                },
            },
        },
    }


def _extract_markdown_from_response(payload: dict[str, Any]) -> str:
    """Pull the best-available markdown field from a crawl4ai response.

    Mirrors knowledge-ingest's _extract_result: prefer fit_markdown, then
    raw_markdown, then the legacy markdown_v2 shape.
    """
    results = payload.get("results") or []
    if isinstance(results, dict):
        results = [results]
    if not results:
        return ""

    page = results[0]
    # @MX:NOTE: [AUTO] Do NOT bail on success=False here. crawl4ai reports
    # success=False when a wait_for predicate or page_timeout elapses, yet it
    # often still captured usable markdown from the last DOM state. Returning ""
    # on success=False threw that content away and produced a 502. Trust the
    # markdown if it's there; the empty-content check in extract_url is the real
    # gate. (Hard network failures never reach this branch — they raise
    # SourceFetchError earlier via httpx.)
    md = page.get("markdown", "")
    if isinstance(md, dict):
        fit = md.get("fit_markdown") or ""
        raw = md.get("raw_markdown") or ""
    else:
        fit = ""
        raw = md or ""

    md_v2 = page.get("markdown_v2", {})
    if not fit:
        fit = md_v2.get("fit_markdown") or ""
    if not raw:
        raw = md_v2.get("raw_markdown") or ""

    return fit or raw


def _derive_title(markdown: str, hostname: str | None) -> str:
    """Title from: first H1 > first non-empty line (<=120 chars) > hostname."""
    if markdown:
        h1 = _H1_PATTERN.search(markdown)
        if h1:
            return h1.group(1).strip()[:_TITLE_MAX_CHARS]

        for line in markdown.splitlines():
            stripped = line.strip().lstrip("#").strip()
            if stripped:
                return stripped[:_TITLE_MAX_CHARS]

    return hostname or "Untitled page"


async def extract_url(url: str) -> tuple[str, str, str]:
    """Fetch ``url`` via crawl4ai and return (title, markdown, source_ref).

    Raises:
        InvalidUrlError: URL malformed or disallowed scheme.
        SSRFBlockedError: URL resolves to a blocked IP range / docker host.
        SourceFetchError: crawl4ai unreachable, non-200, or empty content.
    """
    canonical = await validate_url(url)
    hostname = urlparse(canonical).hostname

    payload = {
        "urls": [canonical],
        "crawler_config": _crawl_config(),
    }

    try:
        async with httpx.AsyncClient(timeout=_CRAWL4AI_TIMEOUT) as client:
            resp = await client.post(f"{settings.crawl4ai_api_url}/crawl", json=payload)
    except httpx.RequestError as exc:
        logger.warning("crawl4ai_request_failed", hostname=hostname, error=str(exc))
        raise SourceFetchError(f"crawl4ai unreachable: {exc}") from exc

    if resp.status_code != 200:
        logger.warning("crawl4ai_non_200", hostname=hostname, status=resp.status_code)
        raise SourceFetchError(f"crawl4ai returned {resp.status_code}")

    try:
        data = resp.json()
    except ValueError as exc:
        raise SourceFetchError(f"crawl4ai returned non-JSON body: {exc}") from exc

    markdown = _extract_markdown_from_response(data)
    if not markdown.strip():
        logger.warning("crawl4ai_empty_content", hostname=hostname)
        raise SourceFetchError("crawl4ai returned empty content")

    title = _derive_title(markdown, hostname)
    return title, markdown, canonical
