"""
Crawl route:
  POST /ingest/v1/crawl         — fetch a URL via Crawl4AI REST API, convert to markdown, and ingest
  POST /ingest/v1/crawl/preview — fetch a URL with PruningContentFilter and return fit_markdown

All crawling goes through the shared Crawl4AI Docker container via crawl4ai_client.
Pipeline selection (SPEC-CRAWL-001 / R-1) is handled by crawl4ai_client.build_crawl_config().

SPEC-TI-003-FOLLOWUP-001 AC-1: handlers that touch knowledge.* open a
tenant_scoped_connection on the body's org_id and pass conn into pg_store /
link_graph / domain_selectors / ingest_document.
"""

import contextlib
import hashlib
import re
import time
from urllib.parse import urlparse

import asyncpg
import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from knowledge_ingest import pg_store
from knowledge_ingest.crawl4ai_client import crawl_dom_summary, crawl_page
from knowledge_ingest.db import tenant_scoped_connection
from knowledge_ingest.domain_selectors import (
    extract_domain,
    get_domain_selector,
    upsert_domain_selector,
)
from knowledge_ingest.fingerprint import compute_content_fingerprint
from knowledge_ingest.identity import (
    assert_caller_identity,
    assert_caller_identity_tenant_only,
)
from knowledge_ingest.models import CrawlRequest, CrawlResponse, IngestRequest
from knowledge_ingest.routes.ingest import ingest_document
from knowledge_ingest.selector_ai import (
    detect_login_indicator_via_llm,
    detect_selector_via_llm,
)
from knowledge_ingest.utils.auth_wall_classifier import classify_auth_wall
from knowledge_ingest.utils.link_density import LINK_DENSITY_THRESHOLD, link_density
from knowledge_ingest.utils.url_validator import validate_url, validate_url_pinned

logger = structlog.get_logger()
router = APIRouter()


_LINK_RE = re.compile(r"\[([^\]]*)\]\([^\)]+\)")


def _detect_nav_contamination(text: str) -> list[str]:
    """Detect navigation/menu contamination in fit_markdown.

    Two signals that BOTH must fire (conservative — false positives are worse than misses):
    - link_density:  >35% of all non-empty lines are 'link-only' (link(s) with ≤2 prose words)
    - top_heavy:     >45% of the first 25 lines are link-only

    Returns ["navigation_detected"] when contamination is likely, [] otherwise.
    """
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) < 15 or len(text.split()) < 50:
        return []

    def _is_nav_line(line: str) -> bool:
        stripped = line.strip("*-># \t|")
        links = _LINK_RE.findall(stripped)
        if not links:
            return False
        remaining = _LINK_RE.sub("", stripped).strip(" |,-•·")
        return len(remaining.split()) <= 2

    nav_count = sum(1 for ln in lines if _is_nav_line(ln))
    nav_ratio = nav_count / len(lines)

    first_n = lines[: min(25, len(lines))]
    first_nav = sum(1 for ln in first_n if _is_nav_line(ln))
    first_nav_ratio = first_nav / len(first_n)

    if nav_ratio > 0.35 and first_nav_ratio > 0.45:
        return ["navigation_detected"]
    return []


class CrawlPreviewRequest(BaseModel):
    url: str
    content_selector: str | None = None
    org_id: str = ""  # optional for backwards compatibility; required for domain selector lookup
    try_ai: bool = False  # explicit opt-in for AI selector detection
    cookies: list[dict] | None = None  # browser cookies for authenticated crawling


class AuthGuardSuggestion(BaseModel):
    """Auto-detected auth guard config for SPEC-CRAWL-004.

    Populated when a preview crawl succeeds with cookies. The preview URL
    becomes the canary page; the login indicator is AI-detected from the DOM.
    """

    canary_url: str | None = None
    canary_fingerprint: str | None = None  # 16-char hex SimHash
    login_indicator_selector: str | None = None
    login_indicator_description: str | None = None  # human-readable hint


class CrawlPreviewResponse(BaseModel):
    url: str
    fit_markdown: str
    word_count: int
    warnings: list[str] = []
    content_selector: str | None = None
    selector_source: str | None = None  # "user" | "ai" | None
    auth_guard: AuthGuardSuggestion | None = None  # SPEC-CRAWL-004
    # SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-3 — five-way classification
    # used by the wizard to gate step-5 → step-6 advance.
    # Default is "unknown" (fail-closed): absence of classification must
    # never be treated as success.  A concrete value is always written by
    # _classify_preview_outcome for the happy path; "unknown" surfaces only
    # when the upstream crawl service errors before classification runs.
    classification: str = "unknown"
    classification_reason: str | None = None


# SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-3 — distinguish "selector matched
# nothing" (raw HTML present, content_selector wrong) from "page is JS-only"
# (raw HTML minimal, no JS execution path). 5KB is the empirical anchor:
# server-rendered pages with any content exceed 5KB; SPA shells with a single
# ``<div id="root">`` stay under.
_RAW_HTML_SPA_THRESHOLD_BYTES = 5000


class AuthProbeRequest(BaseModel):
    """SPEC-CONNECTOR-INPUT-VALIDATION-001 / REQ-2.

    Same payload shape as ``CrawlPreviewRequest`` minus the selector
    fields — the auth probe is run against the seed URL only, no
    ``content_selector`` and no AI fallback.
    """

    url: str
    org_id: str = ""
    cookies: list[dict] | None = None


class AuthProbeResponse(BaseModel):
    """SPEC-CONNECTOR-INPUT-VALIDATION-001 / REQ-2.

    Five-way classification of the seed-page fetch outcome.

    Possible ``classification`` values:

    - ``auth_ok``
    - ``auth_failed_no_cookies``
    - ``auth_failed_still_walled``
    - ``auth_failed_credentials_invalid``
    - ``auth_failed_unreachable``
    """

    classification: str
    match_reasons: list[str] = []
    word_count: int
    auth_guard: AuthGuardSuggestion | None = None


# Minimum word count for a crawl result to be considered usable content.
_MIN_WORD_COUNT = 100


async def _run_crawl(
    url: str,
    selector: str | None,
    cookies: list[dict] | None = None,
) -> tuple[str, int, str]:
    """Crawl a single page via the Crawl4AI REST API.

    Returns (fit_markdown, word_count, raw_html).
    """
    result = await crawl_page(url, selector, cookies=cookies)
    fit_md = result.fit_markdown or result.raw_markdown
    return fit_md, result.word_count, result.html


def _classify_preview_outcome(
    *,
    fit_markdown: str,
    raw_html: str,
    word_count: int,
    response_status_code: int | None,
    redirect_target_url: str | None,
    set_cookie_header: str | None,
) -> tuple[str, str | None]:
    """REQ-3 — return ``(classification, classification_reason)`` for the preview.

    Order of checks matters:
      1. auth-wall heuristic wins over everything else (we never want to
         hand a "success" verdict on a 401 page just because it has many
         words).
      2. If word_count is sufficient: link-density gate.
      3. If word_count is thin: distinguish empty-selector vs SPA via raw
         HTML size.
    """
    auth_wall = classify_auth_wall(
        response_status_code=response_status_code,
        redirect_target_url=redirect_target_url,
        set_cookie_header=set_cookie_header,
        word_count=word_count,
        fit_markdown=fit_markdown,
        raw_html=raw_html,
    )
    if auth_wall.is_walled:
        return ("auth_wall_detected", "Page requires authentication.")

    if word_count >= _MIN_WORD_COUNT:
        density = link_density(fit_markdown)
        if density > LINK_DENSITY_THRESHOLD:
            pct = int(density * 100)
            return (
                "selector_required",
                f"{pct}% of the text is links. Configure a Content Selector.",
            )
        return ("success", None)

    if raw_html and len(raw_html) > _RAW_HTML_SPA_THRESHOLD_BYTES:
        return (
            "selector_returns_empty",
            "Selector matched no content. Try a different selector or click 'Let AI find'.",
        )

    return (
        "requires_javascript",
        "Page renders via JavaScript. Configure a wait_for condition or "
        "selector for the post-render DOM.",
    )


@contextlib.asynccontextmanager
async def _maybe_tenant_conn(org_id: str):
    """Yield a GUC-pinned conn when org_id is present, else None.

    preview_crawl is the only handler that may legitimately run without
    org_id (anonymous preview); all other paths must have org_id and use
    tenant_scoped_connection directly.
    """
    if not org_id:
        yield None
        return
    async with tenant_scoped_connection(org_id) as conn:
        yield conn


@router.post("/ingest/v1/crawl/preview", response_model=CrawlPreviewResponse)
async def preview_crawl(body: CrawlPreviewRequest, request: Request) -> CrawlPreviewResponse:
    """Fetch a URL with PruningContentFilter and return the filtered markdown preview.
    SPEC-TI-003 AC-6: identity assertion when org_id is provided.
    """
    logger.info("crawl_preview_started", url=body.url)
    # SPEC-TI-003 AC-6: assert identity when caller provides org_id
    if body.org_id:
        await assert_caller_identity(request, claimed_org_id=body.org_id)
    # SPEC-SEC-SSRF-001 / REQ-1.1 / AC-1 / AC-6: SSRF validation MUST
    # run before any DNS lookup triggered by downstream crawl4ai /
    # get_domain_selector / crawl_dom_summary logic. It runs outside
    # the try/except block below because REQ-1.2 forbids the broad
    # ``except Exception`` from swallowing the 400-class rejection
    # into the historical 200-with-empty-body shape.
    try:
        await validate_url_pinned(body.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        # Resolve effective selector: user-provided wins, then stored domain selector
        # SPEC-CRAWL-001 / R-2, R-6
        effective_selector = body.content_selector
        selector_source = "user" if effective_selector else None

        async with _maybe_tenant_conn(body.org_id) as conn:
            if not effective_selector and conn is not None:
                stored = await get_domain_selector(conn, extract_domain(body.url), body.org_id)
                if stored:
                    effective_selector, selector_source = stored

            # Initial crawl (HTTP, no DB). Capture full CrawlResult so we
            # can run the REQ-3 classifier with HTTP-level metadata
            # (status_code, redirect URL, response headers).
            initial_result = await crawl_page(body.url, effective_selector, cookies=body.cookies)
            fit_md = initial_result.fit_markdown or initial_result.raw_markdown
            word_count = initial_result.word_count
            raw_html = initial_result.html
            last_result = initial_result
            warnings: list[str] = _detect_nav_contamination(fit_md)

            # AI-assisted selector detection — only when explicitly requested via try_ai flag
            # SPEC-CRAWL-001 / R-4
            if (
                body.try_ai
                and word_count < _MIN_WORD_COUNT
                and not effective_selector
                and conn is not None
            ):
                dom_summary = await crawl_dom_summary(body.url)
                if dom_summary:
                    ai_selector = await detect_selector_via_llm(dom_summary)
                    if ai_selector:
                        try:
                            recrawl_result = await crawl_page(
                                body.url, ai_selector, cookies=body.cookies
                            )
                            recrawl_md = recrawl_result.fit_markdown or recrawl_result.raw_markdown
                            recrawl_wc = recrawl_result.word_count
                            if recrawl_wc >= _MIN_WORD_COUNT:
                                await upsert_domain_selector(
                                    conn,
                                    extract_domain(body.url),
                                    body.org_id,
                                    ai_selector,
                                    "ai",
                                )
                                fit_md = recrawl_md
                                word_count = recrawl_wc
                                raw_html = recrawl_result.html
                                last_result = recrawl_result
                                warnings = _detect_nav_contamination(fit_md)
                                effective_selector = ai_selector
                                selector_source = "ai"
                            else:
                                # Re-crawl also thin — return original, do not store
                                if "low_word_count" not in warnings:
                                    warnings.append("low_word_count")
                        except Exception as exc:
                            logger.warning(
                                "crawl_ai_recrawl_failed",
                                url=body.url,
                                ai_selector=ai_selector,
                                error=str(exc),
                            )
                            if "low_word_count" not in warnings:
                                warnings.append("low_word_count")

            # Persist selector after a successful crawl (>= 100 words), if we have one
            # SPEC-CRAWL-001 / R-3
            if (
                word_count >= _MIN_WORD_COUNT
                and effective_selector
                and conn is not None
                and selector_source
            ):
                await upsert_domain_selector(
                    conn,
                    extract_domain(body.url),
                    body.org_id,
                    effective_selector,
                    selector_source,
                )

        # SPEC-CRAWL-004: auto-detect auth guard when cookies are present and
        # the crawl succeeded with real content. The preview URL becomes the
        # canary page; the login indicator is AI-detected from the DOM.
        auth_guard: AuthGuardSuggestion | None = None
        if body.cookies and word_count >= _MIN_WORD_COUNT:
            canary_fp = compute_content_fingerprint(fit_md)
            if canary_fp:
                auth_guard = AuthGuardSuggestion(
                    canary_url=body.url,
                    canary_fingerprint=canary_fp,
                )
                # Try AI detection of login indicator (best-effort, non-blocking)
                try:
                    dom_summary = await crawl_dom_summary(body.url)
                    if dom_summary:
                        indicator = await detect_login_indicator_via_llm(dom_summary)
                        if indicator:
                            auth_guard.login_indicator_selector = indicator
                            auth_guard.login_indicator_description = f"Detected: {indicator}"
                except Exception:
                    logger.debug(
                        "auth_guard_login_indicator_detection_skipped",
                        url=body.url,
                    )

        # SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-3 — classification
        metadata = last_result.metadata or {}
        response_headers = last_result.response_headers or {}
        set_cookie_header = response_headers.get("set-cookie") or response_headers.get("Set-Cookie")
        classification, classification_reason = _classify_preview_outcome(
            fit_markdown=fit_md,
            raw_html=raw_html,
            word_count=word_count,
            response_status_code=metadata.get("status_code"),
            redirect_target_url=metadata.get("redirect_url") or metadata.get("location"),
            set_cookie_header=set_cookie_header,
        )

        # SPEC-CONNECTOR-INPUT-VALIDATION-001 — completion log for production
        # debugging. Without this, a 200 with classification='unknown' or
        # 'requires_javascript' is invisible in logs (only the 'started' event
        # fires today). VictoriaLogs query: ``event:crawl_preview_completed
        # AND classification:unknown``.
        logger.info(
            "crawl_preview_completed",
            url=body.url,
            classification=classification,
            word_count=word_count,
            status_code=metadata.get("status_code"),
            selector_source=selector_source,
            warnings_count=len(warnings),
        )

        return CrawlPreviewResponse(
            url=body.url,
            fit_markdown=fit_md,
            word_count=word_count,
            warnings=warnings,
            content_selector=effective_selector,
            selector_source=selector_source,
            auth_guard=auth_guard,
            classification=classification,
            classification_reason=classification_reason,
        )
    except Exception as exc:
        logger.warning("crawl_preview_failed", url=body.url, error=str(exc))
        return CrawlPreviewResponse(
            url=body.url,
            fit_markdown="",
            word_count=0,
            classification="unknown",
            classification_reason="Could not reach crawl service. Try again.",
        )


@router.post("/ingest/v1/crawl/auth-probe", response_model=AuthProbeResponse)
async def auth_probe(body: AuthProbeRequest, request: Request) -> AuthProbeResponse:
    """SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-2 — classify a single seed
    fetch as ``auth_ok`` or one of four failure flavours.

    Mirrors the SSRF and identity guards on ``/ingest/v1/crawl/preview``:

    - SSRF validation runs BEFORE any DNS lookup downstream
      (SPEC-SEC-SSRF-001 REQ-1.1 / AC-1).
    - When ``org_id`` is supplied, ``assert_caller_identity_tenant_only``
      enforces tenant scoping (per the bug-pattern memory: this is an
      internal-secret + tenant-only route, no end-user context).
    """
    logger.info("auth_probe_started", url=body.url)

    if body.org_id:
        await assert_caller_identity_tenant_only(request, claimed_org_id=body.org_id)

    try:
        await validate_url_pinned(body.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        result = await crawl_page(body.url, selector=None, cookies=body.cookies)
    except Exception as exc:
        logger.warning("auth_probe_crawl_failed", url=body.url, error=str(exc))
        return AuthProbeResponse(
            classification="auth_failed_unreachable",
            match_reasons=[],
            word_count=0,
            auth_guard=None,
        )

    metadata = result.metadata or {}
    response_headers = result.response_headers or {}
    set_cookie_header = response_headers.get("set-cookie") or response_headers.get("Set-Cookie")
    redirect_target_url = metadata.get("redirect_url") or metadata.get("location")

    classification = classify_auth_wall(
        response_status_code=metadata.get("status_code"),
        redirect_target_url=redirect_target_url,
        set_cookie_header=set_cookie_header,
        word_count=result.word_count,
        fit_markdown=result.fit_markdown or "",
        raw_html=result.html or "",
    )

    label = _label_for_auth_probe(
        is_walled=classification.is_walled,
        match_reasons=classification.match_reasons,
        word_count=result.word_count,
        crawl_success=result.success,
        cookies_provided=bool(body.cookies),
    )

    auth_guard: AuthGuardSuggestion | None = None
    if label == "auth_ok" and body.cookies:
        canary_fp = compute_content_fingerprint(result.fit_markdown or "")
        if canary_fp:
            auth_guard = AuthGuardSuggestion(
                canary_url=body.url,
                canary_fingerprint=canary_fp,
            )
            try:
                dom_summary = await crawl_dom_summary(body.url)
                if dom_summary:
                    indicator = await detect_login_indicator_via_llm(dom_summary)
                    if indicator:
                        auth_guard.login_indicator_selector = indicator
                        auth_guard.login_indicator_description = f"Detected: {indicator}"
            except Exception:
                logger.debug("auth_probe_indicator_detection_skipped", url=body.url)

    # SPEC-CONNECTOR-INPUT-VALIDATION-001 — completion log mirrors the one
    # in preview_crawl above; without it, debugging "why did wizard show
    # auth_failed_unreachable" requires guessing.
    logger.info(
        "auth_probe_completed",
        url=body.url,
        classification=label,
        word_count=result.word_count,
        status_code=metadata.get("status_code"),
        match_reasons=list(classification.match_reasons),
        cookies_provided=bool(body.cookies),
    )

    return AuthProbeResponse(
        classification=label,
        match_reasons=list(classification.match_reasons),
        word_count=result.word_count,
        auth_guard=auth_guard,
    )


def _label_for_auth_probe(
    *,
    is_walled: bool,
    match_reasons: tuple[str, ...],
    word_count: int,
    crawl_success: bool,
    cookies_provided: bool,
) -> str:
    """Map classifier output + request context to one of five outcome labels.

    SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-2 outcome table.
    """
    if "http_unauthenticated" in match_reasons:
        return "auth_failed_credentials_invalid"
    if is_walled:
        return "auth_failed_still_walled" if cookies_provided else "auth_failed_no_cookies"
    if not crawl_success or word_count == 0:
        return "auth_failed_unreachable"
    if word_count < _MIN_WORD_COUNT:
        # Reachable but thin — the page renders but has no real content.
        # Treated the same as unreachable for the wizard: the user must
        # configure cookies or pick a different seed.
        return "auth_failed_unreachable"
    return "auth_ok"


@router.post("/ingest/v1/crawl", response_model=CrawlResponse)
async def crawl_url(request: CrawlRequest, http_request: Request) -> CrawlResponse:
    """Fetch a URL with crawl4ai and ingest via the standard pipeline.

    Uses the same crawl4ai pipeline as the bulk crawler and preview endpoint,
    so JS-rendered pages (SPAs) are handled correctly and content_hash is
    consistent across all crawl paths.
    SPEC-TI-003 AC-6: identity assertion on body org_id.
    """
    if request.org_id:
        await assert_caller_identity(http_request, claimed_org_id=request.org_id)
    try:
        await validate_url(request.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _derive_path() -> str:
        if request.path:
            return request.path
        parsed = urlparse(request.url)
        slug = parsed.path.strip("/").replace("/", "-") or parsed.netloc
        return f"{slug}.md"

    async with tenant_scoped_connection(request.org_id) as conn:
        # Resolve stored domain selector so the right pipeline is used
        # SPEC-CRAWL-001 / R-2
        effective_selector: str | None = None
        if request.org_id:
            stored_sel = await get_domain_selector(
                conn, extract_domain(request.url), request.org_id
            )
            if stored_sel:
                effective_selector, _ = stored_sel

        # WARNING (pipeline config change): modifying crawl4ai settings in
        # crawl4ai_client.build_crawl_config() changes content_hash for every page
        # even when the actual page content has not changed.  After such a change,
        # force a full re-ingest by clearing content_hash:
        #   UPDATE knowledge.crawled_pages
        #      SET content_hash = ''
        #    WHERE org_id = '<org>' AND kb_slug = '<slug>';
        fit_md, _word_count, raw_html = await _run_crawl(request.url, effective_selector)

        # Dual-hash dedup (see migration 012):
        #   1. raw_html_hash unchanged → skip everything (fast path)
        #   2. raw_html_hash changed, content_hash unchanged → JS/tracking update, skip ingest
        #   3. both changed → real content change → full ingest
        raw_html_hash = hashlib.sha256(raw_html.encode()).hexdigest()
        stored = await pg_store.get_crawled_page_stored(
            conn, request.org_id, request.kb_slug, request.url
        )

        if stored is not None:
            stored_raw, _stored_content = stored
            if stored_raw is not None and stored_raw == raw_html_hash:
                logger.info("crawl_skipped_unchanged", url=request.url)
                return CrawlResponse(url=request.url, path=_derive_path(), chunks_ingested=0)

        content_hash = hashlib.sha256(fit_md.encode()).hexdigest()

        if stored is not None:
            _, stored_content = stored
            if stored_content is not None and stored_content == content_hash:
                # HTML changed (JS / tracking pixel) but article content is identical
                # → update raw_html_hash so future crawls hit the fast path, skip ingest
                await pg_store.upsert_crawled_page(
                    conn,
                    org_id=request.org_id,
                    kb_slug=request.kb_slug,
                    url=request.url,
                    raw_html_hash=raw_html_hash,
                    content_hash=content_hash,
                    raw_markdown=fit_md,
                    crawled_at=int(time.time()),
                )
                logger.info("crawl_skipped_html_noise", url=request.url)
                return CrawlResponse(url=request.url, path=_derive_path(), chunks_ingested=0)

        await pg_store.upsert_crawled_page(
            conn,
            org_id=request.org_id,
            kb_slug=request.kb_slug,
            url=request.url,
            raw_html_hash=raw_html_hash,
            content_hash=content_hash,
            raw_markdown=fit_md,
            crawled_at=int(time.time()),
        )

        path = _derive_path()

        # Ingest using existing pipeline (expects IngestRequest, returns dict)
        # SPEC-CRAWL-001 / R-5: include source_url in extra
        # SPEC-CRAWLER-003 R11: populate link graph fields when source_url present
        # SEQUENTIAL not gather — asyncpg.Connection is not concurrent-safe.
        # See crawler.py adapter for the full incident note (Voys help 2026-05-06).
        extra: dict = {"source_url": request.url}
        try:
            from knowledge_ingest import link_graph

            outbound = await link_graph.get_outbound_urls(
                conn, request.url, request.org_id, request.kb_slug
            )
            anchors = await link_graph.get_anchor_texts(
                conn, request.url, request.org_id, request.kb_slug
            )
            incoming = await link_graph.get_incoming_count(
                conn, request.url, request.org_id, request.kb_slug
            )
            extra["links_to"] = outbound[:20]
            extra["anchor_texts"] = anchors
            extra["incoming_link_count"] = incoming
        except Exception as exc:
            logger.warning("link_graph_query_failed", url=request.url, error=str(exc))
        ingest_req = IngestRequest(
            org_id=request.org_id,
            kb_slug=request.kb_slug,
            path=path,
            content=fit_md,
            source_type="crawl",
            source_domain=urlparse(request.url).netloc,
            extra=extra,
        )
        result = await ingest_document(conn, ingest_req)
        n_chunks = result.get("chunks", 0)

    logger.info("crawl_ingest_complete", url=request.url, path=path, chunks=n_chunks)
    return CrawlResponse(url=request.url, path=path, chunks_ingested=n_chunks)


# Type-only import to silence unused warning for asyncpg.Connection
_ = asyncpg.Connection
