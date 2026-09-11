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
crawl4ai's own rejection reason is logged separately, as two bounded
identifiers pulled out of the body -- never as upstream prose. See
``_rejection_diagnosis``.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core.config import Settings
from app.routes.sync import _require_portal_call  # pyright: ignore[reportPrivateUsage]
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
        # Declarative add_cookies hook — crawl4ai's currently-supported path
        # for authenticated crawls via the Docker API. Raw BrowserConfig.cookies
        # (used here previously) is rejected with HTTP 400 by crawl4ai >= 0.9's
        # untrusted-config boundary (CVE-2026-57572 hardening); add_cookies is
        # the server-validated declarative replacement, still running at
        # on_page_context_created (pre-navigation), same timing as before.
        # Mirrors knowledge_ingest.crawl4ai_client._build_cookie_hooks.
        # The field is ``hooks``, not ``hooks_config``: the latter is not on
        # crawl4ai's request model and pydantic drops an unknown field without
        # a word, which is how this answered 200 while sending no cookies.
        payload["hooks"] = {
            "hooks": [{"action": "add_cookies", "params": {"cookies": cookies}}]
        }
    return payload


def _extract_markdown(page: dict[str, Any]) -> str:
    """Return the best available markdown body from a single crawl4ai result.

    crawl4ai may surface markdown either as a string, a dict with
    ``fit_markdown`` / ``raw_markdown`` keys, or under a ``markdown_v2``
    key — handle all three shapes.
    """
    md_raw: Any = page.get("markdown")
    fit: str = ""
    raw: str = ""
    if isinstance(md_raw, dict):
        # pyright: ignore[reportUnknownVariableType] — dict[str, Any]
        # value types are intentionally unknown at the JSON boundary.
        md_dict: dict[str, Any] = md_raw  # pyright: ignore[reportUnknownVariableType]
        fit = str(md_dict.get("fit_markdown") or "")
        raw = str(md_dict.get("raw_markdown") or "")
    elif isinstance(md_raw, str):
        raw = md_raw

    md_v2_raw: Any = page.get("markdown_v2") or {}
    if isinstance(md_v2_raw, dict):
        md_v2: dict[str, Any] = md_v2_raw  # pyright: ignore[reportUnknownVariableType]
        if not fit:
            fit = str(md_v2.get("fit_markdown") or "")
        if not raw:
            raw = str(md_v2.get("raw_markdown") or "")
    return fit or raw


# What we actually need from a crawl4ai error, and nothing else.
#
# Both body shapes are measured against 0.9.2 (2026-09-10):
#   {"detail": "Rejected config: field 'js_code' is not permitted ..."}
#   {"error": "Internal server error", "correlation_id": "188834187d7d"}
#
# Earlier versions of this code logged the whole body, then an allowlist of
# three fields. Both are free-form upstream strings, and scrubbing secrets out
# of a free-form string by substring replacement cannot be both complete and
# free of false positives: the same value is escaped differently depending on
# the serializer, and nested JSON is escaped twice. So we do not log upstream
# prose at all. We pull two BOUNDED identifiers out of it:
#
#   rejected_field  -- which config field the boundary refused
#   correlation_id  -- the handle to find the failure in crawl4ai's own logs
#
# Anything that does not match these shapes is dropped. A credential cannot
# survive a character class that admits neither quotes nor spaces.
_REJECTED_FIELD_RE = re.compile(r"field '([A-Za-z_][A-Za-z0-9_]{0,63})' is not permitted")
_CORRELATION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _loggable_url(url: str) -> str:
    """Scheme, host and path only.

    A canary URL is operator-supplied and may carry a session token in its
    userinfo or query string — ``https://user:pw@wiki/?token=...``. The URL
    validator upstream checks scheme, host and IP but does not forbid either,
    and we log this on every rejection. Keep the part an operator needs to
    recognise the page; drop the parts that can carry a credential.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable>"
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _rejection_diagnosis(resp: httpx.Response) -> dict[str, str]:
    """Bounded identifiers from a crawl4ai error body.

    Returns an empty dict when the body is not JSON or carries neither shape —
    the caller then logs url and status_code only, as it did before any of
    this existed.
    """
    try:
        payload = resp.json()
    except ValueError:
        return {}
    if not isinstance(payload, dict):
        return {}

    diagnosis: dict[str, str] = {}
    detail = payload.get("detail")
    if isinstance(detail, str):
        match = _REJECTED_FIELD_RE.search(detail)
        if match:
            diagnosis["rejected_field"] = match.group(1)
    correlation_id = payload.get("correlation_id")
    if isinstance(correlation_id, str) and _CORRELATION_ID_RE.match(correlation_id):
        diagnosis["correlation_id"] = correlation_id
    return diagnosis


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
        if resp.status_code >= 400:
            # crawl4ai puts the reason for a rejection in the response body —
            # e.g. "field 'cookies' is not permitted on BrowserConfig from an
            # untrusted request" from its CVE-2026-57572 config boundary. It
            # exists nowhere else, so reading it here is the only diagnosis
            # available. Sanitized (SPEC-SEC-INTERNAL-001 REQ-4); the route's
            # 502 detail stays generic per REQ-31.2. raise_for_status() below
            # keeps the exception contract exactly as it was.
            logger.error(
                "compute_fingerprint_crawl4ai_rejected",
                url=_loggable_url(url),
                status_code=resp.status_code,
                **_rejection_diagnosis(resp),
            )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()

    raw_results: Any = data.get("results") or []
    results: list[dict[str, Any]]
    if isinstance(raw_results, dict):
        results = [raw_results]
    elif isinstance(raw_results, list):
        results = raw_results  # pyright: ignore[reportUnknownVariableType]
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
        logger.exception("compute_fingerprint_crawl_failed", url=_loggable_url(body.url))
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
