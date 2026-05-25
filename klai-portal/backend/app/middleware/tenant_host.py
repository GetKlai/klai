"""
KlaiTenantHostMiddleware — validates URL hostname against the session's tenant.

Klai is subdomain-routed: every customer lives at `<org-slug>.getklai.com`.
The session cookie is scoped to `.getklai.com` so it follows the user across
all tenant subdomains. Without this guard, a user logged in to org A would
silently see their own org-A data when visiting `<orgB-slug>.getklai.com` —
the URL would lie about which tenant the user is viewing.

This middleware closes that gap. On every request:

1. Skip when there is no session, when the request is a CORS preflight,
   when the path is on the public/pre-auth allow-list, or when the
   hostname is not a tenant subdomain (e.g. ``my.getklai.com``).
2. Resolve the session's org slug from ``portal_orgs.slug`` and compare
   it to the first DNS label of the request hostname.
3. On match: pass through.
4. On mismatch: return either a 302 redirect (HTML navigation) or a
   409 JSON payload (XHR) so the SPA can call ``window.location.replace``.

The slug lookup is cached in-process for ``_SLUG_CACHE_TTL_SECONDS``. Klai
runs with O(50) tenants, so after warm-up the lookup is a dict hit.
"""

from __future__ import annotations

import re
import time
from urllib.parse import urlsplit, urlunsplit

import structlog
from fastapi import Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.config import settings
from app.core.session import SessionContext

logger = structlog.get_logger()

# RFC 1123 hostname-label shape: lowercase alphanumeric + hyphens, max 63
# chars, must start and end with alphanumeric. Klai slugs are stored in
# ``portal_orgs.slug`` (max 64 chars per ``app/utils/slug.py``); enforcing
# this shape before splicing the slug into a hostname is defense-in-depth
# against any future schema change or rogue admin entry that could break
# the redirect URL or cross-host into an attacker-controlled domain.
_HOSTNAME_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")

# ---------------------------------------------------------------------------
# Skip rules
# ---------------------------------------------------------------------------

# Path prefixes that operate without a session-tenant binding. The CSRF
# allow-list in `app.middleware.session` is the spiritual sibling of this
# list; we keep it separate because the rules diverge (e.g. webhooks need
# CSRF skip but also tenant-host skip).
_SKIP_PATH_PREFIXES: tuple[str, ...] = (
    "/api/auth/",  # OIDC start/callback, login finishers, IDP intent, sso-complete
    "/api/signup",  # pre-session signup
    "/api/health",  # liveness probe
    "/api/perf",  # sendBeacon analytics, intentionally unauthenticated
    "/api/public/",  # reserved public surface
    "/api/webhooks/",  # signed webhook callers, no session
    "/internal/",  # service-to-service (X-Internal-Secret auth)
    "/partner/",  # partner API keys, not the BFF cookie
    "/health",  # bare health endpoint
    "/docs",  # FastAPI Swagger UI
    "/openapi.json",
    "/redoc",
)

# First DNS label values that are NOT tenant slugs. Subdomains used for
# Klai infrastructure that share `.{settings.domain}` but never carry a
# tenant context.
_NON_TENANT_HOSTS: frozenset[str] = frozenset(
    {
        "my",  # canonical login portal — see klai/infra/servers.md
        "auth",  # Zitadel
        "llm",  # LiteLLM proxy
        "grafana",
        "errors",  # GlitchTip
        "connector",  # public klai-connector ingress
        "logs-ingest",  # VictoriaLogs push
        "dev",  # protected dev portal
    }
)

# ---------------------------------------------------------------------------
# Slug cache
# ---------------------------------------------------------------------------

_SLUG_CACHE_TTL_SECONDS = 300.0
_slug_cache: dict[int, tuple[str, float]] = {}


def _slug_cache_clear() -> None:
    """Test hook — purge the in-process slug cache."""
    _slug_cache.clear()


async def _resolve_org_slug(org_id: int) -> str | None:
    """Return ``portal_orgs.slug`` for ``org_id``, cached for 5 minutes.

    Returns ``None`` when the org row is missing (deleted or unknown).
    """
    now = time.monotonic()
    cached = _slug_cache.get(org_id)
    if cached is not None and cached[1] > now:
        return cached[0]

    # Local import to avoid pulling the engine at module import time.
    from app.core.database import AsyncSessionLocal
    from app.models.portal import PortalOrg

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(PortalOrg.slug).where(PortalOrg.id == org_id))
        slug = result.scalar_one_or_none()

    if slug is not None:
        _slug_cache[org_id] = (slug, now + _SLUG_CACHE_TTL_SECONDS)
    return slug


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class KlaiTenantHostMiddleware(BaseHTTPMiddleware):
    """Reject requests whose URL hostname does not match the session tenant.

    Registered as the innermost middleware (after SessionMiddleware) so that
    ``request.state.session`` is already populated.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if self._should_skip(request):
            return await call_next(request)

        session = getattr(request.state, "session", None)
        if not isinstance(session, SessionContext) or session.org_id is None:
            # Unauthenticated / pre-finalised — let other middleware decide.
            return await call_next(request)

        host = request.url.hostname or ""
        host_slug = self._extract_tenant_slug(host)
        if host_slug is None:
            return await call_next(request)

        session_slug = await _resolve_org_slug(session.org_id)
        if session_slug is None:
            # Org row not found (deleted / cache miss on a vanished row).
            # Fail-open: another layer (404 in handlers) will surface this.
            return await call_next(request)

        # Defence-in-depth: slug is server-controlled (portal_orgs.slug)
        # but a hostname-shape validation makes it impossible to accidentally
        # produce a redirect Location that escapes ``*.{settings.domain}``.
        if not _HOSTNAME_LABEL_PATTERN.match(session_slug):
            logger.error(
                "tenant_host_invalid_session_slug",
                org_id=session.org_id,
                session_slug=session_slug,
            )
            return await call_next(request)

        if host_slug == session_slug:
            return await call_next(request)

        return self._mismatch_response(request, session_slug)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _should_skip(request: Request) -> bool:
        if request.method == "OPTIONS":
            return True
        path = request.url.path
        if any(path.startswith(prefix) for prefix in _SKIP_PATH_PREFIXES):
            return True
        return False

    @staticmethod
    def _extract_tenant_slug(host: str) -> str | None:
        """Return the tenant slug from a hostname, or None when not applicable.

        Returns None for:
          * empty / missing host
          * host that does not end with ``.{settings.domain}`` (local dev,
            custom domains)
          * first DNS label in :data:`_NON_TENANT_HOSTS`
        """
        if not host:
            return None
        suffix = "." + settings.domain
        if not host.endswith(suffix):
            return None
        first_label = host[: -len(suffix)].split(".", 1)[0]
        if not first_label or first_label in _NON_TENANT_HOSTS:
            return None
        return first_label

    @staticmethod
    def _mismatch_response(request: Request, session_slug: str) -> Response:
        # Both ``session_slug`` (validated against ``_HOSTNAME_LABEL_PATTERN``
        # by the caller) and ``settings.domain`` (hardcoded config) are
        # server-controlled. Construct the URL with ``urlunsplit`` so the
        # path/query/host segments stay structurally separated.
        target_path, target_query = KlaiTenantHostMiddleware._redirect_path_query(request)
        target = urlunsplit(
            (
                "https",
                f"{session_slug}.{settings.domain}",
                target_path,
                target_query,
                "",
            )
        )

        accept = request.headers.get("accept", "")
        wants_html = "text/html" in accept and "application/json" not in accept

        logger.info(
            "tenant_host_mismatch",
            host=request.url.hostname,
            session_slug=session_slug,
            path=request.url.path,
            response_kind="redirect" if wants_html else "json",
        )

        if wants_html:
            return RedirectResponse(url=target, status_code=status.HTTP_302_FOUND)

        # 409 Conflict: the request URL conflicts with the session's tenant.
        # Frontend (apiFetch.ts) parses ``error_code`` and does
        # ``window.location.replace(redirect_to)``.
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": {
                    "error_code": "tenant_host_mismatch",
                    "redirect_to": target,
                }
            },
        )

    @staticmethod
    def _redirect_path_query(request: Request) -> tuple[str, str]:
        """Return the browser route to use for tenant-mismatch redirects.

        Page navigations should keep their current path. API calls are
        different: redirecting to the same API path would land the browser on
        raw JSON (for example `/api/me`). For XHR/fetch mismatches, use the
        same-origin Referer page path when available, otherwise go to `/`.
        """
        if not request.url.path.startswith("/api/"):
            return request.url.path, request.url.query

        referer = request.headers.get("referer")
        if not referer:
            return "/", ""

        try:
            parsed = urlsplit(referer)
        except ValueError:
            return "/", ""

        if parsed.scheme not in {"http", "https"}:
            return "/", ""
        if parsed.hostname != request.url.hostname:
            return "/", ""
        if parsed.path.startswith("/api/"):
            return "/", ""
        return parsed.path or "/", parsed.query


__all__ = [
    "_NON_TENANT_HOSTS",
    "_SKIP_PATH_PREFIXES",
    "KlaiTenantHostMiddleware",
    "_resolve_org_slug",
    "_slug_cache_clear",
]
