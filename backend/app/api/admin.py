"""
Admin user management endpoints.
All endpoints require authentication and resolve the caller's org from their OIDC token.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.portal import PortalOrg, PortalUser
from app.services.zitadel import zitadel

router = APIRouter(prefix="/api/admin", tags=["admin"])
bearer = HTTPBearer()


async def _get_caller_org(
    credentials: HTTPAuthorizationCredentials,
    db: AsyncSession,
) -> tuple[str, PortalOrg]:
    """Validate token, return (zitadel_user_id, PortalOrg)."""
    try:
        info = await zitadel.get_userinfo(credentials.credentials)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Ongeldig token") from exc

    zitadel_org_id = info.get("urn:zitadel:iam:user:resourceowner:id")
    if not zitadel_org_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Geen organisatie gevonden in token")

    result = await db.execute(select(PortalOrg).where(PortalOrg.zitadel_org_id == zitadel_org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisatie niet gevonden")

    return zitadel_org_id, org


class UserOut(BaseModel):
    zitadel_user_id: str
    email: str
    first_name: str
    last_name: str
    created_at: datetime


class UsersResponse(BaseModel):
    users: list[UserOut]


class InviteRequest(BaseModel):
    email: EmailStr
    first_name: str
    last_name: str


class InviteResponse(BaseModel):
    user_id: str
    message: str


class MessageResponse(BaseModel):
    message: str


@router.get("/users", response_model=UsersResponse)
async def list_users(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> UsersResponse:
    _, org = await _get_caller_org(credentials, db)

    result = await db.execute(
        select(PortalUser)
        .where(PortalUser.org_id == org.id)
        .order_by(PortalUser.created_at)
    )
    users = result.scalars().all()

    return UsersResponse(
        users=[
            UserOut(
                zitadel_user_id=u.zitadel_user_id,
                email=u.email,
                first_name=u.first_name,
                last_name=u.last_name,
                created_at=u.created_at,
            )
            for u in users
        ]
    )


@router.post("/users/invite", response_model=InviteResponse, status_code=status.HTTP_201_CREATED)
async def invite_user(
    body: InviteRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> InviteResponse:
    zitadel_org_id, org = await _get_caller_org(credentials, db)

    try:
        user_data = await zitadel.invite_user(
            org_id=zitadel_org_id,
            email=body.email,
            first_name=body.first_name,
            last_name=body.last_name,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Kon gebruiker niet uitnodigen: {exc}",
        ) from exc

    zitadel_user_id: str = user_data["userId"]

    user_row = PortalUser(
        zitadel_user_id=zitadel_user_id,
        org_id=org.id,
        email=body.email,
        first_name=body.first_name,
        last_name=body.last_name,
    )
    db.add(user_row)
    await db.commit()

    return InviteResponse(
        user_id=zitadel_user_id,
        message=f"Uitnodiging verstuurd naar {body.email}.",
    )


@router.delete("/users/{zitadel_user_id}", response_model=MessageResponse)
async def remove_user(
    zitadel_user_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    zitadel_org_id, org = await _get_caller_org(credentials, db)

    # Verify user belongs to this org before deleting
    result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == org.id,
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gebruiker niet gevonden")

    try:
        await zitadel.remove_user(org_id=zitadel_org_id, zitadel_user_id=zitadel_user_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Kon gebruiker niet verwijderen: {exc}",
        ) from exc

    await db.delete(user)
    await db.commit()

    return MessageResponse(message="Gebruiker verwijderd.")
