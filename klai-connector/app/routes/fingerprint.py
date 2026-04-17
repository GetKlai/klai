"""Compute content fingerprint for a single URL.

SPEC-CRAWL-004 REQ-9: used by the portal backend to (re)compute the canary
fingerprint when an admin manually changes the canary URL. Requires the portal
caller secret (same auth as /sync endpoints).

The endpoint reuses ``_post_crawl_sync()`` for cookie injection and crawl4ai
plumbing, and ``compute_content_fingerprint()`` for the SimHash computation.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core.logging import get_logger
from app.routes.sync import _require_portal_call
from app.services.content_fingerprint import compute_content_fingerprint

logger = get_logger(__name__)

router = APIRouter(tags=["fingerprint"])


class ComputeFingerprintRequest(BaseModel):
    url: str
    cookies: list[dict[str, Any]] | None = None


class ComputeFingerprintResponse(BaseModel):
    fingerprint: str
    word_count: int


@router.post("/compute-fingerprint", response_model=ComputeFingerprintResponse)
async def compute_fingerprint(
    body: ComputeFingerprintRequest,
    request: Request,
) -> ComputeFingerprintResponse:
    """Crawl a single URL and return its content fingerprint.

    Used for canary page fingerprint computation when an admin manually sets
    or changes the canary URL. The portal backend calls this endpoint on
    connector save.

    Returns 200 with fingerprint + word_count on success.
    Returns 422 if the page has fewer than 20 words (fingerprint empty).
    Returns 502 if the crawl fails.
    """
    _require_portal_call(request)

    # Import adapter lazily — it needs Settings which is configured at app startup.
    from app.adapters.webcrawler import WebCrawlerAdapter, _extract_markdown
    from app.core.config import Settings

    settings = Settings()
    adapter = WebCrawlerAdapter(settings)

    try:
        # Minimal crawl params — no content filtering, bypass cache.
        crawl_params: dict[str, Any] = {
            "cache_mode": "bypass",
            "word_count_threshold": 0,
            "page_timeout": 30000,
        }
        result = await adapter._post_crawl_sync(
            urls=[body.url],
            crawl_params=crawl_params,
            cookies=body.cookies,
            text_mode=False,
            timeout=30.0,
        )

        pages = result.get("results", [])
        markdown = _extract_markdown(pages[0]) if pages else ""
        word_count = len(markdown.split()) if markdown else 0

        fingerprint = compute_content_fingerprint(markdown)
        if not fingerprint:
            raise HTTPException(
                status_code=422,
                detail=f"Page has fewer than 20 words ({word_count} found). Cannot compute a meaningful fingerprint.",
            )

        return ComputeFingerprintResponse(
            fingerprint=fingerprint,
            word_count=word_count,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(
            "compute_fingerprint_failed",
            url=body.url,
            error=str(exc),
        )
        raise HTTPException(
            status_code=502,
            detail=f"Crawl failed: {exc}",
        ) from exc
    finally:
        await adapter.aclose()
