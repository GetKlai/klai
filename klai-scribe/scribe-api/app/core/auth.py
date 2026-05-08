"""scribe-api authentication.

SPEC-SEC-IDENTITY-ASSERT-002 REQ-3: scribe-api accepts ONLY requests that
arrive via portal-api's BFF proxy. The proxy verifies identity in-process
(via :func:`app.services.identity_verifier.verify_bff_session_identity`)
and asserts the result via three headers gated by ``X-Internal-Secret``:

    X-Internal-Secret:        portal-api's shared secret
                              (matches ``settings.portal_internal_secret``)
    X-Klai-Verified-User-Id:  Zitadel ``sub`` of the authenticated user
    X-Klai-Verified-Org-Id:   Canonical Zitadel org id resolved from
                              portal_users + portal_orgs

Any deviation (missing header, empty value, secret mismatch) returns HTTP
401 with body ``{"detail": "unauthenticated"}`` and no information leakage.

The previous JWT-decode + portal-roundtrip path
(SPEC-SEC-AUDIT-2026-04 B1) is retired. Portal-api is now the single
identity verifier; scribe-api is a downstream consumer.

The ``Authorization: Bearer <jwt>`` header that portal-api forwards is
NOT read by this module. scribe MAY forward it to downstream providers
(Vexa, Whisper) for their own auth, but it is never consulted for
identity decisions here.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

import structlog
from fastapi import Header, HTTPException, status

from app.core.config import settings

slog = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CallerIdentity:
    """Authenticated caller, sourced from portal-api BFF verified headers.

    SPEC-SEC-IDENTITY-ASSERT-002: ``user_id`` is the Zitadel ``sub`` and
    ``org_id`` is the canonical Zitadel org id resolved by portal-api's
    membership lookup. Both values are trusted because the request was
    gated by ``X-Internal-Secret``.
    """

    user_id: str
    org_id: str


def _const_eq(a: str, b: str) -> bool:
    """Constant-time string comparison for secret-bearing values."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


async def get_authenticated_caller(
    x_internal_secret: str | None = Header(default=None, alias="X-Internal-Secret"),
    x_klai_verified_user_id: str | None = Header(default=None, alias="X-Klai-Verified-User-Id"),
    x_klai_verified_org_id: str | None = Header(default=None, alias="X-Klai-Verified-Org-Id"),
) -> CallerIdentity:
    """Return ``CallerIdentity`` from portal-verified BFF headers.

    SPEC-SEC-IDENTITY-ASSERT-002 REQ-3.1: scribe-api accepts only
    BFF-proxied requests. The contract is:

    1. ``X-Internal-Secret`` matches ``settings.portal_internal_secret``
       (constant-time comparison).
    2. ``X-Klai-Verified-User-Id`` and ``X-Klai-Verified-Org-Id`` are both
       present and non-empty.

    Any deviation returns HTTP 401 with body ``{"detail":
    "unauthenticated"}``. The headers are inspected exactly once per
    request via FastAPI ``Header`` dependencies; missing values arrive as
    ``None`` and empty strings are treated identically.
    """
    if not x_internal_secret or not x_klai_verified_user_id or not x_klai_verified_org_id:
        slog.warning(
            "scribe_auth_missing_bff_header",
            has_secret=bool(x_internal_secret),
            has_user_id=bool(x_klai_verified_user_id),
            has_org_id=bool(x_klai_verified_org_id),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthenticated",
        )

    expected_secret = settings.portal_internal_secret
    if not _const_eq(x_internal_secret, expected_secret):
        slog.warning("scribe_auth_internal_secret_mismatch")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthenticated",
        )

    return CallerIdentity(
        user_id=x_klai_verified_user_id,
        org_id=x_klai_verified_org_id,
    )
