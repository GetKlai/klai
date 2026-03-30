"""
JWT validation for scribe-api.

Validates Zitadel access tokens independently using JWKS from the Zitadel issuer.
No dependency on portal-api. The `sub` claim is used as user_id.
"""
import logging

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.core.config import settings

logger = logging.getLogger(__name__)
bearer = HTTPBearer()

_jwks_cache: dict | None = None


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
        return user_id
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ongeldig of verlopen token",
        ) from exc
