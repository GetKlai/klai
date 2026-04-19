"""
BFF session primitives — shared constants and typed context for SPEC-AUTH-008.

This module is dependency-free on purpose: it can be imported from middleware,
services, and routes without pulling in Redis or Zitadel clients.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Cookie names
#
# Both cookies use the `__Secure-` prefix (requires Secure=true) and are scoped
# to `.getklai.com` so they are shared across the portal + tenant subdomains.
# `__Host-` is NOT usable here because it forbids setting Domain, which would
# restrict the cookie to a single origin and break the my.getklai.com → tenant
# subdomain session sharing.
# ---------------------------------------------------------------------------

SESSION_COOKIE_NAME = "__Secure-klai_session"
"""HttpOnly; Secure; SameSite=Lax; Domain=.getklai.com — holds the opaque sid."""

CSRF_COOKIE_NAME = "__Secure-klai_csrf"
"""Secure; SameSite=Lax; Domain=.getklai.com — readable by JS, mirrored in X-CSRF-Token."""

CSRF_HEADER_NAME = "X-CSRF-Token"
"""Header the frontend attaches to state-changing requests for double-submit CSRF."""

# Redis key prefix for session records. Deliberately short — Redis memory matters.
SESSION_KEY_PREFIX = "klai:session:"


@dataclass(frozen=True, slots=True)
class SessionContext:
    """
    Resolved authenticated session for the current HTTP request.

    Populated by :class:`app.middleware.session.SessionMiddleware` on
    `request.state.session`. Route handlers consume it via the
    `get_session` FastAPI dependency (see `app.api.session_deps`).

    All fields are already verified — handlers trust the values and do
    not re-validate tokens against Zitadel.
    """

    sid: str
    """Opaque session identifier (URL-safe base64, 32 bytes of randomness)."""

    zitadel_user_id: str
    """Subject claim from the ID token; foreign key to `portal_users.zitadel_user_id`."""

    access_token: str
    """Current Zitadel access token, used for downstream service calls."""

    csrf_token: str
    """Per-session CSRF token; must match the X-CSRF-Token header for mutations."""

    access_token_expires_at: int
    """Unix seconds when `access_token` stops being valid."""
