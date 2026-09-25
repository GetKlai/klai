"""Bulk-sync crawl endpoint (SPEC-CRAWLER-004 Fase C).

``POST /ingest/v1/crawl/sync`` replaces the per-adapter klai-connector web
crawler flow with a single internal API. klai-connector (once Fase D lands)
sends the connector_id + config here; knowledge-ingest looks up the
encrypted cookies, decrypts them in-process via the shared
``klai-connector-credentials`` library (REQ-01.3 — plaintext cookies never
leave a service boundary), creates a ``knowledge.crawl_jobs`` row, enqueues
the Procrastinate ``run_crawl`` task, and returns ``{job_id, status}`` in
under 500 ms (REQ-03.2).

Polling: ``GET /ingest/v1/crawl/sync/{job_id}/status`` reads the row and
echoes ``status`` + ``pages_done`` + ``pages_total`` so the caller
(klai-connector's sync_engine in Fase D) can drive sync_runs state.

The endpoint sits behind the existing ``InternalSecretMiddleware`` — no
additional auth check required; unauthenticated requests never reach the
route handler.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Mapping
from typing import Any
from urllib.parse import urljoin, urlparse

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from knowledge_ingest.config import settings
from knowledge_ingest.connector_cookies import (
    ConnectorDecryptError,
    ConnectorNotFoundError,
    ConnectorOrgMismatchError,
    load_connector_cookies,
)
from knowledge_ingest.connector_state import (
    activate_connector_resource,
    get_current_connector_resource_key,
)
from knowledge_ingest.crawl_result_processing import html_visible_text
from knowledge_ingest.crawl_url_policy import same_site_domain
from knowledge_ingest.db import get_pool, tenant_scoped_connection
from knowledge_ingest.resource_jobs import cancel_jobs_by_resource_key, connector_resource_key
from knowledge_ingest.routes.crawl import _probe_fetch, _ProbeResponse
from knowledge_ingest.utils.auth_wall_classifier import classify_auth_wall
from knowledge_ingest.utils.url_validator import SsrfBlockedError, validate_url_pinned

logger = structlog.get_logger()
router = APIRouter()


# @MX:ANCHOR: CrawlSyncRequest -- stable contract between klai-connector and knowledge-ingest
# @MX:REASON: Adding/renaming a field breaks the delegation path added in Fase D.
# @MX:SPEC: SPEC-CRAWLER-004 REQ-03.1
class CrawlSyncRequest(BaseModel):
    """Payload for ``POST /ingest/v1/crawl/sync``.

    Callers send ``connector_id`` rather than any secret. knowledge-ingest
    resolves the cookies itself via the shared credentials library so
    plaintext cookies never cross a service boundary.
    """

    connector_id: uuid.UUID
    generation: str
    org_id: str
    kb_slug: str
    base_url: str
    max_pages: int = Field(default=200, ge=1, le=10000)
    path_prefix: str | None = None
    content_selector: str | None = None
    canary_url: str | None = None
    canary_fingerprint: str | None = None
    login_indicator: str | None = None
    max_depth: int = Field(default=3, ge=1, le=10)
    # Known-good interior page (validated in preview). Used as a fallback
    # crawl seed when base_url discovers no ingestable pages.
    discovery_seed_url: str | None = None


class CrawlSyncResponse(BaseModel):
    job_id: str
    status: str


class CrawlSyncStatusResponse(BaseModel):
    job_id: str
    status: str
    pages_total: int | None
    pages_done: int | None
    error: str | None


_FAILED_PARTIAL_STATUS = "failed_partial"
_BLOG_ARCHIVE_SEGMENTS = ("tag", "tags", "category", "categories", "author", "page")


async def _validate_connector(
    connector_id: uuid.UUID,
    org_id: str,
) -> None:
    """Validate connector exists + decryption would succeed, without keeping plaintext.

    SPEC-CRAWLER-004 fix for REQ-05.4: decrypted cookies must never be passed
    to the Procrastinate task as kwargs (the worker logs args verbatim). So
    the endpoint only verifies that a decrypt WOULD work and enqueues just
    the ``connector_id``; the task reloads the cookies at run time via the
    same helper. Plaintext cookies live only in memory, per-request.

    Raises:
        HTTPException(404): connector_id not found.
        HTTPException(409): zitadel_org_id mismatch.
        HTTPException(500): ENCRYPTION_KEY missing / malformed / decrypt fails.
    """
    pool = await get_pool()
    try:
        await load_connector_cookies(
            connector_id=connector_id,
            expected_zitadel_org_id=org_id,
            pool=pool,
            kek_hex=settings.encryption_key,
        )
    except ConnectorNotFoundError as exc:
        raise HTTPException(status_code=404, detail="connector_not_found") from exc
    except ConnectorOrgMismatchError as exc:
        raise HTTPException(status_code=409, detail="connector_org_mismatch") from exc
    except ConnectorDecryptError as exc:
        logger.error(
            "crawl_sync_decrypt_failed",
            connector_id=str(connector_id),
            reason="auth_tag_mismatch",
        )
        raise HTTPException(status_code=500, detail="decrypt_failed") from exc
    except ValueError as exc:
        # Raised by load_connector_cookies when KEK missing/malformed.
        msg = str(exc)
        if "not_configured" in msg:
            raise HTTPException(
                status_code=500,
                detail="encryption_key_not_configured",
            ) from exc
        logger.exception("crawl_sync_bad_kek", connector_id=str(connector_id))
        raise HTTPException(status_code=500, detail="encryption_key_invalid") from exc


def _default_exclude_patterns(normalized_path_prefix: str | None) -> list[str] | None:
    """Exclude collection/archive pages that should not count as content."""
    if normalized_path_prefix != "/blog":
        return None
    return [f"/blog/{segment}/*" for segment in _BLOG_ARCHIVE_SEGMENTS]


def _normalize_path_prefix(base_url: str, path_prefix: str | None) -> str | None:
    """Return a path-only prefix, accepting legacy rows that stored a full URL."""
    if not path_prefix or not path_prefix.strip():
        return None
    raw = path_prefix.strip()
    parsed_prefix = urlparse(raw)
    if parsed_prefix.scheme or parsed_prefix.netloc:
        parsed_base = urlparse(base_url)
        if (
            parsed_prefix.scheme.lower() != parsed_base.scheme.lower()
            or parsed_prefix.netloc.lower() != parsed_base.netloc.lower()
        ):
            raise HTTPException(status_code=400, detail="path_prefix_must_match_base_url")
        prefix_path = "/" + (parsed_prefix.path or "/").lstrip("/")
        base_path = "/" + (parsed_base.path or "/").lstrip("/")
        if not base_path.endswith("/"):
            base_path += "/"
        if not prefix_path.endswith("/"):
            prefix_path += "/"
        if prefix_path == base_path:
            raw = "/"
        elif prefix_path.startswith(base_path):
            raw = "/" + prefix_path[len(base_path) :]
        else:
            raise HTTPException(status_code=400, detail="path_prefix_must_match_base_url")
    normalized = "/" + raw.lstrip("/")
    return None if normalized == "/" else normalized


@router.post(
    "/ingest/v1/crawl/sync",
    response_model=CrawlSyncResponse,
    status_code=202,
)
async def crawl_sync(req: CrawlSyncRequest) -> CrawlSyncResponse:
    """Enqueue a bulk web crawl; cookies load at task run-time, not enqueue-time."""
    # Fail fast: confirm the connector exists + cookies would decrypt. Do NOT
    # persist the plaintext — the task will reload at run time.
    await _validate_connector(req.connector_id, req.org_id)

    job_id = str(uuid.uuid4())
    now = int(time.time())
    config_for_audit = req.model_dump(mode="json")
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO knowledge.crawl_jobs
            (id, org_id, kb_slug, config, status, created_at, updated_at)
        VALUES ($1, $2, $3, $4, 'pending', $5, $5)
        """,
        job_id,
        req.org_id,
        req.kb_slug,
        json.dumps(config_for_audit),
        now,
    )

    # crawl4ai URLPatternFilter classifies '/nl/' as exact-match (fnmatch with no
    # wildcards) so '/nl/6-bubble' never matches. Appending '/*' makes it a PREFIX
    # pattern, which is the intended semantics for a path_prefix filter.
    #
    # Portal may store path_prefix as either '/blog' or 'blog'. Normalize once
    # before composing start_url; otherwise 'https://host/' + 'blog' becomes the
    # invalid host-like URL 'https://hostblog'.
    normalized_path_prefix = _normalize_path_prefix(req.base_url, req.path_prefix)
    include_patterns = (
        [normalized_path_prefix.rstrip("/") + "/*"] if normalized_path_prefix else None
    )
    exclude_patterns = _default_exclude_patterns(normalized_path_prefix)

    # BFS must enter the graph at a node that links into the allowed subtree.
    # If the root page only links to sibling language paths (e.g. wiki shows
    # /en/ but user asked for /nl/), starting on req.base_url makes the filter
    # reject every outgoing link and the crawl halts after 1 page. Starting
    # on base_url + path_prefix gives BFS a seeded entry inside the filter set.
    start_url = req.base_url
    if normalized_path_prefix:
        start_url = req.base_url.rstrip("/") + normalized_path_prefix

    from knowledge_ingest import enrichment_tasks

    proc_app = enrichment_tasks.get_app()
    resource_key = connector_resource_key(req.org_id, req.kb_slug, req.connector_id, req.generation)
    async with tenant_scoped_connection(req.org_id) as conn:
        old_resource_key = await get_current_connector_resource_key(conn, resource_key)
        await activate_connector_resource(conn, resource_key)
    if old_resource_key and old_resource_key != resource_key:
        await cancel_jobs_by_resource_key(proc_app, pool, old_resource_key)
    await proc_app.run_crawl.defer_async(  # type: ignore[attr-defined]
        job_id=job_id,
        org_id=req.org_id,
        kb_slug=req.kb_slug,
        start_url=start_url,
        discovery_seed_url=req.discovery_seed_url,
        max_depth=req.max_depth,
        max_pages=req.max_pages,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
        rate_limit=2.0,
        content_selector=req.content_selector,
        login_indicator_selector=req.login_indicator,
        # REQ-05.4: connector_id only — plaintext cookies never enter the
        # Procrastinate args column or the worker's "Starting job" log.
        connector_id=str(req.connector_id),
        resource_key=resource_key,
        canary_url=req.canary_url,
        canary_fingerprint=req.canary_fingerprint,
    )

    logger.info(
        "crawl_sync_enqueued",
        job_id=job_id,
        connector_id=str(req.connector_id),
        org_id=req.org_id,
        kb_slug=req.kb_slug,
        start_url=start_url,
        exclude_patterns=exclude_patterns,
    )
    return CrawlSyncResponse(job_id=job_id, status="queued")


def _crawl_sync_error(row: Mapping[str, Any]) -> str | None:
    """Return the stable error code exposed to polling callers."""
    if row.get("error"):
        return str(row["error"])
    if row.get("status") != _FAILED_PARTIAL_STATUS:
        return None

    summary = row.get("error_summary")
    if isinstance(summary, str):
        try:
            summary = json.loads(summary)
        except json.JSONDecodeError:
            return _FAILED_PARTIAL_STATUS
    if isinstance(summary, Mapping) and summary.get("reason"):
        return str(summary["reason"])
    return _FAILED_PARTIAL_STATUS


@router.get(
    "/ingest/v1/crawl/sync/{job_id}/status",
    response_model=CrawlSyncStatusResponse,
)
async def crawl_sync_status(job_id: str) -> CrawlSyncStatusResponse:
    """Return the current state of a crawl job for polling callers."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT id, status, pages_total, pages_done, error, error_summary
        FROM knowledge.crawl_jobs
        WHERE id = $1
        """,
        job_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="job_not_found")
    return CrawlSyncStatusResponse(
        job_id=str(row["id"]),
        status=row["status"],
        pages_total=row["pages_total"],
        pages_done=row["pages_done"],
        error=_crawl_sync_error(row),
    )


@router.post(
    "/ingest/v1/crawl/sync/{job_id}/cancel",
    status_code=204,
)
async def crawl_sync_cancel(job_id: str) -> None:
    """Cancel an in-flight ``run_crawl`` procrastinate task.

    SPEC-WORKER-LANES-001. klai-connector's ``sync_engine`` calls this on
    poll timeout so the procrastinate task does not keep writing artifacts
    behind a sync_run that is already marked failed. Without it, the
    user-visible state (sync_run.status='failed') diverges from the data
    state (artifacts continue to accumulate after the failure).

    2026-08-19 (crawl-cancel): this endpoint previously only called
    Procrastinate's own ``cancel_job_by_id_async(abort=True)``, which sets
    ``abort_requested`` on ``procrastinate_jobs`` — a signal the
    ``run_crawl`` task never actually checked, so cancelling an in-flight
    crawl was a silent no-op (verified in production: 204 returned, the
    crawl kept fetching pages for minutes afterward). The fix is
    cooperative cancellation via ``knowledge.crawl_jobs.cancel_requested``,
    set below and polled by ``crawl_site``'s bulk-fetch loop between
    chunks (``adapters.crawler.run_crawl_job`` builds the poll closure —
    see its docstring). The Procrastinate-level abort call is kept
    alongside it, not replaced: it still serves a job that has not started
    running yet (status ``'todo'``), where it prevents the worker from
    ever picking the job up at all — cheaper than relying on the new flag,
    which only takes effect once ``run_crawl_job`` is already executing.

    Idempotent:

    * If the procrastinate task is already finished (succeeded / failed /
      aborted), this returns 204 — there is nothing to cancel and the data
      is already where it should be.
    * If the underlying ``knowledge.crawl_jobs`` row is unknown, returns 404
      so the caller can distinguish "wrong job_id" from "already done".
    """
    pool = await get_pool()
    crawl_row = await pool.fetchrow(
        "SELECT id, status FROM knowledge.crawl_jobs WHERE id = $1",
        job_id,
    )
    if crawl_row is None:
        raise HTTPException(status_code=404, detail="job_not_found")

    # Find the matching procrastinate run_crawl task. The task args carry
    # ``job_id`` (the crawl_jobs.id, not procrastinate's own id) so we look
    # it up there. Most recent matching row wins — retried jobs would have
    # multiple, the live one is the newest in 'todo' or 'doing'.
    proc_row = await pool.fetchrow(
        """
        SELECT id, status FROM procrastinate_jobs
        WHERE task_name = 'knowledge_ingest.crawl_tasks.run_crawl'
          AND args->>'job_id' = $1
          AND status IN ('todo', 'doing')
        ORDER BY id DESC
        LIMIT 1
        """,
        job_id,
    )
    if proc_row is None:
        # No live procrastinate row — task already finished (succeeded /
        # failed / aborted) or was never enqueued. Either way, cancel is a
        # no-op; the caller's intent (stop the work) is satisfied.
        logger.info(
            "crawl_sync_cancel_noop",
            job_id=job_id,
            crawl_status=crawl_row["status"],
        )
        return

    # The task is still live (todo/doing) — mark it for cooperative
    # cancellation. ``run_crawl_job`` polls this between bulk-fetch chunks
    # and stops within one chunk once it flips true. Guarded to runnable
    # statuses only so this never resurrects an already-terminal row (a
    # narrow race against the running task finishing between the proc_row
    # query above and this UPDATE is harmless: the flag would simply go
    # unread).
    await pool.execute(
        "UPDATE knowledge.crawl_jobs SET cancel_requested=true, updated_at=$2 "
        "WHERE id=$1 AND status IN ('pending', 'running')",
        job_id,
        int(time.time()),
    )

    # Lazy import to keep procrastinate optional in test environments
    # where ENRICHMENT_ENABLED=false.
    import procrastinate  # noqa: F401

    from knowledge_ingest import enrichment_tasks

    proc_app = enrichment_tasks.get_app()
    if proc_app is None:
        # Worker bootstrap hasn't completed yet — the task can't be running
        # either, so this is effectively the no-op path.
        logger.warning(
            "crawl_sync_cancel_proc_app_unavailable",
            job_id=job_id,
            proc_job_id=proc_row["id"],
        )
        return

    try:
        # ``abort=True`` signals the running worker to interrupt the task at
        # the next safe checkpoint via the ``abort_requested`` column.
        # ``delete_job=False`` keeps the row for observability (status
        # transitions to ``cancelled``).
        await proc_app.job_manager.cancel_job_by_id_async(
            proc_row["id"], abort=True, delete_job=False
        )
        logger.info(
            "crawl_sync_cancel_requested",
            job_id=job_id,
            proc_job_id=proc_row["id"],
            previous_status=proc_row["status"],
        )
    except Exception:
        # Cancel is best-effort. If procrastinate raises (e.g. job
        # transitioned to a terminal state mid-call), we still return 204:
        # the caller's intent is satisfied because the task is no longer
        # going to write new data.
        logger.exception(
            "crawl_sync_cancel_failed",
            job_id=job_id,
            proc_job_id=proc_row["id"],
        )


class CrawlKeepAliveRequest(BaseModel):
    """Payload for POST /ingest/v1/crawl/keep-alive.

    Same contract shape as CrawlSyncRequest: callers send connector_id, never
    a cookie value — knowledge-ingest resolves cookies itself via the shared
    credentials library.
    """

    connector_id: uuid.UUID
    org_id: str
    url: str


class CrawlKeepAliveResponse(BaseModel):
    ok: bool
    # Populated only when ok=False, so klai-connector can log an actionable
    # error (connector id + reason) instead of a bare bool. One of:
    # "cookie_load_failed", "no_saved_credentials", "url_invalid",
    # "fetch_failed", "redirect_left_domain", "too_many_redirects", or a
    # comma-joined classify_auth_wall match_reasons tuple (e.g.
    # "redirect_to_login", "end_of_body_login_marker").
    reason: str | None = None


# A same-host language/path redirect (the observed production failure: root
# 302 to /en) is one hop. 5 is generous headroom without letting a
# misconfigured site turn every tick into a long redirect chain.
_MAX_KEEPALIVE_REDIRECTS = 5


async def _follow_same_site_redirects(
    url: str,
    pin_map: dict[str, str],
    cookies: dict[str, str],
    base_hostname: str,
) -> tuple[_ProbeResponse | None, str]:
    """Follow same-site 3xx redirects to the real page cookies must reach.

    ``_probe_fetch`` runs with ``follow_redirects=False`` so a bare 3xx never
    silently escapes the SSRF pin. This drives that loop itself: each hop is
    re-validated with :func:`validate_url_pinned` (so a redirect to a new
    same-site host still gets its own pinned IP — no DNS-rebinding TOCTOU
    across hops) and checked with :func:`same_site_domain` — a redirect to a
    different site (e.g. an SSO host) is a boundary the probe must never
    cross, so it is reported as a failure rather than followed.

    Returns ``(final_response, final_url)`` on success, or
    ``(None, reason)`` when a redirect leaves the site, the pinned
    revalidation rejects a hop, or the hop budget is exhausted.
    """
    current_url = url
    for _ in range(_MAX_KEEPALIVE_REDIRECTS):
        result = await _probe_fetch(current_url, pin_map, cookies)
        if not (300 <= result.status_code < 400 and result.location):
            return result, current_url

        next_url = urljoin(current_url, result.location)
        next_host = urlparse(next_url).netloc.lower()
        if not same_site_domain(next_host, base_hostname):
            return None, f"redirect_left_domain:{next_host}"

        if next_host not in pin_map:
            try:
                validated = await validate_url_pinned(next_url)
            except (ValueError, SsrfBlockedError) as exc:
                return None, f"redirect_ssrf_blocked:{exc}"
            pin_map[validated.hostname] = validated.preferred_ip

        current_url = next_url
    return None, "too_many_redirects"


@router.post("/ingest/v1/crawl/keep-alive", response_model=CrawlKeepAliveResponse)
async def crawl_keepalive(req: CrawlKeepAliveRequest) -> CrawlKeepAliveResponse:
    """Touch a connector's stored session with one cheap authenticated GET.

    Best-effort liveness ping for klai-connector's keep-alive job (a stored
    session goes idle-timeout between once-daily scheduled crawls). Never
    raises — a failed touch is just ``ok=False``, not a 500.

    Follows same-site redirects (REQ: the probed URL is often the site root,
    which many CMSes 302 to a language path) and classifies the final page
    with the shared :func:`classify_auth_wall` heuristic, so a redirect to a
    login page or a thin authenticated-looking stub is caught the same way
    the crawler itself would catch it — not just a bare status-code check.
    """
    try:
        cookies = await load_connector_cookies(
            connector_id=req.connector_id,
            expected_zitadel_org_id=req.org_id,
            pool=await get_pool(),
            kek_hex=settings.encryption_key,
        )
    except (
        ConnectorNotFoundError,
        ConnectorOrgMismatchError,
        ConnectorDecryptError,
        ValueError,
    ) as exc:
        logger.warning(
            "crawl_keepalive_cookie_load_failed",
            connector_id=str(req.connector_id),
            error=str(exc),
        )
        return CrawlKeepAliveResponse(ok=False, reason="cookie_load_failed")

    if not cookies:
        return CrawlKeepAliveResponse(ok=False, reason="no_saved_credentials")

    cookie_dict = {
        c["name"]: c["value"]
        for c in cookies
        if isinstance(c, dict) and c.get("name") and c.get("value")
    }

    try:
        validated = await validate_url_pinned(req.url)
    except ValueError as exc:
        logger.warning(
            "crawl_keepalive_url_invalid",
            connector_id=str(req.connector_id),
            url=req.url,
            error=str(exc),
        )
        return CrawlKeepAliveResponse(ok=False, reason="url_invalid")

    pin_map = {validated.hostname: validated.preferred_ip}
    try:
        result, final = await _follow_same_site_redirects(
            req.url, pin_map, cookie_dict, validated.hostname
        )
    except Exception as exc:
        logger.warning(
            "crawl_keepalive_fetch_failed",
            connector_id=str(req.connector_id),
            url=req.url,
            error=str(exc),
        )
        return CrawlKeepAliveResponse(ok=False, reason="fetch_failed")

    if result is None:
        # `final` here is the reason string, not a URL (see the tuple-union
        # return contract on `_follow_same_site_redirects`).
        logger.error(
            "crawl_keepalive_session_logged_out",
            connector_id=str(req.connector_id),
            org_id=req.org_id,
            url=req.url,
            reason=final,
        )
        return CrawlKeepAliveResponse(ok=False, reason=final)

    visible_text = html_visible_text(result.text)
    auth_wall = classify_auth_wall(
        response_status_code=result.status_code,
        redirect_target_url=None,  # already resolved by _follow_same_site_redirects
        set_cookie_header=result.set_cookie,
        word_count=len(visible_text.split()),
        fit_markdown=visible_text,
        raw_html=result.text,
    )
    ok = 200 <= result.status_code < 300 and not auth_wall.is_walled
    reason = None if ok else (", ".join(auth_wall.match_reasons) or f"status_{result.status_code}")

    log = logger.info if ok else logger.error
    log(
        "crawl_keepalive_probe" if ok else "crawl_keepalive_session_logged_out",
        connector_id=str(req.connector_id),
        org_id=req.org_id,
        url=req.url,
        final_url=final,
        status_code=result.status_code,
        ok=ok,
        reason=reason,
    )
    return CrawlKeepAliveResponse(ok=ok, reason=reason)
