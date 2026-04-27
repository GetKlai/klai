"""
JWT validation for scribe-api.

Validates Zitadel access tokens independently using JWKS from the Zitadel issuer.
No dependency on portal-api. The `sub` claim is used as user_id.
"""
import logging
import re

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


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> str:
    """Validate the Bearer token and return the Zitadel user ID (sub claim)."""
    token = credentials.credentials
    try:
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

        payload = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=settings.zitadel_issuer,
            options={"verify_aud": False},
        )
        user_id: str = payload.get("sub", "")
        if not user_id:
            raise JWTError("Missing sub claim")
        # SPEC-SEC-HYGIENE-001 REQ-34.1 — reject malformed sub at the auth
        # layer so downstream handlers (audio paths, SQL WHERE, log context)
        # never see arbitrary input. Defense-in-depth partner of HY-33.
        if not _ZITADEL_SUB_PATTERN.fullmatch(user_id):
            # Log the rejection (length only — never the value) so a 401
            # spike can be traced via `zitadel_sub_rejected` in VictoriaLogs.
            slog.warning("zitadel_sub_rejected", sub_length=len(user_id))
            raise JWTError("Malformed sub claim")
        return user_id
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ongeldig of verlopen token",
        ) from exc
