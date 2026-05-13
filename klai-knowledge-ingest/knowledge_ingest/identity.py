"""
Shared IdentityAsserter singleton for knowledge-ingest.

SPEC-TI-003 AC-6 / AC-8: identity-assertion is a tenant-binding layer
on top of InternalSecretMiddleware (network auth). Both must pass.

Pattern mirrors klai-retrieval-api/retrieval_api/middleware/auth.py
_get_asserter() / verify_body_identity(). Lazily instantiated on first
use so test environments that don't exercise internal-secret paths do
not pay the cost of an httpx.AsyncClient construction.
"""

from __future__ import annotations

import structlog
from fastapi import HTTPException, Request, status
from klai_identity_assert import (
    KNOWN_CALLER_SERVICES,
    IdentityAsserter,
    IdentityDenied,
    PortalUnreachable,
)

from knowledge_ingest.config import settings

logger = structlog.get_logger()

# @MX:ANCHOR: tenant identity-assertion entry point for knowledge-ingest routes
# @MX:REASON: Every route that reads org_id from body/query MUST call
#             assert_caller_identity() to replace body-trust with cryptographic
#             binding. SPEC-TI-003 AC-6 / AC-8.
_asserter: IdentityAsserter | None = None


def _get_asserter() -> IdentityAsserter:
    global _asserter
    if _asserter is None:
        _asserter = IdentityAsserter(
            portal_base_url=settings.portal_url,
            internal_secret=settings.portal_internal_token,
        )
    return _asserter


async def assert_caller_identity(
    request: Request,
    claimed_org_id: str,
    claimed_user_id: str,
) -> str:
    """Verify that the caller and the end-user are who they claim to be.

    Raises HTTPException(400) when X-Caller-Service is missing/unknown.
    Raises HTTPException(403) when portal denies the identity claim.
    Returns the verified org_id on success.

    ``claimed_user_id`` is required (no default). Routes that have no
    end-user MUST call :func:`assert_caller_identity_tenant_only` instead —
    this function is strictly for user-bound endpoints.

    AC-8: InternalSecretMiddleware (network auth) runs before this.
    This function adds the tenant-binding layer.
    """
    caller_service = request.headers.get("x-caller-service", "")
    if not caller_service or caller_service not in KNOWN_CALLER_SERVICES:
        logger.warning(
            "identity_assert_missing_caller_service",
            caller_service=caller_service,
            claimed_org_id=claimed_org_id,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "missing_caller_service"},
        )

    try:
        result = await _get_asserter().verify(
            caller_service=caller_service,
            claimed_user_id=claimed_user_id,
            claimed_org_id=claimed_org_id,
            bearer_jwt=None,
            request_headers=dict(request.headers),
        )
    except IdentityDenied as exc:
        logger.warning(
            "identity_assertion_denied",
            caller_service=caller_service,
            claimed_org_id=claimed_org_id,
            reason=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "identity_assertion_failed"},
        ) from exc
    except Exception as exc:
        logger.warning(
            "identity_assertion_portal_unreachable",
            caller_service=caller_service,
            claimed_org_id=claimed_org_id,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "identity_assertion_failed"},
        ) from exc

    if not result.verified:
        logger.warning(
            "identity_assertion_failed",
            caller_service=caller_service,
            claimed_org_id=claimed_org_id,
            reason=result.reason,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "identity_assertion_failed"},
        )

    logger.debug(
        "identity_assertion_ok",
        caller_service=caller_service,
        org_id=result.org_id or claimed_org_id,
    )
    return result.org_id or claimed_org_id


async def assert_caller_identity_tenant_only(
    request: Request,
    *,
    claimed_org_id: str,
) -> str:
    """Verify the tenant identity for service-to-service calls with no end-user.

    Used by stats endpoints (source-count, graph-stats) that are called by
    portal-api with only a tenant context — no end-user JWT and no user_id.

    Raises HTTPException(400) when X-Caller-Service is missing/unknown.
    Raises HTTPException(403) when portal denies the tenant identity claim.
    Returns the verified org_id on success.

    AC-8: InternalSecretMiddleware (network auth) runs before this.
    This function adds the tenant-binding layer WITHOUT a user identity check.
    """
    caller_service = request.headers.get("x-caller-service", "")
    if not caller_service or caller_service not in KNOWN_CALLER_SERVICES:
        logger.warning(
            "identity_assert_missing_caller_service",
            caller_service=caller_service,
            claimed_org_id=claimed_org_id,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "missing_caller_service"},
        )

    try:
        result = await _get_asserter().verify_tenant(
            caller_service=caller_service,
            claimed_org_id=claimed_org_id,
            request_headers=dict(request.headers),
        )
    except (IdentityDenied, PortalUnreachable) as exc:
        logger.warning(
            "identity_assertion_tenant_denied",
            caller_service=caller_service,
            claimed_org_id=claimed_org_id,
            reason=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "identity_assertion_failed"},
        ) from exc
    except Exception as exc:
        logger.warning(
            "identity_assertion_portal_unreachable",
            caller_service=caller_service,
            claimed_org_id=claimed_org_id,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "identity_assertion_failed"},
        ) from exc

    if not result.verified:
        logger.warning(
            "identity_assertion_tenant_failed",
            caller_service=caller_service,
            claimed_org_id=claimed_org_id,
            reason=result.reason,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "identity_assertion_failed"},
        )

    logger.debug(
        "identity_assertion_tenant_ok",
        caller_service=caller_service,
        org_id=result.org_id or claimed_org_id,
    )
    return result.org_id or claimed_org_id
