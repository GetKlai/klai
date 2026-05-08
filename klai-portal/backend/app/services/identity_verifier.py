"""Identity verification service for portal-api /internal/identity/verify.

SPEC-SEC-IDENTITY-ASSERT-001 REQ-1 introduced this endpoint;
SPEC-SEC-IDENTITY-ASSERT-002 REQ-1 retired the JWT-resourceowner equality
check. Every Klai service-to-service call that carries an identity claim
eventually reaches this function (via the endpoint in
:mod:`app.api.internal`).

Design responsibilities, in order:

1. **JWT path** — when the caller forwarded the end-user JWT, decode and
   verify its signature against Zitadel JWKS, then assert
   ``jwt.sub == claimed_user_id``. On invalid signature/exp/sub-type
   return ``invalid_jwt`` — never fall through to the membership path.
   On sub mismatch return ``jwt_identity_mismatch``. The JWT's
   ``urn:zitadel:iam:user:resourceowner:id`` claim is intentionally
   ignored (SPEC-002 REQ-1.2): authoritative org resolution flows
   through portal_users membership, not the IdP-side claim.
2. **Membership path** — for both JWT-bound (after sub check) and
   JWT-less calls, look up the user's active membership in
   ``portal_users`` keyed on
   ``(zitadel_user_id, zitadel_org_id, status='active')``. On match
   return ``evidence='jwt'`` (JWT proof of authenticity) or
   ``evidence='membership'`` (no JWT presented). On miss return
   ``no_membership``.
3. **caller_service allowlist** (REQ-1.2) — anything not in the recognised
   list returns ``unknown_caller_service`` BEFORE any DB or JWT work.

Caching (REQ-1.5) is wrapped around this function by the endpoint layer
(:mod:`app.services.identity_verify_cache`); this service is cache-blind.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

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
#
# `litellm` and `portal-api` were added 2026-05-05 after the caller-service
# header check (SPEC-SEC-IDENTITY-ASSERT-001 Phase D, landed 2026-04-28)
# silently broke every internal caller of retrieval-api. See
# pitfalls/process-rules.md -> retrieve-caller-service-header-mismatch.
# `research-api` was removed 2026-05-XX per SPEC-DECOMM-FOCUS-001 — the
# service was decommissioned in SPEC-PORTAL-UNIFY-KB-001 and the entry
# was inert.
KNOWN_CALLER_SERVICES: frozenset[str] = frozenset(
    {
        "knowledge-mcp",
        "scribe",
        "retrieval-api",
        "connector",
        "mailer",
        "litellm",
        "portal-api",
    }
)


# ---------------------------------------------------------------------------
# JWT validation
# ---------------------------------------------------------------------------

# SPEC-SEC-IDENTITY-ASSERT-002 REQ-1.2: the
# `urn:zitadel:iam:user:resourceowner:id` claim is no longer consulted.
# Klai's BFF never requests the scope that would emit it, and the rule at
# `.claude/rules/klai/platform/zitadel.md:99-100` already documents the
# claim as unreliable. Authoritative org-resolution flows through
# portal_users membership. The constant was deliberately deleted to
# prevent re-introduction; CI ast-grep rule
# `rules/no-zitadel-resourceowner-claim.yml` (REQ-5) blocks new uses.


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

ReasonCode = Literal[
    "unknown_caller_service",
    "invalid_jwt",
    "jwt_identity_mismatch",
    "no_membership",
    "org_slug_mismatch",
    # F2 fix-forward (retrieval coupling audit 2026-05-06): partner-key paths.
    "partner_key_not_found",
    "partner_key_org_mismatch",
    # Tenant-only path: org not found in portal_orgs (no live row).
    "tenant_not_found",
]
UserBoundEvidence = Literal["jwt", "membership", "partner_key"]
Evidence = Literal["jwt", "membership", "partner_key", "tenant_only"]

# Synthetic identity prefix used by partner_chat for org-level RAG calls.
# SPEC-API-001 explicitly states partners "have no concept of end users",
# so we mint a synthetic id `partner:<partner_api_keys.id>` and route it
# through this service via the dedicated branch in `verify_identity_claim`.
# The check resolves the key against the partner_api_keys table and confirms
# that key.org_id maps to the claimed Zitadel org_id.
_PARTNER_USER_PREFIX = "partner:"


@dataclass(frozen=True, slots=True)
class VerifyDecision:
    """Outcome of :func:`verify_identity_claim`.

    Mirrors ``klai_identity_assert.VerifyResult`` at the HTTP boundary —
    the endpoint layer maps this to the JSON body documented in REQ-1.1.

    ``org_slug`` carries the canonical ``portal_orgs.slug`` for the verified
    org. Knowledge-mcp uses it to satisfy REQ-2.6 (reject when LibreChat-
    forwarded ``X-Org-Slug`` does not match the org the user is verified for).
    Always populated on allow; ``None`` on deny.
    """

    verified: bool
    user_id: str | None
    org_id: str | None
    org_slug: str | None
    reason: ReasonCode | None
    evidence: Evidence | None

    @classmethod
    def deny(cls, reason: ReasonCode) -> VerifyDecision:
        return cls(verified=False, user_id=None, org_id=None, org_slug=None, reason=reason, evidence=None)

    @classmethod
    def allow(cls, *, user_id: str, org_id: str, org_slug: str, evidence: Evidence) -> VerifyDecision:
        return cls(
            verified=True,
            user_id=user_id,
            org_id=org_id,
            org_slug=org_slug,
            reason=None,
            evidence=evidence,
        )

    @classmethod
    def allow_tenant(cls, *, org_id: str, org_slug: str) -> VerifyDecision:
        """Construct a verified tenant-only decision (no end-user identity).

        Used exclusively for service-to-service calls where there is no
        end-user (e.g. dashboard stats endpoints). The ``user_id`` field is
        intentionally ``None``; callers MUST NOT use this on user-bound
        endpoints — use :meth:`allow` instead.
        """
        return cls(
            verified=True,
            user_id=None,
            org_id=org_id,
            org_slug=org_slug,
            reason=None,
            evidence="tenant_only",
        )


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


@runtime_checkable
class JwksResolver(Protocol):
    """Structural type for any object that resolves a JWT signing key.

    The production implementation is ``jwt.PyJWKClient`` (instantiated by
    ``_get_identity_jwks_resolver`` in ``app.api.internal``). Tests inject
    a fake conforming to this protocol — no inheritance required.
    """

    def get_signing_key_from_jwt(self, token: str, /) -> Any: ...


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
    claimed_org_slug: str | None = None,
) -> VerifyDecision:
    """Resolve a claimed identity to an authoritative allow/deny decision.

    The function is HTTP- and cache-agnostic; the endpoint layer wraps it.
    Exceptions thrown here are programmer errors (e.g. DB unreachable) and
    bubble up to the endpoint, which translates them to HTTP 503.

    ``claimed_org_slug`` (REQ-2.6) is optional. When provided, the canonical
    ``portal_orgs.slug`` for the verified org must match it; mismatch yields
    ``org_slug_mismatch``. When ``None``, the slug is still resolved and
    returned so cache hits can perform the check without re-hitting the DB.
    """

    if caller_service not in KNOWN_CALLER_SERVICES:
        logger.info(
            "identity_verify_unknown_caller",
            extra={"caller_service": caller_service},
        )
        return VerifyDecision.deny("unknown_caller_service")

    # F2 fix-forward (retrieval coupling audit 2026-05-06): synthetic partner
    # identity. partner_chat sends `claimed_user_id="partner:<partner_api_keys.id>"`
    # and the claimed Zitadel org_id. The branch is delegated to
    # ``_verify_partner_claim`` to keep this orchestrator's complexity low.
    if claimed_user_id.startswith(_PARTNER_USER_PREFIX):
        return await _verify_partner_claim(
            db=db,
            caller_service=caller_service,
            claimed_user_id=claimed_user_id,
            claimed_org_id=claimed_org_id,
            claimed_org_slug=claimed_org_slug,
            bearer_jwt=bearer_jwt,
        )

    if bearer_jwt is not None:
        claims = _decode_user_jwt(bearer_jwt, jwks_resolver)
        if claims is None:
            return VerifyDecision.deny("invalid_jwt")

        jwt_sub = claims.get("sub")
        if not isinstance(jwt_sub, str):
            # Malformed JWT — sub absent or wrong type.
            return VerifyDecision.deny("invalid_jwt")

        if jwt_sub != claimed_user_id:
            # SPEC-SEC-IDENTITY-ASSERT-002 REQ-1.1: sub-equality is the
            # actual cross-user defence — keep it strict. Mismatched sub
            # means the caller forwarded somebody else's JWT while
            # claiming a different user identity.
            logger.info(
                "identity_verify_jwt_sub_mismatch",
                extra={"caller_service": caller_service},
            )
            return VerifyDecision.deny("jwt_identity_mismatch")

        # SPEC-SEC-IDENTITY-ASSERT-002 REQ-1.2 + REQ-1.3: the JWT's
        # `urn:zitadel:iam:user:resourceowner:id` claim is intentionally
        # NOT consulted. Klai's data-model allows users to be active members
        # of multiple orgs (portal_users has-many rows per zitadel_user_id);
        # the resourceowner claim refers to the user's PRIMARY Zitadel org
        # which can be unrelated to the org they're acting on. Authoritative
        # org-resolution flows through portal_users membership (the same
        # path the bearer_jwt=None branch uses below). One DB query
        # combines the membership signal and the canonical slug.
        org_slug = await _resolve_active_membership_org_slug(
            db=db,
            zitadel_user_id=claimed_user_id,
            zitadel_org_id=claimed_org_id,
        )
        if org_slug is None:
            return VerifyDecision.deny("no_membership")
        if claimed_org_slug is not None and claimed_org_slug != org_slug:
            return VerifyDecision.deny("org_slug_mismatch")
        return VerifyDecision.allow(
            user_id=claimed_user_id,
            org_id=claimed_org_id,
            org_slug=org_slug,
            evidence="jwt",
        )

    # bearer_jwt is None → fall through to membership lookup (REQ-1.4).
    org_slug = await _resolve_active_membership_org_slug(
        db=db,
        zitadel_user_id=claimed_user_id,
        zitadel_org_id=claimed_org_id,
    )
    if org_slug is None:
        return VerifyDecision.deny("no_membership")
    if claimed_org_slug is not None and claimed_org_slug != org_slug:
        return VerifyDecision.deny("org_slug_mismatch")

    return VerifyDecision.allow(
        user_id=claimed_user_id,
        org_id=claimed_org_id,
        org_slug=org_slug,
        evidence="membership",
    )


async def _verify_partner_claim(
    *,
    db: AsyncSession,
    caller_service: str,
    claimed_user_id: str,
    claimed_org_id: str,
    claimed_org_slug: str | None,
    bearer_jwt: str | None,
) -> VerifyDecision:
    """Verify a ``partner:<key_id>`` claim against ``partner_api_keys``.

    Caller-service is restricted to ``"portal-api"`` (only that service
    mints partner identities); other callers presenting a ``partner:``
    prefix are treated as misconfigured and denied.

    Bearer JWT + partner: prefix is rejected because partner keys are the
    only credential — mixing the two indicates a malformed call.

    F2 fix-forward (retrieval coupling audit 2026-05-06).
    """

    if caller_service != "portal-api":
        logger.info(
            "identity_verify_partner_wrong_caller",
            extra={"caller_service": caller_service},
        )
        return VerifyDecision.deny("partner_key_not_found")
    if bearer_jwt is not None:
        return VerifyDecision.deny("invalid_jwt")

    partner_key_id = claimed_user_id[len(_PARTNER_USER_PREFIX) :]
    org_slug, reason = await _resolve_partner_key_org_slug(
        db=db,
        partner_key_id=partner_key_id,
        claimed_zitadel_org_id=claimed_org_id,
    )
    if reason is not None:
        return VerifyDecision.deny(reason)
    # reason==None implies allow → org_slug is guaranteed non-None
    # by _resolve_partner_key_org_slug's contract.
    if org_slug is None:  # pragma: no cover — defensive type narrowing
        return VerifyDecision.deny("partner_key_not_found")
    if claimed_org_slug is not None and claimed_org_slug != org_slug:
        return VerifyDecision.deny("org_slug_mismatch")
    return VerifyDecision.allow(
        user_id=claimed_user_id,
        org_id=claimed_org_id,
        org_slug=org_slug,
        evidence="partner_key",
    )


async def _resolve_partner_key_org_slug(
    *,
    db: AsyncSession,
    partner_key_id: str,
    claimed_zitadel_org_id: str,
) -> tuple[str | None, ReasonCode | None]:
    """Validate a ``partner:<key_id>`` identity against ``partner_api_keys``.

    Returns ``(org_slug, None)`` when the key exists, the owning portal_orgs
    row is not soft-deleted, and ``portal_orgs.zitadel_org_id`` matches
    ``claimed_zitadel_org_id``.

    Returns ``(None, "partner_key_not_found")`` when the key does not exist,
    is malformed, or the owning org is soft-deleted.

    Returns ``(None, "partner_key_org_mismatch")`` when the key exists but
    its owning org's Zitadel id does not match the claimed value — defends
    against a forged claim that pairs a real partner key with a victim org.

    F2 fix-forward (retrieval coupling audit 2026-05-06).
    """

    from sqlalchemy.exc import DataError

    from app.models.partner_api_keys import PartnerAPIKey

    # Validate the partner_key_id is a sensible-length string before hitting
    # the DB. Fast reject saves a query on obviously-malformed inputs and
    # avoids forwarding garbage into asyncpg's UUID parser.
    if not partner_key_id or len(partner_key_id) > 64:
        return None, "partner_key_not_found"

    # partner_api_keys is RLS Category-B — SELECT works without tenant
    # context, which is essential because this lookup runs on the
    # /internal/identity/verify path BEFORE any tenant context is set.
    stmt = (
        select(PortalOrg.zitadel_org_id, PortalOrg.slug)
        .select_from(PartnerAPIKey)
        .join(PortalOrg, PortalOrg.id == PartnerAPIKey.org_id)
        .where(
            PartnerAPIKey.id == partner_key_id,
            PortalOrg.deleted_at.is_(None),
        )
        .limit(1)
    )
    try:
        result = await db.execute(stmt)
    except DataError:
        # Malformed UUID input surfaces as ``sqlalchemy.exc.DataError``
        # wrapping ``asyncpg.exceptions.DataError`` ("invalid input syntax for
        # type uuid"). This is caller-supplied garbage — treat as "not found"
        # rather than 503.
        logger.info("identity_verify_partner_malformed_uuid")
        return None, "partner_key_not_found"
    # NOTE: do NOT catch broader Exception here. A real DB outage
    # (OperationalError, connection refused) MUST bubble up so the
    # /internal/identity/verify endpoint converts it to HTTP 503
    # cache_unavailable rather than masquerading as a partner_key
    # rejection. Audit: post-F2 polish 2026-05-06.

    row = result.one_or_none()
    if row is None:
        return None, "partner_key_not_found"

    actual_zitadel_org_id, org_slug = row
    if actual_zitadel_org_id != claimed_zitadel_org_id:
        # Real key, wrong org — log loudly because this is the shape of
        # a deliberate cross-tenant probe.
        logger.warning(
            "identity_verify_partner_org_mismatch",
            extra={
                "claimed_zitadel_org_id": claimed_zitadel_org_id,
                "actual_zitadel_org_id_hash": actual_zitadel_org_id[:8] + "…",
            },
        )
        return None, "partner_key_org_mismatch"

    return org_slug, None


async def _resolve_active_membership_org_slug(
    *,
    db: AsyncSession,
    zitadel_user_id: str,
    zitadel_org_id: str,
) -> str | None:
    """Return the canonical ``portal_orgs.slug`` iff the user is an active member.

    Combines the Phase A active-membership check with the Phase B slug lookup
    in one query: a single SELECT yields both signals (membership row exists
    and which slug the org carries). Returns ``None`` when the user has no
    active membership in the org or the org is soft-deleted.
    """

    stmt = (
        select(PortalOrg.slug)
        .join(PortalUser, PortalUser.org_id == PortalOrg.id)
        .where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalOrg.zitadel_org_id == zitadel_org_id,
            PortalUser.status == "active",
            PortalOrg.deleted_at.is_(None),
        )
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _resolve_org_slug(
    *,
    db: AsyncSession,
    zitadel_org_id: str,
) -> str | None:
    """Return the canonical ``portal_orgs.slug`` for an org regardless of caller.

    Used on the JWT path where the JWT proves user-org binding directly and
    no membership row need be checked; we only need the canonical slug to
    return alongside the verified result. Returns ``None`` when the org row
    is missing or soft-deleted.
    """

    stmt = (
        select(PortalOrg.slug)
        .where(
            PortalOrg.zitadel_org_id == zitadel_org_id,
            PortalOrg.deleted_at.is_(None),
        )
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


# SPEC-SEC-IDENTITY-ASSERT-002 REQ-6.1: evidence_path is a structlog field
# that exposes the FULL resolution path used to verify the identity. It
# is derived from VerifyDecision.evidence so a future split (e.g. a
# non-membership JWT path) is visible in logs without re-deriving the
# query plan. Values:
#
#   "jwt+membership"   JWT signature + sub-equality + portal_users membership
#                      (the SPEC-002 default for JWT-bound calls)
#   "membership"       bearer_jwt=None path; portal_users membership only
#   "partner_key"      partner:<key_id> claim verified against partner_api_keys
#   "tenant_only"      service-to-service tenant claim (no end-user)
_EVIDENCE_PATH_BY_EVIDENCE: dict[str, str] = {
    "jwt": "jwt+membership",
    "membership": "membership",
    "partner_key": "partner_key",
    "tenant_only": "tenant_only",
}


def evidence_path(evidence: str | None) -> str:
    """Map ``VerifyDecision.evidence`` to the public ``evidence_path`` log field.

    Returns ``"unknown"`` if the evidence string is not recognised. That
    case is impossible per the dataclass contract but the helper keeps
    the log line populated under a future evidence-type addition that
    forgets to update this mapping.
    """

    if evidence is None:
        return "unknown"
    return _EVIDENCE_PATH_BY_EVIDENCE.get(evidence, "unknown")


async def verify_bff_session_identity(
    *,
    db: AsyncSession,
    zitadel_user_id: str,
    portal_org_id: int,
) -> VerifyDecision:
    """Membership-authoritative verification for portal-api BFF proxy calls.

    Used by :mod:`app.api.proxy` when forwarding `/api/scribe/*` and
    `/api/docs/*` to upstream services. The BFF session was already
    authenticated at cookie-decode time (the access_token was JWT-validated
    at session creation/refresh). This function adds defence-in-depth by
    re-checking that the user STILL has an active membership in the
    portal_org_id stored in the session, AND resolves the canonical
    (zitadel_org_id, org_slug) pair needed for the downstream
    ``X-Klai-Verified-*`` headers.

    Single SELECT joining portal_users and portal_orgs on the portal-side
    primary key (``portal_orgs.id``) — different from
    :func:`_resolve_active_membership_org_slug` which joins on
    ``zitadel_org_id`` for the public-API path.

    Returns the same :class:`VerifyDecision` shape as
    :func:`verify_identity_claim` so the proxy can use a uniform
    allow/deny pattern. ``evidence='jwt'`` because the session's
    access_token was JWT-validated at session creation; the membership
    re-check here is an authorisation defence, not an authentication one.
    """

    stmt = (
        select(PortalOrg.zitadel_org_id, PortalOrg.slug)
        .join(PortalUser, PortalUser.org_id == PortalOrg.id)
        .where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalOrg.id == portal_org_id,
            PortalUser.status == "active",
            PortalOrg.deleted_at.is_(None),
        )
        .limit(1)
    )
    result = await db.execute(stmt)
    row = result.one_or_none()
    if row is None:
        return VerifyDecision.deny("no_membership")
    zitadel_org_id, org_slug = row
    return VerifyDecision.allow(
        user_id=zitadel_user_id,
        org_id=zitadel_org_id,
        org_slug=org_slug,
        evidence="jwt",
    )


async def verify_tenant_claim(
    *,
    db: AsyncSession,
    caller_service: str,
    claimed_org_id: str,
    claimed_org_slug: str | None = None,
) -> VerifyDecision:
    """Resolve a tenant-only identity claim to an authoritative allow/deny decision.

    No JWT and no ``claimed_user_id`` — this path is for service-to-service
    calls where there is no end-user (e.g. portal-api → knowledge-ingest stats
    endpoints). The function is HTTP- and cache-agnostic; the endpoint layer
    wraps it.

    Does NOT touch ``verify_identity_claim`` — the two code paths are fully
    independent so a future caller that accidentally passes ``None`` as
    ``claimed_user_id`` cannot silently fall through to a tenant-only result.
    """

    if caller_service not in KNOWN_CALLER_SERVICES:
        logger.info(
            "identity_verify_tenant_unknown_caller",
            extra={"caller_service": caller_service},
        )
        return VerifyDecision.deny("unknown_caller_service")

    org_slug = await _resolve_org_slug(db=db, zitadel_org_id=claimed_org_id)
    if org_slug is None:
        return VerifyDecision.deny("tenant_not_found")
    if claimed_org_slug is not None and claimed_org_slug != org_slug:
        return VerifyDecision.deny("org_slug_mismatch")

    return VerifyDecision.allow_tenant(org_id=claimed_org_id, org_slug=org_slug)
