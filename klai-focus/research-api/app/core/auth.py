"""JWT validation for research-api.

Validates Zitadel access tokens independently using JWKS from the Zitadel issuer.
Extracts user_id (sub) and resolves tenant_id from the JWT resourceowner claim.

A-12 fix (SPEC-TI-004-RLS-RESEARCH): _get_user_org now uses JWT
urn:zitadel:iam:org:project:resourceowner as the authoritative tenant selector.
Multi-org users have one portal_users row per org; without the resourceowner
filter the query returned an arbitrary row (whichever the DB chose first),
silently landing the user in the wrong tenant.
"""

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db, set_tenant

bearer = HTTPBearer()

_jwks_cache: dict | None = None

# JWT claim name for the Zitadel organization that issued this token.
# The value is the Zitadel org ID (string UUID) for the org the user
# authenticated against — the authoritative tenant selector for multi-org users.
_RESOURCEOWNER_CLAIM = "urn:zitadel:iam:org:project:resourceowner"


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

        # SPEC-SEC-012: audience is mandatory. Settings validator guarantees
        # settings.zitadel_api_audience is non-empty; no conditional branch.
        decode_kwargs: dict = {
            "algorithms": ["RS256"],
            "issuer": settings.zitadel_issuer,
            "audience": settings.zitadel_api_audience,
        }

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
    """Validate Bearer token, resolve tenant_id, return CurrentUser.

    A-12 fix: uses JWT resourceowner claim to select the correct portal_users
    row for multi-org users. Without this fix, the query returned an arbitrary
    row for users who belong to more than one organization (portal_users has a
    UniqueConstraint on (zitadel_user_id, org_id), not on zitadel_user_id alone).

    Flow:
    1. Decode and validate JWT via Zitadel JWKS.
    2. Extract `sub` (user ID) and `urn:zitadel:iam:org:project:resourceowner`
       (the Zitadel org ID the token was issued for).
    3. Look up portal_users JOIN portal_orgs WHERE both match.
    4. If no row found → 403 (user_not_in_resourceowner_tenant).
    5. Call set_tenant(db, zitadel_org_id) so RLS is enforced for subsequent
       queries in this request.
    """
    payload = await _decode_token(credentials.credentials)

    user_id: str = payload.get("sub", "")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing sub claim")

    # A-12: resourceowner claim is the authoritative tenant selector.
    # No fall-back to "first row" — that is the bug we are fixing.
    resourceowner_id: str = payload.get(_RESOURCEOWNER_CLAIM, "")
    if not resourceowner_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing resourceowner claim — cannot determine tenant",
        )

    # Look up the portal_users row that matches BOTH the user AND the org
    # the JWT was issued for. LIMIT 1 is for safety; the UniqueConstraint
    # on (zitadel_user_id, org_id) means at most one row matches.
    result = await db.execute(
        text(
            "SELECT pu.org_id, po.zitadel_org_id"
            " FROM portal_users pu"
            " JOIN portal_orgs po ON po.id = pu.org_id"
            " WHERE pu.zitadel_user_id = :uid"
            "   AND po.zitadel_org_id = :rid"
            " LIMIT 1"
        ),
        {"uid": user_id, "rid": resourceowner_id},
    )
    org = result.fetchone()
    if org is None:
        # User exists in Zitadel but has no portal_users row for this org.
        # This happens if the user was removed from the org or the JWT was
        # issued by an org that does not match any klai tenant.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="user_not_in_resourceowner_tenant",
        )

    # zitadel_org_id is the stable tenant identifier used throughout research-api.
    # It is a UUID in the research schema (tenant_id columns are UUID).
    zitadel_org_id: str = str(org[1])
    tenant_id: str = zitadel_org_id

    # Set RLS tenant context for all subsequent queries in this request.
    # research._rls_current_org_id() reads app.current_tenant_id and returns
    # it as uuid — must be called before any ORM query on research.* tables.
    await set_tenant(db, tenant_id)

    # Extract roles from JWT (custom claim set by Zitadel)
    roles_claim = payload.get("urn:zitadel:iam:org:project:roles", {})
    roles = list(roles_claim.keys()) if isinstance(roles_claim, dict) else []

    return CurrentUser(
        user_id=user_id,
        tenant_id=tenant_id,
        zitadel_org_id=zitadel_org_id,
        roles=roles,
    )
