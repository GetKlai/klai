"""
JWT validation for scribe-api.

Validates Zitadel access tokens independently using JWKS from the Zitadel issuer.
No dependency on portal-api for the JWT path. The `sub` claim is used as
user_id; the `urn:zitadel:iam:user:resourceowner:id` claim is the primary
org and is consumed by the SPEC-SEC-IDENTITY-ASSERT-001 REQ-3 ingest path.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx
import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.core.config import settings

# Stdlib logger kept for back-compat with existing call sites; structlog
# logger added for the new HY-34 rejection event so VictoriaLogs can grep
# for `zitadel_sub_rejected` to investigate 401 spikes.
logger = logging.getLogger(__name__)
slog = structlog.get_logger(__name__)
bearer = HTTPBearer()

_jwks_cache: dict | None = None

# @MX:ANCHOR fan_in=multiple
# @MX:REASON: SPEC-SEC-HYGIENE-001 REQ-34. The `sub` claim flows downstream
# into audio-path construction (HY-33), SQL WHERE clauses, and structlog
# context. Defense-in-depth: HY-33's `_safe_audio_path` catches traversal
# even if this regex is widened; this regex catches malformed sub even if
# a new writer bypasses the path helper.
#
# Format: Zitadel default sub is a 19-20 digit numeric string. UUID-style
# subs (with dashes) and short alphanumeric+underscore subs are also valid.
# If a future auth flow (custom IdP, SAML federation) needs additional
# characters, REVISIT this pattern. See Zitadel sub format reference:
# https://zitadel.com/docs/apis/openidoauth/claims#standard-claims
_ZITADEL_SUB_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# SPEC-SEC-IDENTITY-ASSERT-001 REQ-3: Zitadel claim carrying the user's
# primary org ID. Mirrors the constant in
# klai-portal/backend/app/services/identity_verifier.py.
_ZITADEL_RESOURCEOWNER_CLAIM = "urn:zitadel:iam:user:resourceowner:id"


@dataclass(frozen=True, slots=True)
class CallerIdentity:
    """Authenticated caller derived from a verified Zitadel access token.

    SPEC-SEC-IDENTITY-ASSERT-001 REQ-3.5: ``org_id`` comes from the JWT's
    ``resourceowner`` claim. Because the JWT signature is validated here,
    the caller cannot tamper the value without invalidating the token —
    so the value is trustworthy directly, no portal-api round-trip needed.
    """

    user_id: str
    org_id: str


async def _fetch_jwks() -> dict:
    jwks_url = f"{settings.zitadel_issuer}/oauth/v2/keys"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(jwks_url)
        resp.raise_for_status()
        return resp.json()


async def _get_jwks(force_refresh: bool = False) -> dict:
    global _jwks_cache
    if _jwks_cache is None or force_refresh:
        _jwks_cache = await _fetch_jwks()
    return _jwks_cache


def _find_key(jwks: dict, kid: str | None) -> dict | None:
    for k in jwks.get("keys", []):
        if kid is None or k.get("kid") == kid:
            return k
    return None


async def _decode_zitadel_token(token: str) -> dict:
    """Validate JWT signature against Zitadel JWKS and return the claim set.

    Shared between :func:`get_current_user_id` (returns sub) and
    :func:`get_authenticated_caller` (returns sub + resourceowner). Raises
    :class:`JWTError` on any signature/issuer/format failure — callers map
    that to HTTP 401.
    """
    header = jwt.get_unverified_header(token)
    kid = header.get("kid")

    jwks = await _get_jwks()
    key = _find_key(jwks, kid)

    # Key rotation: if kid not found, refresh JWKS once and retry
    if key is None:
        jwks = await _get_jwks(force_refresh=True)
        key = _find_key(jwks, kid)

    if key is None:
        raise JWTError("Signing key not found in JWKS")

    return jwt.decode(
        token,
        key,
        algorithms=["RS256"],
        issuer=settings.zitadel_issuer,
        options={"verify_aud": False},
    )


def _validate_sub(user_id: str) -> str:
    """REQ-34.1 sub-claim charset whitelist."""
    if not user_id:
        raise JWTError("Missing sub claim")
    if not _ZITADEL_SUB_PATTERN.fullmatch(user_id):
        slog.warning("zitadel_sub_rejected", sub_length=len(user_id))
        raise JWTError("Malformed sub claim")
    return user_id


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> str:
    """Validate the Bearer token and return the Zitadel user ID (sub claim)."""
    try:
        payload = await _decode_zitadel_token(credentials.credentials)
        return _validate_sub(payload.get("sub", ""))
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ongeldig of verlopen token",
        ) from exc


async def get_authenticated_caller(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> CallerIdentity:
    """Return ``(user_id, org_id)`` extracted from a verified Zitadel access token.

    SPEC-SEC-IDENTITY-ASSERT-001 REQ-3 / REQ-3.5: instead of trusting an
    ``org_id`` field in the request body (the S1 finding in spec.md), the
    handler derives the org from the JWT's ``resourceowner`` claim. The
    JWT signature is validated against Zitadel JWKS here, so the value is
    cryptographically authentic — no portal-api round-trip needed for the
    common case of a user acting in their primary org.

    A JWT without a ``resourceowner`` claim means the user has no active
    org membership in Zitadel; the endpoint MUST reject with 403
    ``no_active_org_membership`` (REQ-3.4).
    """
    try:
        payload = await _decode_zitadel_token(credentials.credentials)
        user_id = _validate_sub(payload.get("sub", ""))
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ongeldig of verlopen token",
        ) from exc

    org_id = payload.get(_ZITADEL_RESOURCEOWNER_CLAIM, "")
    if not isinstance(org_id, str) or not org_id:
        # REQ-3.4: a Zitadel user without resourceowner has no active org
        # membership. Fail closed — the caller cannot pick a tenant
        # arbitrarily, and we do not silently downgrade to a default org.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="no_active_org_membership",
        )
    return CallerIdentity(user_id=user_id, org_id=org_id)
