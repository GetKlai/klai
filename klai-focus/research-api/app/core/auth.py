"""
JWT validation for research-api.

Validates Zitadel access tokens independently using JWKS from the Zitadel issuer.
Extracts user_id (sub) and resolves tenant_id from the Zitadel org claim.
"""
import logging

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db

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


async def _decode_token(token: str) -> dict:
    """Decode and validate a Zitadel JWT. Returns the full payload."""
    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")

        jwks = await _get_jwks()
        key = _find_key(jwks, kid)

        if key is None:
            jwks = await _get_jwks(force_refresh=True)
            key = _find_key(jwks, kid)

        if key is None:
            raise JWTError("Signing key not found in JWKS")

        decode_kwargs: dict = {
            "algorithms": ["RS256"],
            "issuer": settings.zitadel_issuer,
        }
        if settings.zitadel_api_audience:
            decode_kwargs["audience"] = settings.zitadel_api_audience
        else:
            logger.error(
                "ZITADEL_API_AUDIENCE not set — JWT audience verification is DISABLED. "
                "Set RESEARCH_API_ZITADEL_AUDIENCE in .env to the Zitadel project ID."
            )
            decode_kwargs["options"] = {"verify_aud": False}

        payload = jwt.decode(token, key, **decode_kwargs)
        return payload
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ongeldig of verlopen token",
        ) from exc


class CurrentUser:
    def __init__(self, user_id: str, tenant_id: str, zitadel_org_id: str, roles: list[str]):
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.zitadel_org_id = zitadel_org_id
        self.roles = roles

    def is_org_admin(self) -> bool:
        return "org_admin" in self.roles

    def can_upload(self) -> bool:
        return "uploader" in self.roles or self.is_org_admin()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    """Validate Bearer token, resolve tenant_id, return CurrentUser."""
    payload = await _decode_token(credentials.credentials)

    user_id: str = payload.get("sub", "")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing sub claim")

    # Resolve tenant via portal_users (org claim is not present in the portal OIDC token)
    row = await db.execute(
        text(
            "SELECT pu.org_id, po.zitadel_org_id"
            " FROM portal_users pu"
            " JOIN portal_orgs po ON po.id = pu.org_id"
            " WHERE pu.zitadel_user_id = :uid"
        ),
        {"uid": user_id},
    )
    org = row.fetchone()
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Gebruiker niet gevonden",
        )

    tenant_id: str = str(org[1])   # zitadel_org_id — stable tenant identifier
    zitadel_org_id: str = str(org[1])

    # Extract roles from JWT (custom claim set by Zitadel)
    roles_claim = payload.get("urn:zitadel:iam:org:project:roles", {})
    roles = list(roles_claim.keys()) if isinstance(roles_claim, dict) else []

    return CurrentUser(
        user_id=user_id,
        tenant_id=tenant_id,
        zitadel_org_id=zitadel_org_id,
        roles=roles,
    )
