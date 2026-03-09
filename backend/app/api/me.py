"""
GET /api/me

Validates the OIDC access token forwarded by the frontend and returns
the current user's profile + org info.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.portal import PortalOrg, PortalUser
from app.services.zitadel import zitadel

router = APIRouter(prefix="/api", tags=["auth"])
bearer = HTTPBearer()


class MeResponse(BaseModel):
    user_id: str
    email: str
    name: str
    org_id: str | None = None
    roles: list[str] = []
    workspace_url: str | None = None
    provisioning_status: str = "pending"


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
    db: AsyncSession = Depends(get_db),
) -> MeResponse:
    try:
        info = await zitadel.get_userinfo(credentials.credentials)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ongeldig of verlopen token",
        ) from exc

    zitadel_user_id = info.get("sub", "")

    # Resolve org from portal_users -> portal_orgs
    workspace_url: str | None = None
    provisioning_status: str = "pending"
    if zitadel_user_id:
        result = await db.execute(
            select(PortalOrg)
            .join(PortalUser, PortalUser.org_id == PortalOrg.id)
            .where(PortalUser.zitadel_user_id == zitadel_user_id)
        )
        org = result.scalar_one_or_none()
        if org:
            provisioning_status = org.provisioning_status
            if org.slug:
                workspace_url = f"https://{org.slug}.{settings.domain}"

    return MeResponse(
        user_id=zitadel_user_id,
        email=info.get("email", ""),
        name=info.get("name", info.get("preferred_username", "")),
        org_id=info.get("urn:zitadel:iam:user:resourceowner:id"),
        roles=_extract_roles(info),
        workspace_url=workspace_url,
        provisioning_status=provisioning_status,
    )
