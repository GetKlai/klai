"""Compute content fingerprint for a single URL via the crawl4ai HTTP API.

SPEC-CRAWL-004 REQ-9: used by the portal backend to (re)compute the canary
fingerprint when an admin manually changes the canary URL. Requires the
portal caller secret (same auth as /sync endpoints).

SPEC-SEC-HYGIENE-001 HY-31 (Branch B — rewire): the original
implementation lazy-imported ``app.adapters.webcrawler`` which was deleted
by SPEC-CRAWLER-004 Fase F (commit 2295bc0c). Every request silently 502'd
with a body that leaked
``Crawl failed: ModuleNotFoundError: No module named 'app.adapters.webcrawler'``
— an internal-topology disclosure (REQ-31.2 violation) and a feature
break: portal's ``_auto_fill_canary_fingerprint`` swallowed the
exception and saved connectors without canary protection.

The endpoint is now wired to the shared crawl4ai HTTP API at
``settings.crawl4ai_api_url`` — same client pattern as
``klai-knowledge-ingest/knowledge_ingest/crawl4ai_client.crawl_page``.
The 502 response detail is the constant string ``"Crawl failed"``;
all exception text goes only to ``logger.exception`` (REQ-31.2).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core.config import Settings
from app.routes.sync import _require_portal_call
from app.services.content_fingerprint import compute_content_fingerprint

# structlog directly (not app.core.logging.get_logger which returns stdlib
# Logger and rejects arbitrary kwargs). Mirrors the pattern used by
# knowledge_ingest.crawl4ai_client. portal-logging-py.md rule.
logger = structlog.get_logger(__name__)

router = APIRouter(tags=["fingerprint"])

# Single-page crawl deadline. Mirror knowledge_ingest.crawl4ai_client.crawl_page
# (90s) — long enough for slow renders, short enough to fail fast on hung pages.
_CRAWL_TIMEOUT = 90.0


class ComputeFingerprintRequest(BaseModel):
    url: str
    cookies: list[dict[str, Any]] | None = None


class ComputeFingerprintResponse(BaseModel):
    fingerprint: str
    word_count: int


def _build_crawl_payload(
    url: str, cookies: list[dict[str, Any]] | None
) -> dict[str, Any]:
    """Build the POST /crawl payload for a single-page bypass-cache fetch.

    Mirrors the shape of
    ``knowledge_ingest.crawl4ai_client.crawl_page``'s payload — same
    PruningContentFilter, same chrome-stripping ``excluded_tags`` —
    so canary fingerprints match the markdown the bulk crawler would
    produce for the same URL.
    """
    md_gen: dict[str, Any] = {
        "type": "DefaultMarkdownGenerator",
        "params": {
            "content_filter": {
                "type": "PruningContentFilter",
                "params": {"threshold": 0.45, "threshold_type": "dynamic"},
            },
            "options": {
                "type": "dict",
                "value": {"ignore_links": False, "body_width": 0},
            },
        },
    }
    config: dict[str, Any] = {
        "cache_mode": "bypass",
        "word_count_threshold": 0,
        "page_timeout": 30000,
        "remove_consent_popups": True,
        "remove_overlay_elements": True,
        "excluded_tags": ["nav", "footer", "header", "aside", "script", "style"],
        "markdown_generator": md_gen,
    }
    payload: dict[str, Any] = {
        "urls": [url],
        "crawler_config": {"type": "CrawlerRunConfig", "params": config},
    }
    if cookies:
        # Same on_page_context_created hook shape as
        # knowledge_ingest.crawl4ai_client._build_cookie_hooks.
        cookies_json = json.dumps(cookies)
        hook_code = (
            "async def hook(page, context, **kwargs):\n"
            f"    await context.add_cookies({cookies_json})\n"
            "    return page\n"
        )
        payload["hooks"] = {
            "code": {"on_page_context_created": hook_code},
            "timeout": 30,
        }
    return payload


def _extract_markdown(page: dict[str, Any]) -> str:
    """Return the best available markdown body from a single crawl4ai result.

    crawl4ai may surface markdown either as a string, a dict with
    ``fit_markdown`` / ``raw_markdown`` keys, or under a ``markdown_v2``
    key — handle all three shapes.

    pyright strict-mode noise on dict[str, Any].get() is suppressed at
    this boundary — JSON parsing is intentionally untyped.
    """
    # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType, reportUnknownArgumentType]
    md = page.get("markdown")
    if isinstance(md, dict):
        fit = str(md.get("fit_markdown") or "")
        raw = str(md.get("raw_markdown") or "")
    elif isinstance(md, str):
        fit = ""
        raw = md
    else:
        fit, raw = "", ""

    md_v2 = page.get("markdown_v2") or {}
    if isinstance(md_v2, dict):
        if not fit:
            fit = str(md_v2.get("fit_markdown") or "")
        if not raw:
            raw = str(md_v2.get("raw_markdown") or "")
    return fit or raw


async def _fetch_page_markdown(
    url: str,
    cookies: list[dict[str, Any]] | None,
    settings: Settings,
) -> str:
    """POST a single-URL crawl to crawl4ai and return its markdown body.

    This is the patch surface used by tests — the test suite monkey-patches
    this function so it can drive the route's three response paths
    (200/422/502) without touching httpx. Keep the signature stable.
    """
    payload = _build_crawl_payload(url, cookies)
    headers: dict[str, str] = {}
    if settings.crawl4ai_internal_key:
        headers["Authorization"] = f"Bearer {settings.crawl4ai_internal_key}"

    async with httpx.AsyncClient(timeout=_CRAWL_TIMEOUT) as client:
        resp = await client.post(
            f"{settings.crawl4ai_api_url}/crawl",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()

    raw_results: Any = data.get("results") or []
    results: list[dict[str, Any]]
    if isinstance(raw_results, dict):
        results = [raw_results]
    elif isinstance(raw_results, list):
        results = raw_results
    else:
        results = []
    if not results:
        return ""
    return _extract_markdown(results[0])


@router.post("/compute-fingerprint", response_model=ComputeFingerprintResponse)
async def compute_fingerprint(
    body: ComputeFingerprintRequest,
    request: Request,
) -> ComputeFingerprintResponse:
    """Crawl a single URL and return its content fingerprint.

    Used for canary page fingerprint computation when an admin manually
    sets or changes the canary URL. The portal backend calls this endpoint
    on connector save.

    Returns 200 with ``fingerprint`` + ``word_count`` on success.
    Returns 422 if the page has fewer than 20 words (cannot fingerprint).
    Returns 502 with the generic detail ``"Crawl failed"`` on any crawl
    failure — full exception text goes only to structlog
    (REQ-31.2: no internal-name leak in user-visible bodies).
    """
    _require_portal_call(request)

    settings = Settings()  # type: ignore[call-arg]

    try:
        markdown = await _fetch_page_markdown(body.url, body.cookies, settings)
    except HTTPException:
        raise
    except Exception:
        # REQ-31.2: never echo internal module names, hostnames, or
        # exception class names. logger.exception preserves the traceback
        # in structlog for VictoriaLogs queries.
        logger.exception("compute_fingerprint_crawl_failed", url=body.url)
        raise HTTPException(status_code=502, detail="Crawl failed") from None

    word_count = len(markdown.split()) if markdown else 0
    fingerprint = compute_content_fingerprint(markdown)
    if not fingerprint:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Page has fewer than 20 words ({word_count} found). "
                "Cannot compute a meaningful fingerprint."
            ),
        )

    return ComputeFingerprintResponse(fingerprint=fingerprint, word_count=word_count)
