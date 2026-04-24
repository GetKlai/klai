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

# crawl4ai returns within ~15s for most pages; 30 is a safety ceiling.
_CRAWL4AI_TIMEOUT = 30.0

_TITLE_MAX_CHARS = 120

# First ATX-style H1 in the markdown — greedy match on the heading line only.
_H1_PATTERN = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)


def _crawl_config() -> dict[str, Any]:
    """Default crawler config for untrusted user-supplied URLs.

    Matches the "full pipeline" from knowledge-ingest's build_crawl_config
    (no selector) but without login-indicator selectors which are a
    connector-only feature.
    """
    return {
        "type": "CrawlerRunConfig",
        "params": {
            "cache_mode": "bypass",
            "word_count_threshold": 10,
            "wait_for": ("js:() => document.body.innerText.trim().split(/\\s+/).length > 50"),
            "remove_consent_popups": True,
            "remove_overlay_elements": True,
            "page_timeout": 25_000,  # crawl4ai internal page timeout, well under our 30s
            "excluded_tags": ["nav", "footer", "header", "aside", "script", "style"],
            "markdown_generator": {
                "type": "DefaultMarkdownGenerator",
                "params": {
                    "content_filter": {
                        "type": "PruningContentFilter",
                        "params": {"threshold": 0.45, "threshold_type": "dynamic"},
                    },
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
    if not page.get("success", True):
        # success=False from crawl4ai is an upstream failure signal.
        return ""

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
