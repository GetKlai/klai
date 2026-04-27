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
Evidence = Literal["jwt", "membership"]

# Stable reject codes — mirrors portal-api REQ-1.7 stable_code list. Plus two
# consumer-side codes the library raises before ever reaching portal:
#   - "portal_unreachable": network or 5xx error against /internal/identity/verify
#   - "library_misconfigured": SDK config invalid (caller passed an unknown service)
ReasonCode = Literal[
    "unknown_caller_service",
    "invalid_jwt",
    "jwt_identity_mismatch",
    "no_membership",
    "cache_unavailable",
    "portal_unreachable",
    "library_misconfigured",
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
    reason: ReasonCode | None
    evidence: Evidence | None
    cached: bool

    @classmethod
    def deny(cls, reason: ReasonCode) -> VerifyResult:
        """Construct a non-verified result with a stable reason code."""
        return cls(verified=False, user_id=None, org_id=None, reason=reason, evidence=None, cached=False)

    @classmethod
    def allow(
        cls,
        *,
        user_id: str,
        org_id: str,
        evidence: Evidence,
        cached: bool = False,
    ) -> VerifyResult:
        """Construct a verified result with the canonical resolved identity."""
        return cls(
            verified=True,
            user_id=user_id,
            org_id=org_id,
            reason=None,
            evidence=evidence,
            cached=cached,
        )


# Recognised caller services. Mirrors portal-api REQ-1.2 reject list. Adding a
# new caller requires a synchronised change to portal-api's allowlist; consumers
# fail-closed if they pass an unknown service identifier.
KNOWN_CALLER_SERVICES: frozenset[str] = frozenset(
    {
        "knowledge-mcp",
        "scribe",
        "retrieval-api",
        "connector",
        "mailer",
    }
)
