"""Shared identity-assertion helper for Klai service-to-service calls.

SPEC-SEC-IDENTITY-ASSERT-001 — consolidates the identity-verify call into one
implementation that every Python consumer (knowledge-mcp, scribe,
retrieval-api, mailer, connector) imports. Services do not re-implement the
contract; they use this library or they fail review.

Public API (all re-exported at package root):

- :class:`IdentityAsserter` — async client; instantiate once per process
- :class:`VerifyResult` — frozen dataclass returned by every verify call
- :class:`IdentityAssertError` — base exception
- :class:`PortalUnreachable` — network/5xx failure (raise mode only)
- :class:`IdentityDenied` — portal returned verified=false (raise mode only)
- :data:`KNOWN_CALLER_SERVICES` — frozenset, mirrors portal allowlist

Quickstart
----------

::

    from klai_identity_assert import IdentityAsserter, VerifyResult

    asserter = IdentityAsserter(
        portal_base_url=settings.portal_base_url,
        internal_secret=settings.internal_secret,
    )

    result = await asserter.verify(
        caller_service="scribe",
        claimed_user_id=user_id,
        claimed_org_id=org_id,
        bearer_jwt=jwt_or_none,
        request_headers=trace_headers,  # propagates X-Request-ID
    )
    if not result.verified:
        raise HTTPException(403, detail="identity_assertion_failed")
"""

from klai_identity_assert.client import IdentityAsserter
from klai_identity_assert.exceptions import (
    IdentityAssertError,
    IdentityDenied,
    PortalUnreachable,
)
from klai_identity_assert.models import (
    KNOWN_CALLER_SERVICES,
    Evidence,
    ReasonCode,
    VerifyResult,
)

__all__ = [
    "KNOWN_CALLER_SERVICES",
    "Evidence",
    "IdentityAssertError",
    "IdentityAsserter",
    "IdentityDenied",
    "PortalUnreachable",
    "ReasonCode",
    "VerifyResult",
]
