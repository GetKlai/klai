"""
Web crawler adapter: bulk-crawls a website and ingests each page.
Uses the Crawl4AI REST API (shared Docker container) for all crawling.

SPEC-TI-003-FOLLOWUP-001 AC-1: ``run_crawl_job`` and its helpers take the
GUC-pinned ``asyncpg.Connection`` from the calling task's
``tenant_scoped_connection(org_id)`` block. Every knowledge.* read or write
runs on that same connection so RLS sees the tenant context.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter

import asyncpg
import httpx
import structlog
from klai_image_storage import ImageStore, download_and_upload_crawl_images

from knowledge_ingest import pg_store, qdrant_store
from knowledge_ingest.config import settings
from knowledge_ingest.crawl4ai_client import CrawlResult, crawl_site
from knowledge_ingest.models import IngestRequest
from knowledge_ingest.utils.auth_wall_classifier import classify_auth_wall
from knowledge_ingest.utils.auth_wall_detector import (
    AuthWallSignal,
    detect_anonymous_auth_wall,
)
from knowledge_ingest.utils.content_fingerprint import compute_simhash

logger = structlog.get_logger()

# SPEC-INGEST-LOGIN-WALL-DETECT-001 REQ-05 — recognised modes; anything else
# is treated as ``audit_only`` (fail-safe, REQ-05 AC-05.2). Defining the
# whitelist as a tuple keeps the validation cheap and easy to extend.
_VALID_LOGIN_WALL_MODES = ("reject", "degrade", "audit_only")


# SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-4: structured reason code written
# into ``crawl_jobs.error_summary`` and ``connector.sync_runs.error_details``
# when the dirty-content guard trips. REQ-5 surfaces this exact string in the
# UI badge query, so it MUST stay stable.
DIRTY_CONTENT_REASON = "boilerplate_or_authwall_dominant"
CRAWL_BUDGET_EXHAUSTED_REASON = "crawl_budget_exhausted"
CRAWL_FRONTIER_INCOMPLETE_REASON = "crawl_frontier_incomplete"
AUTH_WALL_DETECTED_REASON = "auth_wall_detected"

_NOT_FETCHED_REASON_PREFIX = "not_fetched_"

_DIRTY_CONTENT_SUGGESTION = (
    "Re-run preview from the connector edit page. The site likely now requires "
    "authentication or the content_selector is no longer matching."
)
_AUTH_WALL_WITH_AUTH_SUGGESTION = (
    "Configured authentication did not unlock all crawled pages. Refresh the "
    "saved cookies from the connector edit page before syncing again."
)


def decide_terminal_status(
    *,
    auth_wall_count: int,
    total_count: int,
    has_cookies: bool,
    has_login_indicator: bool,
    threshold: float,
) -> tuple[str, dict | None]:
    """SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-4 — pure decision function.

    Decide whether a finished crawl_site run should be marked
    ``failed_partial`` because too many pages classified as auth-walled
    while the operator did not configure cookies or a login indicator.

    @MX:NOTE — Threshold defaults to 0.30 in ``settings``; env var
    ``KLAI_INGEST_AUTHWALL_DIRTY_TRIP_RATE`` overrides per-deployment.
    Default calibrated against Voys help (0% trip-rate post-PR-#459)
    and Redcactus pre-fix (70% trip-rate).
    @MX:REASON — Loud failure beats silent skip. Operator gets actionable
    error in connector.sync_runs.error_details; UI surfaces via REQ-5.

    Args:
        auth_wall_count: Pages that classified as auth-walled.
        total_count: Total pages reaching the post-fetch decision.
        has_cookies: True when the connector has cookies configured (the
            operator EXPECTED auth-walled content; do not trip).
        has_login_indicator: True when ``login_indicator_selector`` is
            configured (same rationale as cookies).
        threshold: Trip-rate at or above which the guard fires. Inclusive
            (0.30 trips when 30/100 pages are walled).

    Returns:
        ``(terminal_status, summary_dict_or_None)``. ``summary_dict`` is
        the JSONB payload to write into ``crawl_jobs.error_summary`` when
        ``terminal_status == "failed_partial"`` and the guard fired.
        Returns ``(None, None)`` semantics are NOT used — when the guard
        does not fire, returns ``("", None)`` so the caller falls through
        to its existing terminal-status logic.
    """
    if total_count <= 0:
        return ("", None)
    if has_cookies or has_login_indicator:
        return ("", None)
    trip_rate = round(auth_wall_count / total_count, 3)
    if trip_rate < threshold:
        return ("", None)
    summary: dict = {
        "reason": DIRTY_CONTENT_REASON,
        "trip_rate": trip_rate,
        "auth_wall_count": auth_wall_count,
        "total_count": total_count,
        "suggestion": _DIRTY_CONTENT_SUGGESTION,
    }
    return ("failed_partial", summary)


def _build_crawl_outcome_warning(
    fetch_outcomes: list[dict],
    *,
    max_pages: int,
) -> dict | None:
    """Summarise discovered-but-unfetched URLs for crawl_jobs.error_summary.

    ``crawl_site`` owns URL discovery now, so a page-budget hit is no longer a
    quiet implementation detail inside Crawl4AI. The adapter turns those
    not_fetched outcomes into a compact, operator-readable warning.
    """
    omitted = [
        outcome
        for outcome in fetch_outcomes
        if str(outcome.get("reason_code") or "").startswith(_NOT_FETCHED_REASON_PREFIX)
    ]
    if not omitted:
        return None

    counts = Counter(str(outcome.get("reason_code") or "") for outcome in omitted)
    reason = (
        CRAWL_BUDGET_EXHAUSTED_REASON
        if counts.get("not_fetched_budget_exhausted", 0) > 0
        else CRAWL_FRONTIER_INCOMPLETE_REASON
    )
    return {
        "reason": reason,
        "max_pages": max_pages,
        "omitted_count": len(omitted),
        "omitted_reason_counts": dict(counts),
        "sample_omitted_urls": [
            str(outcome.get("url") or "") for outcome in omitted[:10] if outcome.get("url")
        ],
    }


def _crawl_warning_terminal_status(crawl_warning: dict | None) -> str:
    """Return the terminal status implied by a crawl warning, if any."""
    if crawl_warning is None:
        return ""
    if crawl_warning.get("reason") in {
        CRAWL_BUDGET_EXHAUSTED_REASON,
        CRAWL_FRONTIER_INCOMPLETE_REASON,
    }:
        return "failed_partial"
    return ""


def _header_value(headers: dict | None, name: str) -> str | None:
    if not headers:
        return None
    needle = name.lower()
    for key, value in headers.items():
        if str(key).lower() == needle:
            return str(value)
    return None


def _attach_crawl_warning(summary: dict, crawl_warning: dict | None) -> dict:
    """Attach warning details without replacing a stronger top-level reason."""
    if crawl_warning is not None:
        summary["crawl_warning"] = crawl_warning
    return summary


# @MX:ANCHOR: AuthWallDetected -- propagates login-indicator triggers from _ingest_crawl_result
#   back up to run_crawl_job, which converts them into a single structured
#   crawl_jobs.error entry and halts the remaining BFS pages.
# @MX:REASON: A silent auth wall would otherwise ingest login pages as "content"
#   and pollute Qdrant. Hard failing with a typed exception keeps the error
#   surface at exactly one row per sync regardless of page count.
# @MX:SPEC: SPEC-CRAWLER-004 REQ-02.3
class AuthWallDetected(Exception):
    """Raised when a page matches the configured ``login_indicator_selector``.

    Attributes:
        selector: The CSS selector that matched. Included in ``crawl_jobs.error``
            so operators can tell which indicator fired without reading logs.
    """

    def __init__(self, selector: str) -> None:
        super().__init__(f"auth_wall_detected: {selector}")
        self.selector = selector


# @MX:ANCHOR: AnonymousAuthWallDetected -- raised by _ingest_crawl_result for
#   anonymous-crawl walls. Distinct from AuthWallDetected: that one halts BFS
#   (session expired = downstream pages also walled). Anonymous walls are
#   per-page (one URL is gated, sibling URLs may be public) so the BFS handler
#   in run_crawl_job logs + skips + continues. See SPEC-INGEST-LOGIN-WALL-
#   DETECT-001 REQ-04 for the BFS-continuity contract.
# @MX:SPEC: SPEC-INGEST-LOGIN-WALL-DETECT-001 REQ-03
class AnonymousAuthWallDetected(Exception):
    """Raised when a page's content looks like a login-wall stub from an
    anonymous (no-cookies) crawl.

    Unlike :class:`AuthWallDetected`, this does NOT halt the BFS. The caller
    in ``run_crawl_job`` is expected to log + record + continue iteration
    (Phase C wires this fully; Phase B falls into the existing generic
    ``except Exception`` handler which counts the page as failed).

    Attributes:
        url: The page URL that was flagged.
        signal: The :class:`AuthWallSignal` describing which pattern matched.
    """

    def __init__(self, url: str, signal: AuthWallSignal) -> None:
        super().__init__(f"anonymous_auth_wall_detected: {url} ({signal.pattern})")
        self.url = url
        self.signal = signal


def _resolve_login_wall_mode() -> str:
    """Return the configured detection mode, falling back to ``audit_only``.

    REQ-05 AC-05.2: an invalid configured value MUST NOT crash the pipeline.
    We log a warning the first time we encounter the bad value (logger.warning
    is rate-limited at the structlog level) and treat it as audit_only so the
    crawl still runs and operators can investigate.
    """
    mode = settings.ingest_login_wall_detect_mode
    if mode not in _VALID_LOGIN_WALL_MODES:
        logger.warning(
            "login_wall_detector_config_invalid",
            configured=mode,
            falling_back_to="audit_only",
        )
        return "audit_only"
    return mode


def _build_image_store() -> ImageStore | None:
    """Construct an ImageStore from settings, or None if disabled.

    SPEC-CRAWLER-004 Fase A — empty ``garage_s3_endpoint`` means the image
    pipeline is turned off (e.g. in dev where Garage is not provisioned).
    """
    if not settings.garage_s3_endpoint:
        return None
    return ImageStore(
        endpoint=settings.garage_s3_endpoint,
        access_key=settings.garage_access_key,
        secret_key=settings.garage_secret_key,
        bucket=settings.garage_bucket,
        region=settings.garage_region,
    )


# @MX:ANCHOR: [AUTO] _build_link_graph — Phase 1 ordering contract with Phase 2
# @MX:REASON: page_links rows must exist before per-page link_graph queries run in
#   _ingest_crawl_result. Any caller that moves or removes this call breaks the
#   guarantee that get_anchor_texts() and get_incoming_count() return final values.
# @MX:SPEC: SPEC-CRAWLER-005 REQ-01.2
async def _build_link_graph(
    conn: asyncpg.Connection,
    results: list[CrawlResult],
    org_id: str,
    kb_slug: str,
) -> None:
    """Phase 1 of the two-phase crawl pipeline: upsert every page's
    outbound links BEFORE any page is ingested. This guarantees that
    ``link_graph.get_anchor_texts(P)`` and ``get_incoming_count(P)`` return
    final values during Phase 2, even for pages processed early.

    SPEC-CRAWLER-005 REQ-01.2.

    Idempotent: pg_store.upsert_page_links uses ON CONFLICT UPSERT.
    Failed crawl results (success=False) are skipped — they have no
    trustworthy links.
    """
    for result in results:
        if not result.success:
            continue
        internal = (result.links or {}).get("internal") or []
        if not internal:
            continue
        await pg_store.upsert_page_links(
            conn,
            org_id=org_id,
            kb_slug=kb_slug,
            from_url=result.url,
            links=internal,
        )


_UNSET = object()  # sentinel: stored_hash not yet fetched from DB


async def run_crawl_job(
    conn: asyncpg.Connection,
    job_id: str,
    org_id: str,
    kb_slug: str,
    start_url: str,
    max_depth: int = 2,
    max_pages: int = 200,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    rate_limit: float = 2.0,
    content_selector: str | None = None,
    login_indicator_selector: str | None = None,
    cookies: list[dict] | None = None,
    canary_url: str | None = None,
    canary_fingerprint: str | None = None,
    connector_id: str | None = None,
) -> None:
    """
    Crawl a website and ingest each page into the knowledge pipeline.
    Updates knowledge.crawl_jobs progress as pages are processed.

    Seeds the crawl with start_url + sitemap.xml, then lets crawl_site's
    Klai-owned frontier schedule same-domain links to max_depth.

    ``exclude_patterns`` is applied after crawl4ai discovery and before
    progress counting / dirty-content decisions. ``rate_limit`` is accepted
    for compatibility; Crawl4AI manages its own request pacing.

    ``login_indicator_selector`` (SPEC-CRAWLER-004 Fase B / REQ-02.3) is
    injected into crawl4ai's wait_for and also re-checked on every returned
    page. If any page is flagged as auth-walled the job is marked failed
    with ``error='auth_wall_detected: {selector}'`` and no further pages
    are ingested.

    SPEC-TI-003-FOLLOWUP-001 AC-1: ``conn`` carries the RLS GUC from the
    caller's ``tenant_scoped_connection(org_id)`` block. Every knowledge.*
    statement below runs on this same connection.
    """
    await _update_job(conn, job_id, status="running")

    pages_done = 0
    pages_failed = 0
    # SPEC-INGEST-LOGIN-WALL-DETECT-001 REQ-04 — anonymous-wall tracking. URLs
    # that hit AnonymousAuthWallDetected during ingest. After the BFS, this
    # populates crawl_jobs.error_summary (separate from `error`, which the
    # cookie-path AuthWallDetected handler still owns).
    auth_wall_pages: list[str] = []

    try:
        # SPEC-INGEST-RECONCILE-001 AC-4: crawl_site now returns
        # ``(results, outcomes)``. ``outcomes`` is a JSONB-shaped list with
        # one entry per discovered candidate URL — written to
        # ``crawl_jobs.fetch_outcomes`` so operators can answer "where did
        # the missing pages go?" without log forensics.
        results, fetch_outcomes = await crawl_site(
            start_url=start_url,
            selector=content_selector,
            max_depth=max_depth,
            max_pages=max_pages,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            login_indicator_selector=login_indicator_selector,
            cookies=cookies,
        )
        crawl_outcome_warning = _build_crawl_outcome_warning(
            fetch_outcomes,
            max_pages=max_pages,
        )
        # canary_url / canary_fingerprint are accepted for forwards-compat with
        # the /ingest/v1/crawl/sync request body; they are plumbed here but the
        # bulk crawl does not yet evaluate them (SPEC-CRAWL-004 canary check
        # currently lives in the preview endpoint). Declaring + no-opping them
        # keeps the public signature stable for Fase D delegation.
        _ = (canary_url, canary_fingerprint)

        # SPEC-INGEST-RECONCILE-001 AC-4: persist per-URL outcomes alongside
        # the page-count rollup. ``pages_total`` keeps its existing semantics
        # ("how many CrawlResults reached the ingest loop"); the JSONB
        # ``fetch_outcomes`` is the per-candidate breakdown.
        await conn.execute(
            "UPDATE knowledge.crawl_jobs "
            "SET pages_total=$1, fetch_outcomes=$2::jsonb, updated_at=$3 "
            "WHERE id=$4",
            len(results),
            json.dumps(fetch_outcomes),
            int(time.time()),
            job_id,
        )

        # SPEC-CRAWLER-005 Fase 1: build link graph BEFORE per-page ingest so
        # late pages don't read an empty graph. REQ-01.1.
        await _build_link_graph(conn, results, org_id, kb_slug)

        # Batch-fetch all known content hashes in a single query
        urls = [r.url for r in results]
        known_hashes = await pg_store.get_crawled_page_hashes(conn, org_id, kb_slug, urls)

        for result in results:
            url = result.url
            # SPEC-CRAWLER-004 Fase B: detect login-indicator trigger.
            # crawl4ai returns success=False when the injected wait_for times
            # out on the login selector; surfacing that as AuthWallDetected
            # gives us a single structured failure per sync.
            if login_indicator_selector and not result.success:
                raise AuthWallDetected(login_indicator_selector)
            try:
                await _ingest_crawl_result(
                    conn,
                    result,
                    url,
                    org_id,
                    kb_slug,
                    stored=known_hashes.get(url),
                    login_indicator_selector=login_indicator_selector,
                    connector_id=connector_id,
                )
                pages_done += 1
            except AuthWallDetected:
                # Halt the whole BFS — downstream handler in the except block
                # writes the job row; do not keep ingesting follow-up pages.
                raise
            except AnonymousAuthWallDetected as wall_exc:
                # SPEC-INGEST-LOGIN-WALL-DETECT-001 REQ-04.1 — per-page wall,
                # NOT a session-wide failure. Record + continue BFS so sibling
                # URLs (which may be public) still ingest.
                auth_wall_pages.append(wall_exc.url)
                logger.info(
                    "crawl_page_login_wall",
                    url=url,
                    job_id=job_id,
                    pattern=wall_exc.signal.pattern,
                    confidence=wall_exc.signal.confidence,
                )
            except Exception as exc:
                logger.warning("crawl_page_failed", url=url, job_id=job_id, error=str(exc))
                pages_failed += 1

            await conn.execute(
                "UPDATE knowledge.crawl_jobs SET pages_done=$1, updated_at=$2 WHERE id=$3",
                pages_done,
                int(time.time()),
                job_id,
            )

        # SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-4 — dirty-content guard.
        # Evaluate FIRST: if too many pages classified as auth-walled while
        # no cookies / login_indicator are configured, mark failed_partial
        # with the structured reason that REQ-5's UI badge keys on.
        dirty_status, dirty_summary = decide_terminal_status(
            auth_wall_count=len(auth_wall_pages),
            total_count=len(results),
            has_cookies=bool(cookies),
            has_login_indicator=bool(login_indicator_selector),
            threshold=settings.ingest_authwall_dirty_trip_rate,
        )

        # SPEC-INGEST-LOGIN-WALL-DETECT-001 REQ-04.2/04.3 — terminal status:
        # - failed_partial when REQ-4 dirty-content guard tripped (loud failure)
        # - failed_partial when 0 pages ingested AND >= 1 wall skipped
        # - completed otherwise
        if dirty_status:
            terminal_status = dirty_status
            summary_payload = dirty_summary or {}
            # Preserve the existing wall-tracking diagnostics alongside the
            # REQ-4 reason for forensic logs.
            summary_payload.setdefault("login_walls_skipped", len(auth_wall_pages))
            summary_payload.setdefault("sample_urls", auth_wall_pages[:10])
            _attach_crawl_warning(summary_payload, crawl_outcome_warning)
            summary_json = json.dumps(summary_payload)
            await conn.execute(
                "UPDATE knowledge.crawl_jobs SET status=$1, error_summary=$2::jsonb, "
                "updated_at=$3 WHERE id=$4",
                terminal_status,
                summary_json,
                int(time.time()),
                job_id,
            )
        elif auth_wall_pages and (cookies or login_indicator_selector):
            terminal_status = "failed_partial"
            summary_json = json.dumps(
                _attach_crawl_warning({
                    "reason": AUTH_WALL_DETECTED_REASON,
                    "login_walls_skipped": len(auth_wall_pages),
                    "sample_urls": auth_wall_pages[:10],
                    "suggestion": _AUTH_WALL_WITH_AUTH_SUGGESTION,
                }, crawl_outcome_warning)
            )
            await conn.execute(
                "UPDATE knowledge.crawl_jobs SET status=$1, error_summary=$2::jsonb, "
                "updated_at=$3 WHERE id=$4",
                terminal_status,
                summary_json,
                int(time.time()),
                job_id,
            )
        elif _crawl_warning_terminal_status(crawl_outcome_warning):
            terminal_status = _crawl_warning_terminal_status(crawl_outcome_warning)
            summary_payload = dict(crawl_outcome_warning or {})
            if auth_wall_pages:
                summary_payload["login_walls_skipped"] = len(auth_wall_pages)
                summary_payload["sample_urls"] = auth_wall_pages[:10]
            summary_json = json.dumps(summary_payload)
            await conn.execute(
                "UPDATE knowledge.crawl_jobs SET status=$1, error_summary=$2::jsonb, "
                "updated_at=$3 WHERE id=$4",
                terminal_status,
                summary_json,
                int(time.time()),
                job_id,
            )
        elif auth_wall_pages and pages_done == 0:
            terminal_status = "failed_partial"
            summary_json = json.dumps(
                _attach_crawl_warning({
                    "login_walls_skipped": len(auth_wall_pages),
                    "sample_urls": auth_wall_pages[:10],
                }, crawl_outcome_warning)
            )
            await conn.execute(
                "UPDATE knowledge.crawl_jobs SET status=$1, error_summary=$2::jsonb, "
                "updated_at=$3 WHERE id=$4",
                terminal_status,
                summary_json,
                int(time.time()),
                job_id,
            )
        elif auth_wall_pages:
            terminal_status = "completed"
            summary_json = json.dumps(
                _attach_crawl_warning({
                    "login_walls_skipped": len(auth_wall_pages),
                    "sample_urls": auth_wall_pages[:10],
                }, crawl_outcome_warning)
            )
            await conn.execute(
                "UPDATE knowledge.crawl_jobs SET status=$1, error_summary=$2::jsonb, "
                "updated_at=$3 WHERE id=$4",
                terminal_status,
                summary_json,
                int(time.time()),
                job_id,
            )
        elif crawl_outcome_warning:
            terminal_status = "completed"
            await conn.execute(
                "UPDATE knowledge.crawl_jobs SET status=$1, error_summary=$2::jsonb, "
                "updated_at=$3 WHERE id=$4",
                terminal_status,
                json.dumps(crawl_outcome_warning),
                int(time.time()),
                job_id,
            )
        else:
            terminal_status = "completed"
            await _update_job(conn, job_id, status=terminal_status)

        if connector_id and terminal_status == "completed" and pages_done > 0:
            current_urls = [result.url for result in results]
            stale_paths = await pg_store.list_stale_connector_artifact_paths(
                conn,
                org_id=org_id,
                kb_slug=kb_slug,
                connector_id=connector_id,
                current_paths=current_urls,
            )
            if stale_paths:
                stale_paths_deleted: list[str] = []
                for path in stale_paths:
                    try:
                        await qdrant_store.delete_document(org_id, kb_slug, path)
                    except Exception as exc:
                        logger.warning(
                            "crawl_connector_stale_vector_delete_failed",
                            job_id=job_id,
                            connector_id=connector_id,
                            kb_slug=kb_slug,
                            path=path,
                            error=str(exc),
                        )
                    else:
                        stale_paths_deleted.append(path)
                if stale_paths_deleted:
                    retired_count = await pg_store.soft_delete_stale_connector_artifacts(
                        conn,
                        org_id=org_id,
                        kb_slug=kb_slug,
                        connector_id=connector_id,
                        stale_paths=stale_paths_deleted,
                    )
                    logger.info(
                        "crawl_connector_stale_artifacts_retired",
                        job_id=job_id,
                        connector_id=connector_id,
                        kb_slug=kb_slug,
                        retired_count=retired_count,
                    )

        logger.info(
            "crawl_job_complete",
            job_id=job_id,
            pages_done=pages_done,
            pages_failed=pages_failed,
            login_walls_skipped=len(auth_wall_pages),
            status=terminal_status,
        )

    except AuthWallDetected as exc:
        # SPEC-CRAWLER-004 REQ-02.3: one structured error per sync, no artifacts.
        logger.error(
            "crawl_job_auth_wall",
            job_id=job_id,
            selector=exc.selector,
            pages_ingested=pages_done,
        )
        await _update_job(conn, job_id, status="failed", error=str(exc))
    except Exception as exc:
        logger.exception("crawl_job_error", job_id=job_id, error=str(exc))
        await _update_job(conn, job_id, status="failed", error=str(exc))


async def _ingest_crawl_result(
    conn: asyncpg.Connection,
    result: CrawlResult,
    url: str,
    org_id: str,
    kb_slug: str,
    stored: pg_store.PageHashes | None | object = _UNSET,
    login_indicator_selector: str | None = None,
    connector_id: str | None = None,
) -> None:
    """Process a crawl result: dedup, extract links, ingest.

    WARNING (pipeline config change): modifying crawl4ai settings in
    crawl4ai_client.build_crawl_config() changes content_hash for every page
    even when the actual page content has not changed.  After such a change,
    force a full re-ingest by clearing content_hash:
      UPDATE knowledge.crawled_pages
         SET content_hash = ''
       WHERE org_id = '<org>' AND kb_slug = '<slug>';
    """
    if not result.success:
        # With a login indicator set, crawl4ai's wait_for fails on auth-walled
        # pages and returns success=False. run_crawl_job catches this first
        # (before calling us), but guard here too so a direct caller still
        # surfaces the typed exception instead of a generic ValueError.
        if login_indicator_selector:
            raise AuthWallDetected(login_indicator_selector)
        raise ValueError(f"Crawl failed: {result.error_message}")

    # Detect PDF: check Content-Type header first, fall back to URL extension
    content_type_header = ""
    if result.response_headers:
        content_type_header = result.response_headers.get("content-type", "")
    is_pdf = "application/pdf" in content_type_header or url.lower().endswith(".pdf")
    content_type = "pdf_document" if is_pdf else "kb_article"

    # Dual-hash dedup (see migration 012)
    if stored is _UNSET:
        stored = await pg_store.get_crawled_page_stored(conn, org_id, kb_slug, url)

    raw_html = result.html or ""
    raw_html_hash = hashlib.sha256(raw_html.encode()).hexdigest()

    if stored is not None:
        stored_raw, _stored_content = stored  # type: ignore[misc]
        if stored_raw is not None and stored_raw == raw_html_hash:
            logger.info("crawl_skipped_unchanged", url=url, org_id=org_id, kb_slug=kb_slug)
            return

    text = result.fit_markdown or result.raw_markdown or ""
    front_matter = (result.metadata or {}).get("description", "")

    content_hash = hashlib.sha256(text.encode()).hexdigest()
    if stored is not None:
        _, stored_content = stored  # type: ignore[misc]
        if stored_content is not None and stored_content == content_hash:
            await pg_store.upsert_crawled_page(
                conn,
                org_id=org_id,
                kb_slug=kb_slug,
                url=url,
                raw_html_hash=raw_html_hash,
                content_hash=content_hash,
                raw_markdown=text,
                crawled_at=int(time.time()),
            )
            logger.info("crawl_skipped_html_noise", url=url, org_id=org_id, kb_slug=kb_slug)
            return

    # SPEC-INGEST-LOGIN-WALL-DETECT-002 REQ-01 — compute the page's SimHash
    # once, BEFORE the detector call (the detector reuses it for cluster
    # lookup) and store it AFTER the upsert below so the next crawl can
    # cluster against it. Computed unconditionally — even with detection
    # disabled, the fingerprint is needed for the operator-triggered
    # backfill / validation script.
    #
    # Empty / whitespace-only content yields ``compute_simhash == 0``. Storing
    # 0 would let "empty" pages cluster together (all match Hamming 0), which
    # would falsely flag a tenant whose crawl results were 5+ rendered-but-
    # empty pages. We treat the missing-content case as "no fingerprint" by
    # leaving content_simhash NULL, matching the cold-start contract.
    page_simhash: int | None = compute_simhash(text)
    if page_simhash == 0:
        page_simhash = None

    # SPEC-CONNECTOR-INPUT-VALIDATION-001 / REQ-4 — shared auth-wall
    # classifier at sync time. This catches single embedded protected-section
    # gates inside otherwise-large pages before they are written as content.
    login_wall_signal: AuthWallSignal | None = None
    login_wall_mode: str | None = None
    if settings.ingest_login_wall_detect_enabled:
        auth_wall = classify_auth_wall(
            response_status_code=None,
            redirect_target_url=None,
            set_cookie_header=_header_value(result.response_headers, "set-cookie"),
            word_count=result.word_count,
            fit_markdown=text,
            raw_html=raw_html,
        )
        if auth_wall.is_walled:
            login_wall_signal = AuthWallSignal(
                pattern="auth_wall_classifier",
                evidence=auth_wall.match_reasons,
                confidence=0.95,
            )

    # SPEC-INGEST-LOGIN-WALL-DETECT-002 REQ-02 — anonymous-crawl auth-wall
    # detection by SimHash near-duplicate clustering. Runs AFTER dedup (don't
    # waste work on already-seen pages) and BEFORE image upload + Qdrant
    # write (cheaper to bail early). Skipped when a direct classifier signal
    # already fired, and when login_indicator_selector is set because that path
    # has its own wait_for guard.
    if (
        settings.ingest_login_wall_detect_enabled
        and login_wall_signal is None
        and login_indicator_selector is None
    ):
        login_wall_signal = await detect_anonymous_auth_wall(
            result.raw_markdown or "",
            fit_markdown=result.fit_markdown or None,
            url=url,
            org_id=org_id,
            kb_slug=kb_slug,
            conn=conn,
            cluster_min=settings.ingest_template_cluster_min,
            target_simhash=page_simhash,
        )
    if login_wall_signal is not None:
        login_wall_mode = _resolve_login_wall_mode()
        if login_wall_mode == "reject":
            logger.info(
                "login_wall_reject",
                url=url,
                org_id=org_id,
                kb_slug=kb_slug,
                pattern=login_wall_signal.pattern,
                evidence=login_wall_signal.evidence,
                confidence=login_wall_signal.confidence,
            )
            raise AnonymousAuthWallDetected(url, login_wall_signal)
        if login_wall_mode == "degrade":
            logger.info(
                "login_wall_degrade",
                url=url,
                org_id=org_id,
                kb_slug=kb_slug,
                pattern=login_wall_signal.pattern,
                evidence=login_wall_signal.evidence,
                confidence=login_wall_signal.confidence,
            )
        else:  # audit_only
            logger.warning(
                "login_wall_detected",
                url=url,
                org_id=org_id,
                kb_slug=kb_slug,
                pattern=login_wall_signal.pattern,
                evidence=login_wall_signal.evidence,
                confidence=login_wall_signal.confidence,
                mode="audit_only",
            )

    extra: dict = {"source_url": url, "crawled_at": int(time.time())}
    # SPEC-INGEST-LOGIN-WALL-DETECT-001 REQ-03 — degrade mode pushes
    # quality_score=0.0 + ingest_warning into extra, which qdrant_store's
    # base_payload.update(_extra_payload_for_qdrant(extra_payload)) then
    # overrides over its hard-coded default of 0.5. The retrieval-side floor
    # filter (Phase E) refuses to serve quality_score < 0.05 chunks, which
    # is the actual exclusion mechanism.
    if login_wall_mode == "degrade":
        extra["quality_score"] = 0.0
        extra["ingest_warning"] = "login_wall_detected"
    if connector_id:
        # SPEC-CRAWLER-005 Fase 6 follow-up: wire source_connector_id through
        # so connector-delete (qdrant_store.delete_connector +
        # pg_store.delete_connector_artifacts) can actually find this chunk.
        # Before this, every crawl chunk had source_connector_id=None and
        # neither Qdrant nor artifact delete matched.
        extra["source_connector_id"] = connector_id
    if is_pdf and front_matter:
        extra["front_matter"] = front_matter

    # SPEC-CRAWLER-003 R11: populate link graph fields after page_links upsert.
    #
    # SEQUENTIAL not gather: asyncpg.Connection is NOT safe for concurrent use.
    # Submitting three queries through asyncio.gather on the same conn raises
    # ``InterfaceError: cannot perform operation: another operation is in
    # progress`` and leaves the conn in an unusable state — every subsequent
    # ``conn.execute`` in run_crawl_job (the per-page UPDATE on knowledge.crawl_jobs
    # at the bottom of the loop, and the terminal _update_job) then re-raises
    # the same error, killing the whole crawl. Discovered live on Voys help
    # 2026-05-06 — symptom: 0 of N pages ingested, procrastinate retry-loop.
    # Gather buys nothing here: a single asyncpg conn serialises queries on
    # the wire anyway.
    try:
        from knowledge_ingest import link_graph

        outbound = await link_graph.get_outbound_urls(conn, url, org_id, kb_slug)
        anchors = await link_graph.get_anchor_texts(conn, url, org_id, kb_slug)
        incoming = await link_graph.get_incoming_count(conn, url, org_id, kb_slug)
        extra["links_to"] = outbound[:20]
        extra["anchor_texts"] = anchors
        extra["incoming_link_count"] = incoming
    except Exception as exc:
        logger.warning("link_graph_query_failed", url=url, error=str(exc))

    # SPEC-CRAWLER-004 Fase A: extract and upload images from crawl4ai
    # media.images. Skipped silently when garage_s3_endpoint is empty.
    image_store = _build_image_store()
    if image_store is not None:
        media_images = (result.media or {}).get("images") or []
        if media_images:
            try:
                timeout = settings.image_download_timeout
                async with httpx.AsyncClient(timeout=timeout) as http_client:
                    image_urls = await download_and_upload_crawl_images(
                        media_images=media_images,
                        base_url=url,
                        org_id=org_id,
                        kb_slug=kb_slug,
                        image_store=image_store,
                        http_client=http_client,
                    )
                if image_urls:
                    extra["image_urls"] = image_urls
            except Exception as exc:
                logger.warning("crawl_image_upload_failed", url=url, error=str(exc))

    from knowledge_ingest.routes.ingest import ingest_document

    # SPEC-CRAWLER-005 Fase 6 follow-up: was "connector"; crawl chunks now carry
    # source_type="crawl" so retrieval + assertions can distinguish them from
    # non-crawl connector artifacts (notion, github, gdrive).
    await ingest_document(
        conn,
        IngestRequest(
            org_id=org_id,
            kb_slug=kb_slug,
            path=url,
            content=text,
            source_type="crawl",
            content_type=content_type,
            synthesis_depth=1,
            extra=extra,
        ),
    )

    await pg_store.upsert_crawled_page(
        conn,
        org_id=org_id,
        kb_slug=kb_slug,
        url=url,
        raw_html_hash=raw_html_hash,
        content_hash=content_hash,
        raw_markdown=text,
        crawled_at=int(time.time()),
    )

    # SPEC-INGEST-LOGIN-WALL-DETECT-002 REQ-01 — persist the page's SimHash
    # so the next crawl in this KB can include it in cluster lookups. Done
    # AFTER upsert_crawled_page so the row exists; the helper does an UPDATE
    # by (org_id, kb_slug, url). Skipped for empty/whitespace-only content
    # (page_simhash is None) so 0-fingerprints never reach the DB.
    if page_simhash is not None:
        await pg_store.update_crawled_page_simhash(
            conn,
            org_id=org_id,
            kb_slug=kb_slug,
            url=url,
            content_simhash=page_simhash,
        )


async def _update_job(
    conn: asyncpg.Connection, job_id: str, status: str, error: str | None = None
) -> None:
    await conn.execute(
        "UPDATE knowledge.crawl_jobs SET status=$1, error=$2, updated_at=$3 WHERE id=$4",
        status,
        error,
        int(time.time()),
        job_id,
    )
