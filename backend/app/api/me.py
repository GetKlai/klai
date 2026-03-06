"""
GET /api/me

Validates the OIDC access token forwarded by the frontend and returns
the current user's profile + org info from Zitadel.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.services.zitadel import zitadel

router = APIRouter(prefix="/api", tags=["auth"])
bearer = HTTPBearer()


class MeResponse(BaseModel):
    user_id: str
    email: str
    name: str
    org_id: str | None = None
    roles: list[str] = []


def _extract_roles(info: dict) -> list[str]:
    """Extract project role names from Zitadel userinfo claims.

    Zitadel encodes roles as:
    "urn:zitadel:iam:org:project:roles": {"org:owner": {"orgId": "orgName"}}
    """
    raw = info.get("urn:zitadel:iam:org:project:roles", {})
    if isinstance(raw, dict):
        return list(raw.keys())
    return []


@router.get("/me", response_model=MeResponse)
async def me(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> MeResponse:
    try:
        info = await zitadel.get_userinfo(credentials.credentials)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ongeldig of verlopen token",
        ) from exc

    return MeResponse(
        user_id=info.get("sub", ""),
        email=info.get("email", ""),
        name=info.get("name", info.get("preferred_username", "")),
        org_id=info.get("urn:zitadel:iam:user:resourceowner:id"),
        roles=_extract_roles(info),
    )
