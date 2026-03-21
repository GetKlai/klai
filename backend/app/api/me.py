"""
GET /api/me

Validates the OIDC access token forwarded by the frontend and returns
the current user's profile + org info.
"""
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.portal import PortalOrg, PortalUser
from app.services.zitadel import zitadel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["auth"])
bearer = HTTPBearer()


class LanguageUpdate(BaseModel):
    preferred_language: Literal["nl", "en"]


class MessageResponse(BaseModel):
    message: str


class MeResponse(BaseModel):
    user_id: str
    email: str
    name: str
    org_id: str | None = None
    roles: list[str] = []
    workspace_url: str | None = None
    provisioning_status: str = "pending"
    mfa_enrolled: bool = False
    mfa_policy: str = "optional"
    preferred_language: Literal["nl", "en"] = "nl"


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

    # Resolve org + user preferences from portal_users -> portal_orgs
    workspace_url: str | None = None
    provisioning_status: str = "pending"
    mfa_policy: str = "optional"
    preferred_language: Literal["nl", "en"] = "nl"
    if zitadel_user_id:
        result = await db.execute(
            select(PortalOrg, PortalUser)
            .join(PortalUser, PortalUser.org_id == PortalOrg.id)
            .where(PortalUser.zitadel_user_id == zitadel_user_id)
        )
        row = result.one_or_none()
        if row:
            org, portal_user = row
            provisioning_status = org.provisioning_status
            mfa_policy = org.mfa_policy
            preferred_language = portal_user.preferred_language
            if org.slug:
                workspace_url = f"https://{org.slug}.{settings.domain}"

    # Check whether the user has any MFA method enrolled
    mfa_enrolled = False
    if zitadel_user_id:
        try:
            mfa_enrolled = await zitadel.has_any_mfa(zitadel_user_id)
        except Exception:  # noqa: S110 — intentional: MFA check must not block login
            pass

    return MeResponse(
        user_id=zitadel_user_id,
        email=info.get("email", ""),
        name=info.get("name", info.get("preferred_username", "")),
        org_id=info.get("urn:zitadel:iam:user:resourceowner:id"),
        roles=_extract_roles(info),
        workspace_url=workspace_url,
        provisioning_status=provisioning_status,
        mfa_enrolled=mfa_enrolled,
        mfa_policy=mfa_policy,
        preferred_language=preferred_language,
    )


@router.patch("/me/language", response_model=MessageResponse)
async def update_my_language(
    body: LanguageUpdate,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    try:
        info = await zitadel.get_userinfo(credentials.credentials)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Ongeldig token") from exc

    zitadel_user_id = info.get("sub", "")
    if not zitadel_user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Geen gebruiker gevonden")

    result = await db.execute(
        select(PortalUser).where(PortalUser.zitadel_user_id == zitadel_user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gebruiker niet gevonden")

    user.preferred_language = body.preferred_language
    await db.commit()

    # Best-effort sync to Zitadel — don't fail if it doesn't work
    try:
        await zitadel.update_user_language(
            org_id=settings.zitadel_portal_org_id,
            user_id=zitadel_user_id,
            language=body.preferred_language,
        )
    except Exception:
        logger.warning("Could not sync preferred_language to Zitadel for user %s", zitadel_user_id)

    return MessageResponse(message="Taalvoorkeur opgeslagen.")
