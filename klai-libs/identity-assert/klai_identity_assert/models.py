"""Result and request types for the identity-assert helper.

SPEC-SEC-IDENTITY-ASSERT-001 REQ-7.1: ``VerifyResult`` is a frozen dataclass
returned by every call to :class:`IdentityAsserter.verify`. Consumers branch
on ``verified`` and may surface ``reason`` in operator-facing logs (never to
end-user clients — see REQ-2.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Stable evidence types — mirrors portal-api REQ-1.3 / REQ-1.4 contract.
# ``partner_key`` (F2 fix-forward, retrieval coupling audit 2026-05-06):
# evidence used for synthetic ``partner:<key_id>`` identities verified
# against the partner_api_keys table by portal-api.
Evidence = Literal["jwt", "membership", "partner_key"]

# Stable reject codes — mirrors portal-api REQ-1.7 stable_code list. Plus two
# consumer-side codes the library raises before ever reaching portal:
#   - "portal_unreachable": network or 5xx error against /internal/identity/verify
#   - "library_misconfigured": SDK config invalid (caller passed an unknown service)
ReasonCode = Literal[
    "unknown_caller_service",
    "invalid_jwt",
    "jwt_identity_mismatch",
    "no_membership",
    "org_slug_mismatch",
    "cache_unavailable",
    "portal_unreachable",
    "library_misconfigured",
    # F2 fix-forward (retrieval coupling audit 2026-05-06): partner-key paths.
    "partner_key_not_found",
    "partner_key_org_mismatch",
]


@dataclass(frozen=True, slots=True)
class VerifyResult:
    """Outcome of a single identity-assertion call.

    Attributes
    ----------
    verified:
        ``True`` only when the portal returned 200 + verified=true. Any other
        outcome (4xx, 5xx, network error, cache miss-followed-by-failure) is
        ``False`` — the caller MUST refuse the upstream operation.
    user_id, org_id:
        Canonical resolved identity from portal. Both populated when
        ``verified`` is True. Both ``None`` on deny.
    org_slug:
        Canonical ``portal_orgs.slug`` for the verified org (REQ-2.6).
        Always populated on allow — callers SHOULD use this when constructing
        upstream URLs (e.g. klai-docs ``/api/orgs/{org_slug}/...``) instead
        of trusting the caller-asserted ``X-Org-Slug`` header. ``None`` on
        deny.
    reason:
        Stable code on deny (see :data:`ReasonCode`). ``None`` on allow.
    evidence:
        ``"jwt"`` when the verification rested on a fresh JWT validation,
        ``"membership"`` when the fallback membership lookup was decisive
        (used when the caller passed ``bearer_jwt=None``). ``None`` on deny.
    cached:
        ``True`` when this result was returned from the consumer-side LRU
        cache (REQ-7.2). ``False`` on cache miss / live portal call.
    """

    verified: bool
    user_id: str | None
    org_id: str | None
    org_slug: str | None
    reason: ReasonCode | None
    evidence: Evidence | None
    cached: bool

    @classmethod
    def deny(cls, reason: ReasonCode) -> VerifyResult:
        """Construct a non-verified result with a stable reason code."""
        return cls(
            verified=False,
            user_id=None,
            org_id=None,
            org_slug=None,
            reason=reason,
            evidence=None,
            cached=False,
        )

    @classmethod
    def allow(
        cls,
        *,
        user_id: str,
        org_id: str,
        org_slug: str,
        evidence: Evidence,
        cached: bool = False,
    ) -> VerifyResult:
        """Construct a verified result with the canonical resolved identity."""
        return cls(
            verified=True,
            user_id=user_id,
            org_id=org_id,
            org_slug=org_slug,
            reason=None,
            evidence=evidence,
            cached=cached,
        )


# Recognised caller services. Mirrors portal-api REQ-1.2 reject list. Adding a
# new caller requires a synchronised change to portal-api's allowlist; consumers
# fail-closed if they pass an unknown service identifier.
#
# `litellm` and `portal-api` were added 2026-05-05 after the caller-service
# header check (SPEC-SEC-IDENTITY-ASSERT-001 Phase D, landed 2026-04-28)
# silently broke every internal caller of retrieval-api `/retrieve`. The
# fail-open in those callers degraded chat to "no KB" without surfacing a
# single error for 7 days. See pitfalls/process-rules.md →
# retrieve-caller-service-header-mismatch.
#
# `research-api` was removed 2026-05-XX per SPEC-DECOMM-FOCUS-001. The service
# was decommissioned in SPEC-PORTAL-UNIFY-KB-001 (April 2026); the allowlist
# entry added on 2026-05-05 was inert defensive code on a service that no
# longer runs.
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
