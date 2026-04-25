"""Identity verification service for portal-api /internal/identity/verify.

SPEC-SEC-IDENTITY-ASSERT-001 REQ-1: this is the source-of-truth implementation
of "is the claimed (user, org) tuple real". Every Klai service-to-service call
that carries an identity claim eventually reaches this function (via the
endpoint in :mod:`app.api.internal`).

Design responsibilities, in order:

1. **JWT path** (REQ-1.3) — when the caller forwarded the end-user JWT, decode
   and verify its signature against Zitadel JWKS, then assert
   ``jwt.sub == claimed_user_id`` AND ``jwt.resourceowner == claimed_org_id``.
   On mismatch return ``jwt_identity_mismatch``. On invalid signature/exp/aud
   return ``invalid_jwt`` — never fall through to the membership path.
2. **Membership path** (REQ-1.4) — when ``bearer_jwt`` is None, look up the
   user's active membership in ``portal_users`` keyed on
   ``(zitadel_user_id, zitadel_org_id, status='active')``. On match return
   ``evidence='membership'``; on miss return ``no_membership``.
3. **caller_service allowlist** (REQ-1.2) — anything not in the recognised
   list returns ``unknown_caller_service`` BEFORE any DB or JWT work.

Caching (REQ-1.5) is wrapped around this function by the endpoint layer
(:mod:`app.services.identity_verify_cache`); this service is cache-blind.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.portal import PortalOrg, PortalUser

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Recognised callers (REQ-1.2)
# ---------------------------------------------------------------------------

# Mirrors klai_identity_assert.KNOWN_CALLER_SERVICES exactly. Adding a caller
# requires a synchronised change in both locations; the library fails closed
# with ``library_misconfigured`` and the endpoint with
# ``unknown_caller_service`` so a one-sided change is loud.
KNOWN_CALLER_SERVICES: frozenset[str] = frozenset(
    {
        "knowledge-mcp",
        "scribe",
        "retrieval-api",
        "connector",
        "mailer",
    }
)


# ---------------------------------------------------------------------------
# JWT validation
# ---------------------------------------------------------------------------

# Stable Zitadel claim name for the user's primary org. Matches the constant
# at ``klai-retrieval-api/retrieval_api/middleware/auth.py``. Defining it here
# instead of importing keeps portal-api standalone — retrieval-api is not on
# our import path.
_ZITADEL_RESOURCEOWNER_CLAIM = "urn:zitadel:iam:user:resourceowner:id"


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

ReasonCode = Literal[
    "unknown_caller_service",
    "invalid_jwt",
    "jwt_identity_mismatch",
    "no_membership",
]
Evidence = Literal["jwt", "membership"]


@dataclass(frozen=True, slots=True)
class VerifyDecision:
    """Outcome of :func:`verify_identity_claim`.

    Mirrors ``klai_identity_assert.VerifyResult`` at the HTTP boundary —
    the endpoint layer maps this to the JSON body documented in REQ-1.1.
    """

    verified: bool
    user_id: str | None
    org_id: str | None
    reason: ReasonCode | None
    evidence: Evidence | None

    @classmethod
    def deny(cls, reason: ReasonCode) -> VerifyDecision:
        return cls(verified=False, user_id=None, org_id=None, reason=reason, evidence=None)

    @classmethod
    def allow(cls, *, user_id: str, org_id: str, evidence: Evidence) -> VerifyDecision:
        return cls(verified=True, user_id=user_id, org_id=org_id, reason=None, evidence=evidence)


# ---------------------------------------------------------------------------
# JWT validation
# ---------------------------------------------------------------------------


def _decode_user_jwt(bearer_jwt: str, jwks_resolver: JwksResolver) -> dict[str, Any] | None:
    """Verify an end-user JWT signature against Zitadel JWKS.

    Returns the decoded claim set on success; ``None`` on any failure
    (invalid signature, expired, malformed). The caller MUST treat ``None``
    as ``invalid_jwt`` and SHALL NOT fall through to the membership path
    (REQ-1.8).

    Audience is intentionally NOT validated here. Service-forwarded JWTs
    can come from various Zitadel-issued audiences (LibreChat, retrieval-api,
    etc.); the identity guarantees we need are sub + resourceowner +
    signature + exp. Audience-specific permission checks belong to the
    consuming service, not this guard.
    """

    try:
        signing_key = jwks_resolver.get_signing_key_from_jwt(bearer_jwt).key
        return jwt.decode(
            bearer_jwt,
            signing_key,
            algorithms=["RS256"],
            issuer=settings.zitadel_base_url,
            options={
                "require": ["sub", "iss", "exp"],
                "verify_aud": False,
            },
        )
    except jwt.PyJWTError as exc:
        logger.warning("identity_verify_jwt_invalid", extra={"error": str(exc)})
        return None


class JwksResolver:
    """Minimal duck-typing protocol for ``PyJWKClient``.

    Defining this lets tests inject a fake without depending on jwt internals.
    Production passes ``app.services.bff_oidc._get_jwks_client()`` which
    returns a real ``PyJWKClient``.
    """

    def get_signing_key_from_jwt(self, _token: str) -> Any:  # pragma: no cover - protocol
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Verification orchestrator
# ---------------------------------------------------------------------------


async def verify_identity_claim(
    *,
    db: AsyncSession,
    jwks_resolver: JwksResolver,
    caller_service: str,
    claimed_user_id: str,
    claimed_org_id: str,
    bearer_jwt: str | None,
) -> VerifyDecision:
    """Resolve a claimed identity to an authoritative allow/deny decision.

    The function is HTTP- and cache-agnostic; the endpoint layer wraps it.
    Exceptions thrown here are programmer errors (e.g. DB unreachable) and
    bubble up to the endpoint, which translates them to HTTP 503.
    """

    if caller_service not in KNOWN_CALLER_SERVICES:
        logger.info(
            "identity_verify_unknown_caller",
            extra={"caller_service": caller_service},
        )
        return VerifyDecision.deny("unknown_caller_service")

    if bearer_jwt is not None:
        claims = _decode_user_jwt(bearer_jwt, jwks_resolver)
        if claims is None:
            return VerifyDecision.deny("invalid_jwt")

        jwt_sub = claims.get("sub")
        jwt_resourceowner = claims.get(_ZITADEL_RESOURCEOWNER_CLAIM)
        if not isinstance(jwt_sub, str) or not isinstance(jwt_resourceowner, str):
            # Claims are present but not strings — treat as malformed JWT.
            return VerifyDecision.deny("invalid_jwt")

        if jwt_sub != claimed_user_id or jwt_resourceowner != claimed_org_id:
            logger.info(
                "identity_verify_jwt_mismatch",
                extra={
                    "caller_service": caller_service,
                    "claim_sub_matches": jwt_sub == claimed_user_id,
                    "claim_org_matches": jwt_resourceowner == claimed_org_id,
                },
            )
            return VerifyDecision.deny("jwt_identity_mismatch")

        return VerifyDecision.allow(
            user_id=claimed_user_id,
            org_id=claimed_org_id,
            evidence="jwt",
        )

    # bearer_jwt is None → fall through to membership lookup (REQ-1.4).
    member_exists = await _user_has_active_membership(
        db=db,
        zitadel_user_id=claimed_user_id,
        zitadel_org_id=claimed_org_id,
    )
    if not member_exists:
        return VerifyDecision.deny("no_membership")

    return VerifyDecision.allow(
        user_id=claimed_user_id,
        org_id=claimed_org_id,
        evidence="membership",
    )


async def _user_has_active_membership(
    *,
    db: AsyncSession,
    zitadel_user_id: str,
    zitadel_org_id: str,
) -> bool:
    """Return True iff the (user, org) pair has an active row in portal_users.

    The lookup joins ``portal_users`` on ``portal_orgs.zitadel_org_id`` so
    callers may pass the external Zitadel org ID directly — the integer
    portal org_id is internal-only and not exposed at this contract.
    """

    stmt = (
        select(PortalUser.id)
        .join(PortalOrg, PortalUser.org_id == PortalOrg.id)
        .where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalOrg.zitadel_org_id == zitadel_org_id,
            PortalUser.status == "active",
            PortalOrg.deleted_at.is_(None),
        )
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None
